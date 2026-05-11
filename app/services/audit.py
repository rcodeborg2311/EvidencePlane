from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import UUID, uuid4

import httpx

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.db import AuditEvent, EvidencePack, Run, utc_now
from app.models.schemas import AuditEventResponse


def emit_audit_event(
    session: Session,
    *,
    event_type: str,
    actor_identity: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    payload: dict | None = None,
    organization_id: UUID | None = None,
) -> AuditEvent:
    """Add an audit event to the session. Caller is responsible for committing."""
    event = AuditEvent(
        id=uuid4(),
        organization_id=organization_id,
        event_type=event_type,
        actor_identity=actor_identity,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        payload_json=payload,
        created_at=utc_now(),
    )
    session.add(event)
    return event


def list_audit_events(
    session: Session,
    *,
    event_type: str | None = None,
    resource_id: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    limit: int = 100,
) -> list[AuditEventResponse]:
    stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)
    if event_type is not None:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    if resource_id is not None:
        stmt = stmt.where(AuditEvent.resource_id == resource_id)
    if from_date is not None:
        stmt = stmt.where(AuditEvent.created_at >= from_date)
    if to_date is not None:
        stmt = stmt.where(AuditEvent.created_at <= to_date)
    events = session.scalars(stmt).all()
    return [_to_response(e) for e in events]


def flush_pending_to_siem(
    session: Session,
    siem_url: str,
    siem_token: str,
    limit: int = 200,
) -> int:
    """Forward unforwarded audit events to the SIEM webhook. Returns count sent."""
    pending = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.siem_forwarded_at.is_(None))
        .order_by(AuditEvent.created_at)
        .limit(limit)
    ).all()

    forwarded = 0
    for event in pending:
        hec_payload = {
            "time": event.created_at.timestamp(),
            "sourcetype": "evidenceplane:audit",
            "event": {
                "id": str(event.id),
                "event_type": event.event_type,
                "actor_identity": event.actor_identity,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "payload": event.payload_json,
            },
        }
        try:
            httpx.post(
                siem_url,
                json=hec_payload,
                headers={"Authorization": f"Splunk {siem_token}"},
                timeout=5.0,
            )
            event.siem_forwarded_at = utc_now()
            forwarded += 1
        except Exception:
            break  # stop on first failure; retry next flush

    if forwarded:
        session.commit()
    return forwarded


def build_audit_bundle(
    session: Session,
    repo_name: str,
    from_date: str,
    to_date: str,
) -> dict:
    from datetime import date, timezone

    try:
        from_dt = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        to_dt = datetime.fromisoformat(to_date + "T23:59:59").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        from app.errors import EvidencePlaneError
        raise EvidencePlaneError(422, "invalid_date", str(exc)) from exc

    runs = session.scalars(
        select(Run)
        .where(Run.repo_name == repo_name)
        .where(Run.created_at >= from_dt)
        .where(Run.created_at <= to_dt)
        .order_by(Run.created_at)
    ).all()

    run_ids = [str(r.id) for r in runs]

    packs = session.scalars(
        select(EvidencePack).where(EvidencePack.run_id.in_([r.id for r in runs]))
    ).all() if runs else []

    audit_events = session.scalars(
        select(AuditEvent)
        .where(AuditEvent.resource_id.in_(run_ids))
        .order_by(AuditEvent.created_at)
    ).all() if run_ids else []

    bundle_body = {
        "generated_at": utc_now().isoformat(),
        "repo_name": repo_name,
        "from_date": from_date,
        "to_date": to_date,
        "runs_count": len(runs),
        "evidence_packs": [p.body for p in packs],
        "audit_events": [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "actor_identity": e.actor_identity,
                "resource_type": e.resource_type,
                "resource_id": e.resource_id,
                "payload": e.payload_json,
                "created_at": e.created_at.isoformat(),
            }
            for e in audit_events
        ],
    }
    canonical = json.dumps(bundle_body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    bundle_body["bundle_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    return bundle_body


def _to_response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(
        event_id=event.id,
        organization_id=event.organization_id,
        event_type=event.event_type,
        actor_identity=event.actor_identity,
        resource_type=event.resource_type,
        resource_id=event.resource_id,
        payload=event.payload_json,
        siem_forwarded_at=event.siem_forwarded_at,
        created_at=event.created_at,
    )
