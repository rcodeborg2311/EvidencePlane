from __future__ import annotations

import hashlib
import json
from datetime import timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.errors import EvidencePlaneError
from app.models.db import Case, CaseEvent, CaseExternalLink, CaseMessage, CaseParticipant, utc_now
from app.models.schemas import (
    CaseEventResponse,
    CaseExternalLinkRequest,
    CaseExternalLinkResponse,
    CaseMessageRequest,
    CaseMessageResponse,
    CaseResolveRequest,
    CaseResponse,
    CaseSummary,
)
from app.services.evidence import canonical_json


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _case_options(stmt):
    return stmt.options(
        selectinload(Case.messages),
        selectinload(Case.events),
        selectinload(Case.external_links),
        selectinload(Case.participants),
    )


def _case_event_sha256(event_body: dict) -> str:
    body = {k: v for k, v in event_body.items() if k != "event_sha256"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def _message_sha256(case_id: str, body: str, created_at: str) -> str:
    payload = json.dumps({"case_id": case_id, "body": body, "created_at": created_at}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _next_sequence(case: Case) -> int:
    return len(case.events) + 1


def _prev_sha(case: Case) -> str | None:
    if not case.events:
        return None
    return max(case.events, key=lambda e: e.sequence).event_sha256


def _append_case_event(
    session: Session,
    case: Case,
    *,
    event_type: str,
    actor_identity: str,
    payload: dict | None = None,
) -> CaseEvent:
    sequence = _next_sequence(case)
    prev_sha = _prev_sha(case)
    now = utc_now()
    body = {
        "case_id": str(case.id),
        "event_type": event_type,
        "actor_identity": actor_identity,
        "payload": payload,
        "sequence": sequence,
        "previous_event_sha256": prev_sha,
        "created_at": now.astimezone(timezone.utc).isoformat(),
    }
    sha = _case_event_sha256(body)
    event = CaseEvent(
        id=uuid4(),
        case_id=case.id,
        event_type=event_type,
        actor_identity=actor_identity,
        payload_json=payload,
        sequence=sequence,
        previous_event_sha256=prev_sha,
        event_sha256=sha,
        created_at=now,
    )
    session.add(event)
    case.events.append(event)
    return event


def _severity_from_violations(violations: list[dict]) -> str:
    severities = {v.get("severity") for v in violations}
    if "high" in severities:
        return "high"
    if "medium" in severities:
        return "medium"
    return "low"


def _to_case_response(case: Case) -> CaseResponse:
    return CaseResponse(
        case_id=case.id,
        run_id=case.run_id,
        status=case.status,
        case_type=case.case_type,
        severity=case.severity,
        title=case.title,
        summary=case.summary,
        resolved_by=case.resolved_by,
        resolution_note=case.resolution_note,
        created_at=case.created_at,
        resolved_at=case.resolved_at,
        messages=[
            CaseMessageResponse(
                message_id=m.id,
                author_identity=m.author_identity,
                body=m.body,
                message_sha256=m.message_sha256,
                created_at=m.created_at,
            )
            for m in case.messages
        ],
        events=[
            CaseEventResponse(
                event_id=e.id,
                event_type=e.event_type,
                actor_identity=e.actor_identity,
                payload=e.payload_json,
                sequence=e.sequence,
                previous_event_sha256=e.previous_event_sha256,
                event_sha256=e.event_sha256,
                created_at=e.created_at,
            )
            for e in case.events
        ],
        external_links=[
            CaseExternalLinkResponse(
                link_id=lnk.id,
                provider=lnk.provider,
                external_id=lnk.external_id,
                url=lnk.url,
                link_type=lnk.link_type,
                created_by=lnk.created_by,
                created_at=lnk.created_at,
            )
            for lnk in case.external_links
        ],
    )


def _to_case_summary(case: Case) -> CaseSummary:
    return CaseSummary(
        case_id=case.id,
        run_id=case.run_id,
        status=case.status,
        case_type=case.case_type,
        severity=case.severity,
        title=case.title,
        created_at=case.created_at,
        resolved_at=case.resolved_at,
    )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def auto_create_case(
    session: Session,
    *,
    run_id: UUID,
    decision: str,
    repo_name: str,
    branch: str,
    violations: list[dict],
    organization_id: UUID | None = None,
) -> Case | None:
    """Create a case for review/block decisions. Returns None for allow."""
    if decision == "allow":
        return None

    case_type = "review_required" if decision == "review" else "blocked_change"
    severity = _severity_from_violations(violations) if violations else ("high" if decision == "block" else "medium")

    violation_codes = ", ".join(v.get("code", "") for v in violations) if violations else "no violations"
    title = f"{decision.upper()}: {violation_codes} — {repo_name}@{branch}"[:500]

    now = utc_now()
    case = Case(
        id=uuid4(),
        organization_id=organization_id,
        run_id=run_id,
        status="open",
        case_type=case_type,
        severity=severity,
        title=title,
        summary=None,
        created_at=now,
    )
    session.add(case)
    session.flush()

    _append_case_event(
        session,
        case,
        event_type="created",
        actor_identity="system",
        payload={"decision": decision, "violation_codes": [v.get("code") for v in violations]},
    )
    session.commit()
    return case


def get_case(session: Session, case_id: UUID) -> CaseResponse:
    case = session.scalar(_case_options(select(Case).where(Case.id == case_id)))
    if case is None:
        raise EvidencePlaneError(404, "not_found", "Case not found.")
    return _to_case_response(case)


def get_case_for_run(session: Session, run_id: UUID) -> CaseResponse | None:
    case = session.scalar(
        _case_options(select(Case).where(Case.run_id == run_id))
    )
    if case is None:
        return None
    return _to_case_response(case)


def list_cases(
    session: Session,
    *,
    status: str | None = None,
    severity: str | None = None,
    case_type: str | None = None,
    limit: int = 50,
) -> list[CaseSummary]:
    stmt = select(Case).order_by(Case.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Case.status == status)
    if severity is not None:
        stmt = stmt.where(Case.severity == severity)
    if case_type is not None:
        stmt = stmt.where(Case.case_type == case_type)
    cases = session.scalars(stmt).all()
    return [_to_case_summary(c) for c in cases]


def resolve_case(
    session: Session,
    case_id: UUID,
    request: CaseResolveRequest,
) -> CaseResponse:
    case = session.scalar(_case_options(select(Case).where(Case.id == case_id)))
    if case is None:
        raise EvidencePlaneError(404, "not_found", "Case not found.")
    if case.status != "open":
        raise EvidencePlaneError(409, "case_not_open", f"Case is already {case.status}.")

    now = utc_now()
    case.status = "resolved"
    case.resolved_by = request.resolved_by
    case.resolution_note = request.resolution_note
    case.resolved_at = now

    _append_case_event(
        session,
        case,
        event_type="resolved",
        actor_identity=request.resolved_by,
        payload={"resolution_note": request.resolution_note},
    )
    from app.services.audit import emit_audit_event
    emit_audit_event(
        session,
        event_type="case_resolved",
        actor_identity=request.resolved_by,
        resource_type="case",
        resource_id=str(case_id),
        payload={"resolution_note": request.resolution_note},
    )
    session.commit()
    session.refresh(case)
    return _to_case_response(case)


def dismiss_case(
    session: Session,
    case_id: UUID,
    actor_identity: str,
    note: str | None = None,
) -> CaseResponse:
    case = session.scalar(_case_options(select(Case).where(Case.id == case_id)))
    if case is None:
        raise EvidencePlaneError(404, "not_found", "Case not found.")
    if case.status != "open":
        raise EvidencePlaneError(409, "case_not_open", f"Case is already {case.status}.")

    case.status = "dismissed"
    case.resolved_by = actor_identity
    case.resolution_note = note
    case.resolved_at = utc_now()

    _append_case_event(
        session,
        case,
        event_type="dismissed",
        actor_identity=actor_identity,
        payload={"note": note},
    )
    from app.services.audit import emit_audit_event
    emit_audit_event(
        session,
        event_type="case_dismissed",
        actor_identity=actor_identity,
        resource_type="case",
        resource_id=str(case_id),
        payload={"note": note},
    )
    session.commit()
    session.refresh(case)
    return _to_case_response(case)


def add_message(
    session: Session,
    case_id: UUID,
    request: CaseMessageRequest,
) -> CaseResponse:
    case = session.scalar(_case_options(select(Case).where(Case.id == case_id)))
    if case is None:
        raise EvidencePlaneError(404, "not_found", "Case not found.")

    now = utc_now()
    now_str = now.astimezone(timezone.utc).isoformat()
    sha = _message_sha256(str(case_id), request.body, now_str)

    msg = CaseMessage(
        id=uuid4(),
        case_id=case_id,
        author_identity=request.author_identity,
        body=request.body,
        message_sha256=sha,
        created_at=now,
    )
    session.add(msg)
    case.messages.append(msg)

    _append_case_event(
        session,
        case,
        event_type="message_added",
        actor_identity=request.author_identity,
        payload={"message_sha256": sha},
    )
    session.commit()
    session.refresh(case)
    return _to_case_response(case)


def add_external_link(
    session: Session,
    case_id: UUID,
    request: CaseExternalLinkRequest,
    actor_identity: str,
) -> CaseResponse:
    case = session.scalar(_case_options(select(Case).where(Case.id == case_id)))
    if case is None:
        raise EvidencePlaneError(404, "not_found", "Case not found.")

    link = CaseExternalLink(
        id=uuid4(),
        case_id=case_id,
        provider=request.provider,
        external_id=request.external_id,
        url=request.url,
        link_type=request.link_type,
        created_by=request.created_by or actor_identity,
        created_at=utc_now(),
    )
    session.add(link)
    case.external_links.append(link)

    _append_case_event(
        session,
        case,
        event_type="link_added",
        actor_identity=actor_identity,
        payload={"provider": request.provider, "url": request.url, "link_type": request.link_type},
    )
    session.commit()
    session.refresh(case)
    return _to_case_response(case)
