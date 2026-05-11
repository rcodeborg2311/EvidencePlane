from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import EvidencePlaneError
from app.models.db import Organization, PolicyConfig, Run, utc_now
from app.models.schemas import (
    DryRunChange,
    DryRunRequest,
    DryRunResponse,
    PolicyConfigRequest,
    PolicyConfigResponse,
)
from app.services.policy import PolicySettings, evaluate_policy
from app.models.schemas import RunReceipt, ChangedFile, TestResult, ToolCall, PolicyContext


def _next_version(session: Session, org_id, repo_name: str | None) -> int:
    existing = session.scalars(
        select(PolicyConfig).where(
            PolicyConfig.organization_id == org_id,
            PolicyConfig.repo_name == repo_name,
        )
    ).all()
    return max((c.version for c in existing), default=0) + 1


def create_policy_config(
    session: Session,
    request: PolicyConfigRequest,
    *,
    created_by_id=None,
) -> PolicyConfigResponse:
    org = session.scalar(select(Organization).limit(1))
    if org is None:
        raise EvidencePlaneError(500, "no_organization", "No organization found.")

    version = _next_version(session, org.id, request.repo_name)
    config = PolicyConfig(
        id=uuid4(),
        organization_id=org.id,
        repo_name=request.repo_name,
        version=version,
        is_active=True,
        config_json=request.config.model_dump(),
        created_by=created_by_id,
        created_at=utc_now(),
    )
    session.add(config)
    from app.services.audit import emit_audit_event
    emit_audit_event(
        session,
        event_type="policy_changed",
        resource_type="policy_config",
        resource_id=str(config.id),
        payload={"repo_name": config.repo_name, "version": config.version},
        organization_id=org.id,
    )
    session.commit()
    session.refresh(config)
    return PolicyConfigResponse(
        config_id=config.id,
        repo_name=config.repo_name,
        version=config.version,
        is_active=config.is_active,
        config=config.config_json,
        created_at=config.created_at,
    )


def list_policy_configs(
    session: Session, repo_name: str | None = None
) -> list[PolicyConfigResponse]:
    stmt = select(PolicyConfig).order_by(PolicyConfig.created_at.desc())
    if repo_name is not None:
        stmt = stmt.where(PolicyConfig.repo_name == repo_name)
    configs = session.scalars(stmt).all()
    return [
        PolicyConfigResponse(
            config_id=c.id,
            repo_name=c.repo_name,
            version=c.version,
            is_active=c.is_active,
            config=c.config_json,
            created_at=c.created_at,
        )
        for c in configs
    ]


def get_policy_config(session: Session, config_id) -> PolicyConfigResponse:
    config = session.get(PolicyConfig, config_id)
    if config is None:
        raise EvidencePlaneError(404, "not_found", "Policy config not found.")
    return PolicyConfigResponse(
        config_id=config.id,
        repo_name=config.repo_name,
        version=config.version,
        is_active=config.is_active,
        config=config.config_json,
        created_at=config.created_at,
    )


def deactivate_policy_config(session: Session, config_id) -> None:
    config = session.get(PolicyConfig, config_id)
    if config is None:
        raise EvidencePlaneError(404, "not_found", "Policy config not found.")
    config.is_active = False
    session.commit()


def dry_run_policy_config(
    session: Session, request: DryRunRequest
) -> DryRunResponse:
    settings = PolicySettings.from_dict(request.config.model_dump())

    runs = session.scalars(
        select(Run)
        .where(Run.repo_name == request.repo_name)
        .order_by(Run.created_at.desc())
        .limit(request.limit)
    ).all()

    changes: list[DryRunChange] = []
    for run in runs:
        receipt = _receipt_from_run(run)
        new_decision_obj = evaluate_policy(receipt, settings)
        new_decision = new_decision_obj.decision
        old_decision = run.decision

        old_codes = {v["code"] for v in (run.normalized_input.get("violations") or [])}
        new_codes = {v.code for v in new_decision_obj.violations}

        if new_decision != old_decision:
            changes.append(
                DryRunChange(
                    run_id=run.id,
                    repo_name=run.repo_name,
                    old_decision=old_decision,
                    new_decision=new_decision,
                    violations_added=sorted(new_codes - old_codes),
                    violations_removed=sorted(old_codes - new_codes),
                )
            )

    return DryRunResponse(
        repo_name=request.repo_name,
        total_runs=len(runs),
        would_change=len(changes),
        changes=changes,
    )


def _receipt_from_run(run: Run) -> RunReceipt:
    from datetime import timezone as _tz
    data = run.normalized_input
    ts = run.timestamp_utc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_tz.utc)
    return RunReceipt(
        idempotency_key=run.idempotency_key,
        repo_name=run.repo_name,
        commit_sha=run.commit_sha,
        branch=run.branch,
        actor=run.actor,
        timestamp_utc=ts,
        changed_files=[ChangedFile(**f) for f in (data.get("changed_files") or [])],
        tests=[TestResult(**t) for t in (data.get("tests") or [])],
        tool_calls=[ToolCall(**tc) for tc in (data.get("tool_calls") or [])],
        policy_context=PolicyContext(**(data.get("policy_context") or {})),
    )
