from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.orm import Session, selectinload

from app.errors import EvidencePlaneError
from app.models.db import EvidencePack, Run, ViolationRecord, utc_now
from app.models.schemas import (
    DecisionResponse,
    RunDetail,
    RunReceipt,
    RunSummary,
    Violation,
)
from app.services.evidence import (
    build_evidence_pack,
    compute_evidence_sha256,
    normalized_input,
)
from app.services.policy import evaluate_policy
from app.services.security import sha256_hex


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _run_options(statement: Select[tuple[Run]]) -> Select[tuple[Run]]:
    return statement.options(selectinload(Run.violations), selectinload(Run.evidence_pack))


def _decision_response_from_run(run: Run) -> DecisionResponse:
    return DecisionResponse(
        run_id=run.id,
        decision=run.decision,
        risk_score=run.risk_score,
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
        risk_score=run.risk_score,
        repo_name=run.repo_name,
        actor=run.actor,
        timestamp_utc=_as_utc(run.timestamp_utc),
        created_at=_as_utc(run.created_at),
    )


def _detail_from_run(run: Run) -> RunDetail:
    return RunDetail(
        **_summary_from_run(run).model_dump(),
        idempotency_key=run.idempotency_key,
        commit_sha=run.commit_sha,
        branch=run.branch,
        changed_files=run.changed_files,
        tests=run.tests,
        tool_calls=run.tool_calls,
        policy_context=run.policy_context,
        violations=[
            Violation(code=item.code, message=item.message, severity=item.severity)
            for item in run.violations
        ],
        evidence_pack_id=run.evidence_pack_id,
        evidence_sha256=run.evidence_sha256,
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

    policy_decision = evaluate_policy(receipt)
    run_id = uuid4()
    evidence_pack_id = uuid4()
    created_at = utc_now()
    normalized = normalized_input(receipt)
    evidence_body = build_evidence_pack(
        evidence_pack_id=evidence_pack_id,
        run_id=run_id,
        receipt=receipt,
        decision=policy_decision.decision,
        risk_score=policy_decision.risk_score,
        violations=policy_decision.violations,
        generated_at=created_at,
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
        risk_score=policy_decision.risk_score,
        evidence_pack_id=evidence_pack_id,
        evidence_sha256=evidence_sha256,
        created_at=created_at,
        normalized_input=normalized,
        changed_files=normalized["changed_files"],
        tests=normalized["tests"],
        tool_calls=normalized["tool_calls"],
        policy_context=normalized["policy_context"],
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


def get_evidence(session: Session, run_id: UUID) -> dict:
    evidence_pack = session.scalar(
        select(EvidencePack).where(EvidencePack.run_id == run_id)
    )
    if evidence_pack is None:
        raise EvidencePlaneError(404, "not_found", "Evidence pack was not found.")

    body = deepcopy(evidence_pack.body)
    computed = compute_evidence_sha256(body)
    if computed != evidence_pack.sha256 or body.get("evidence_sha256") != evidence_pack.sha256:
        raise EvidencePlaneError(
            503,
            "service_unavailable",
            "Evidence integrity check failed.",
        )
    return body
