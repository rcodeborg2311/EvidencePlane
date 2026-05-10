from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.errors import EvidencePlaneError
from app.models.db import EvidencePack, EvidenceSource, PolicyConfig, ReviewEvent, Run, ViolationRecord, utc_now
from app.models.schemas import (
    DecisionResponse,
    ReviewEventSummary,
    RunDetail,
    RunReceipt,
    RunSummary,
    Violation,
)
from app.services.evidence import (
    build_evidence_pack,
    build_review_event,
    compute_evidence_sha256,
    compute_review_event_sha256,
    format_datetime,
    normalized_input,
)
from app.services.policy import POLICY_VERSION, PolicySettings, evaluate_policy
from app.services.security import sha256_hex


def _find_active_policy_config(session: Session, repo_name: str) -> PolicyConfig | None:
    return session.scalar(
        select(PolicyConfig)
        .where(PolicyConfig.repo_name == repo_name, PolicyConfig.is_active.is_(True))
        .order_by(PolicyConfig.created_at.desc())
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_options(statement: Select[tuple[Run]]) -> Select[tuple[Run]]:
    return statement.options(
        selectinload(Run.violations),
        selectinload(Run.evidence_pack),
        selectinload(Run.review_events),
    )


def initial_review_status(decision: str) -> str:
    return "pending" if decision == "review" else "not_required"


def _review_fields_from_run(run: Run) -> dict:
    return {
        "review_status": run.review_status,
        "review_outcome": run.review_outcome,
        "reviewer_identity": run.reviewer_identity,
        "review_note": run.review_note,
        "reviewed_at": _as_utc(run.reviewed_at) if run.reviewed_at else None,
    }


def _decision_response_from_run(run: Run) -> DecisionResponse:
    return DecisionResponse(
        run_id=run.id,
        decision=run.decision,
        policy_version=run.policy_version,
        risk_score=run.risk_score,
        **_review_fields_from_run(run),
        violations=[
            Violation(code=item.code, message=item.message, severity=item.severity)
            for item in run.violations
        ],
        evidence_pack_id=run.evidence_pack_id,
        evidence_sha256=run.evidence_sha256,
        created_at=_as_utc(run.created_at),
    )


def _summary_from_run(run: Run) -> RunSummary:
    return RunSummary(
        run_id=run.id,
        decision=run.decision,
        policy_version=run.policy_version,
        risk_score=run.risk_score,
        **_review_fields_from_run(run),
        repo_name=run.repo_name,
        branch=run.branch,
        actor=run.actor,
        timestamp_utc=_as_utc(run.timestamp_utc),
        evidence_sha256=run.evidence_sha256,
        created_at=_as_utc(run.created_at),
    )


def _review_event_body_from_record(event: ReviewEvent) -> dict:
    body = {
        "event_id": str(event.id),
        "run_id": str(event.run_id),
        "sequence": event.sequence,
        "action": event.action,
        "actor": event.actor,
        "reason": event.reason,
        "created_at": format_datetime(_as_utc(event.created_at)),
        "previous_event_sha256": event.previous_event_sha256,
        "event_sha256": event.event_sha256,
    }
    if compute_review_event_sha256(body) != event.event_sha256:
        raise EvidencePlaneError(
            503,
            "service_unavailable",
            "Review event integrity check failed.",
        )
    return body


def _review_event_bodies_from_records(events: list[ReviewEvent]) -> list[dict]:
    previous_event_sha256 = None
    bodies = []
    for expected_sequence, event in enumerate(events, start=1):
        body = _review_event_body_from_record(event)
        if (
            body["sequence"] != expected_sequence
            or body["previous_event_sha256"] != previous_event_sha256
        ):
            raise EvidencePlaneError(
                503,
                "service_unavailable",
                "Review event chain integrity check failed.",
            )
        previous_event_sha256 = body["event_sha256"]
        bodies.append(body)
    return bodies


def _review_event_summary_from_record(event: ReviewEvent) -> ReviewEventSummary:
    return ReviewEventSummary(
        event_id=event.id,
        sequence=event.sequence,
        action=event.action,
        actor=event.actor,
        reason=event.reason,
        created_at=_as_utc(event.created_at),
        previous_event_sha256=event.previous_event_sha256,
        event_sha256=event.event_sha256,
    )


def _detail_from_run(run: Run) -> RunDetail:
    _review_event_bodies_from_records(run.review_events)
    return RunDetail(
        **_summary_from_run(run).model_dump(),
        idempotency_key=run.idempotency_key,
        commit_sha=run.commit_sha,
        changed_files=run.changed_files,
        tests=run.tests,
        tool_calls=run.tool_calls,
        policy_context=run.policy_context,
        violations=[
            Violation(code=item.code, message=item.message, severity=item.severity)
            for item in run.violations
        ],
        evidence_pack_id=run.evidence_pack_id,
        review_events=[
            _review_event_summary_from_record(event) for event in run.review_events
        ],
    )


def store_run(session: Session, receipt: RunReceipt, raw_body: bytes) -> DecisionResponse:
    payload_sha256 = sha256_hex(raw_body)
    existing = session.scalar(
        _run_options(select(Run).where(Run.idempotency_key == receipt.idempotency_key))
    )
    if existing is not None:
        if existing.payload_sha256 != payload_sha256:
            raise EvidencePlaneError(
                409,
                "idempotency_conflict",
                "idempotency_key already exists with a different payload.",
            )
        return _decision_response_from_run(existing)

    active_config = _find_active_policy_config(session, receipt.repo_name)
    settings = PolicySettings.from_dict(active_config.config_json) if active_config else PolicySettings()
    policy_config_id = str(active_config.id) if active_config else None
    policy_config_snapshot = active_config.config_json if active_config else None

    # Resolve evidence source from v2 source_id field
    evidence_source: EvidenceSource | None = None
    if receipt.source_id:
        try:
            from uuid import UUID as _UUID
            source_uuid = _UUID(receipt.source_id)
            evidence_source = session.get(EvidenceSource, source_uuid)
            if evidence_source and not evidence_source.enabled:
                evidence_source = None
        except (ValueError, AttributeError):
            pass
    if evidence_source is not None:
        evidence_source.last_seen_at = utc_now()

    policy_decision = evaluate_policy(receipt, settings)
    review_status = initial_review_status(policy_decision.decision)
    run_id = uuid4()
    evidence_pack_id = uuid4()
    created_at = utc_now()
    normalized = normalized_input(receipt)
    evidence_body = build_evidence_pack(
        evidence_pack_id=evidence_pack_id,
        run_id=run_id,
        receipt=receipt,
        decision=policy_decision.decision,
        policy_version=POLICY_VERSION,
        risk_score=policy_decision.risk_score,
        review_status=review_status,
        review_outcome=None,
        reviewer_identity=None,
        review_note=None,
        reviewed_at=None,
        violations=policy_decision.violations,
        generated_at=created_at,
        policy_config_id=policy_config_id,
        policy_config_snapshot=policy_config_snapshot,
    )
    evidence_sha256 = evidence_body["evidence_sha256"]

    run = Run(
        id=run_id,
        idempotency_key=receipt.idempotency_key,
        payload_sha256=payload_sha256,
        repo_name=receipt.repo_name,
        commit_sha=receipt.commit_sha,
        branch=receipt.branch,
        actor=receipt.actor,
        timestamp_utc=receipt.timestamp_utc,
        decision=policy_decision.decision,
        policy_version=POLICY_VERSION,
        risk_score=policy_decision.risk_score,
        review_status=review_status,
        review_outcome=None,
        reviewer_identity=None,
        review_note=None,
        reviewed_at=None,
        evidence_pack_id=evidence_pack_id,
        evidence_sha256=evidence_sha256,
        created_at=created_at,
        normalized_input=normalized,
        changed_files=normalized["changed_files"],
        tests=normalized["tests"],
        tool_calls=normalized["tool_calls"],
        policy_context=normalized["policy_context"],
        schema_version=receipt.schema_version,
        source_run_url=receipt.source_run_url,
        pull_request_json=receipt.pull_request.model_dump(mode="json") if receipt.pull_request else None,
        scanner_results_json=[r.model_dump(mode="json") for r in receipt.scanner_results] or None,
        artifact_refs_json=[a.model_dump(mode="json") for a in receipt.artifact_refs] or None,
        evidence_source_id=evidence_source.id if evidence_source else None,
    )
    run.violations = [
        ViolationRecord(
            run_id=run_id,
            code=violation.code,
            message=violation.message,
            severity=violation.severity,
            sort_order=index,
        )
        for index, violation in enumerate(policy_decision.violations)
    ]
    run.evidence_pack = EvidencePack(
        id=evidence_pack_id,
        run_id=run_id,
        body=evidence_body,
        sha256=evidence_sha256,
        created_at=created_at,
    )

    session.add(run)
    session.commit()

    # Auto-create a Case Room for review/block decisions
    from app.services.case_rooms import auto_create_case
    auto_create_case(
        session,
        run_id=run_id,
        decision=policy_decision.decision,
        repo_name=receipt.repo_name,
        branch=receipt.branch,
        violations=[v.model_dump(mode="json") for v in policy_decision.violations],
    )

    saved = session.scalar(_run_options(select(Run).where(Run.id == run_id)))
    if saved is None:
        raise EvidencePlaneError(503, "service_unavailable", "Run could not be persisted.")
    return _decision_response_from_run(saved)


def latest_runs(session: Session, limit: int = 50) -> list[RunSummary]:
    runs = session.scalars(
        select(Run).order_by(Run.created_at.desc()).limit(limit)
    ).all()
    return [_summary_from_run(run) for run in runs]


def get_run_detail(session: Session, run_id: UUID) -> RunDetail:
    run = session.scalar(_run_options(select(Run).where(Run.id == run_id)))
    if run is None:
        raise EvidencePlaneError(404, "not_found", "Run was not found.")
    return _detail_from_run(run)


def _sync_evidence_review_state(run: Run) -> bool:
    if run.evidence_pack is None:
        raise EvidencePlaneError(
            503,
            "service_unavailable",
            "Evidence pack was not found for review update.",
        )

    body = deepcopy(run.evidence_pack.body)
    body["review_status"] = run.review_status
    body["review_outcome"] = run.review_outcome
    body["reviewer_identity"] = run.reviewer_identity
    body["review_note"] = run.review_note
    body["reviewed_at"] = (
        format_datetime(_as_utc(run.reviewed_at)) if run.reviewed_at else None
    )
    body["review_events"] = _review_event_bodies_from_records(run.review_events)
    body["evidence_sha256"] = compute_evidence_sha256(body)
    if body == run.evidence_pack.body and body["evidence_sha256"] == run.evidence_sha256:
        return False
    run.evidence_pack.body = body
    run.evidence_pack.sha256 = body["evidence_sha256"]
    run.evidence_sha256 = body["evidence_sha256"]
    return True


def review_run(
    session: Session,
    run_id: UUID,
    *,
    outcome: str,
    reviewer_identity: str,
    review_note: str | None,
) -> RunDetail:
    run = session.scalar(_run_options(select(Run).where(Run.id == run_id)))
    if run is None:
        raise EvidencePlaneError(404, "run_not_found", "Run was not found.")
    if run.review_status != "pending":
        raise EvidencePlaneError(
            409,
            "review_not_pending",
            "Run does not have a pending review.",
        )

    reviewed_at = utc_now()
    last_event = run.review_events[-1] if run.review_events else None
    event_id = uuid4()
    sequence = (last_event.sequence if last_event else 0) + 1
    event_body = build_review_event(
        event_id=event_id,
        run_id=run.id,
        sequence=sequence,
        action=outcome,
        actor=reviewer_identity,
        reason=review_note,
        created_at=reviewed_at,
        previous_event_sha256=last_event.event_sha256 if last_event else None,
    )
    run.review_events.append(
        ReviewEvent(
            id=event_id,
            run_id=run.id,
            sequence=sequence,
            action=outcome,
            actor=reviewer_identity,
            reason=review_note,
            created_at=reviewed_at,
            previous_event_sha256=last_event.event_sha256 if last_event else None,
            event_sha256=event_body["event_sha256"],
        )
    )
    run.review_status = outcome
    run.review_outcome = outcome
    run.reviewer_identity = reviewer_identity
    run.review_note = review_note
    run.reviewed_at = reviewed_at
    _sync_evidence_review_state(run)
    session.commit()

    saved = session.scalar(_run_options(select(Run).where(Run.id == run_id)))
    if saved is None:
        raise EvidencePlaneError(503, "service_unavailable", "Run could not be loaded.")
    return _detail_from_run(saved)


def get_evidence(session: Session, run_id: UUID) -> dict:
    run = session.scalar(_run_options(select(Run).where(Run.id == run_id)))
    if run is None or run.evidence_pack is None:
        raise EvidencePlaneError(404, "not_found", "Evidence pack was not found.")

    body = deepcopy(run.evidence_pack.body)
    computed = compute_evidence_sha256(body)
    if (
        computed != run.evidence_pack.sha256
        or body.get("evidence_sha256") != run.evidence_pack.sha256
    ):
        raise EvidencePlaneError(
            503,
            "service_unavailable",
            "Evidence integrity check failed.",
        )

    changed = _sync_evidence_review_state(run)
    if changed:
        session.commit()
        session.refresh(run.evidence_pack)

    body = deepcopy(run.evidence_pack.body)
    computed = compute_evidence_sha256(body)
    if (
        computed != run.evidence_pack.sha256
        or body.get("evidence_sha256") != run.evidence_pack.sha256
    ):
        raise EvidencePlaneError(
            503,
            "service_unavailable",
            "Evidence integrity check failed.",
        )

    # Append case summary outside the sealed hash — advisory context only
    from app.models.db import Case as _Case
    case = session.scalar(select(_Case).where(_Case.run_id == run_id))
    if case is not None:
        body["case"] = {
            "case_id": str(case.id),
            "status": case.status,
            "case_type": case.case_type,
            "severity": case.severity,
            "title": case.title,
            "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        }
    else:
        body["case"] = None

    return body
