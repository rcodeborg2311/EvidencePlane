from __future__ import annotations

from datetime import datetime
import hmac
import json
from pathlib import Path
import time
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_session
from app.errors import EvidencePlaneError
from app.models.db import Organization, UserSession, ROLES, role_meets
from app.models.schemas import (
    AdvisorFindingResponse,
    CaseExternalLinkRequest,
    CaseMessageRequest,
    CaseResponse,
    CaseResolveRequest,
    CaseSummary,
    DecisionResponse,
    DryRunRequest,
    DryRunResponse,
    EvidenceSourceRequest,
    EvidenceSourceResponse,
    PolicyConfigRequest,
    PolicyConfigResponse,
    ReviewRequest,
    RunDetail,
    RunReceipt,
    RunSummary,
    StrictModel,
)
from app.observability import log_event, record_policy_decision
from app.services.auth import (
    check_role,
    issue_session,
    provision_user,
    resolve_session,
    revoke_session,
)
from app.services.github import (
    handle_installation_event,
    handle_pull_request_event,
    mark_delivery_processed,
    store_delivery,
    update_check_run_for_run,
    validate_github_signature,
)
from app.services.advisor import list_advisor_findings, request_advisor_finding
from app.services.case_rooms import (
    add_external_link,
    add_message,
    dismiss_case,
    get_case,
    get_case_for_run,
    list_cases,
    resolve_case,
)
from app.services.evidence_sources import (
    create_evidence_source,
    disable_evidence_source,
    get_evidence_source,
    list_evidence_sources,
)
from app.services.policy_config import (
    create_policy_config,
    deactivate_policy_config,
    dry_run_policy_config,
    get_policy_config,
    list_policy_configs,
)
from app.services.runs import (
    get_evidence,
    get_run_detail,
    latest_runs,
    review_run,
    store_run,
)
from app.services.security import SIGNATURE_HEADER, signature_is_valid

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


def iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


templates.env.filters["iso_z"] = iso_z


def dashboard_metrics(runs: list[RunSummary]) -> dict[str, int | float | None]:
    total = len(runs)
    return {
        "total": total,
        "allow": sum(1 for run in runs if run.decision == "allow"),
        "review": sum(1 for run in runs if run.decision == "review"),
        "block": sum(1 for run in runs if run.decision == "block"),
        "pending_reviews": sum(1 for run in runs if run.review_status == "pending"),
        "average_risk": round(sum(run.risk_score for run in runs) / total, 1)
        if total
        else None,
    }


async def require_valid_signature(
    request: Request, settings: Settings = Depends(get_settings)
) -> bytes:
    body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)
    if not signature_is_valid(signature, body, settings.hmac_secret):
        raise EvidencePlaneError(401, "invalid_signature", "Invalid request signature.")
    return body


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        token = header.removeprefix("Bearer ").strip()
        return token if token else None
    return None


def _is_break_glass(token: str, settings: Settings) -> bool:
    return bool(token) and hmac.compare_digest(token, settings.admin_token)


def require_reviewer(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
) -> UserSession | None:
    """Require at least reviewer role. Falls back to break-glass ADMIN_TOKEN."""
    token = _extract_bearer(request)
    if token is None:
        raise EvidencePlaneError(401, "auth_required", "Bearer token is required.")
    if _is_break_glass(token, settings):
        return None  # break-glass; caller treats None as superuser
    user_session = resolve_session(db, token)
    check_role(user_session, "reviewer")
    return user_session


def require_admin(
    request: Request,
    settings: Settings = Depends(get_settings),
    db: Session = Depends(get_session),
) -> UserSession | None:
    """Require at least admin role. Falls back to break-glass ADMIN_TOKEN."""
    token = _extract_bearer(request)
    if token is None:
        raise EvidencePlaneError(401, "auth_required", "Bearer token is required.")
    if _is_break_glass(token, settings):
        return None
    user_session = resolve_session(db, token)
    check_role(user_session, "admin")
    return user_session


# --------------------------------------------------------------------------- #
# Auth request/response schemas
# --------------------------------------------------------------------------- #

class ProvisionUserRequest(StrictModel):
    email: str = Field(min_length=1, max_length=254)
    display_name: str | None = Field(default=None, max_length=200)
    role: str = Field(default="reviewer")

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError(f"role must be one of: {', '.join(ROLES)}")
        return value


class ProvisionUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: str
    email: str
    role: str
    token: str


class IssueSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    session_id: str
    role: str
    token: str


async def parse_review_request(request: Request) -> ReviewRequest:
    content_type = request.headers.get("Content-Type", "")
    try:
        if "application/json" in content_type:
            data = await request.json()
        else:
            form_data = parse_qs((await request.body()).decode("utf-8"))
            data = {
                key: values[-1] if values else ""
                for key, values in form_data.items()
                if key != "admin_token"
            }
        return ReviewRequest.model_validate(data)
    except (UnicodeDecodeError, ValueError, ValidationError):
        raise EvidencePlaneError(
            422,
            "validation_error",
            "Request body failed validation.",
        )


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/api/v1/runs", response_model=DecisionResponse)
async def post_run(
    request: Request,
    receipt: RunReceipt,
    raw_body: bytes = Depends(require_valid_signature),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DecisionResponse:
    started = time.perf_counter()
    response = store_run(session, receipt, raw_body)
    decision_counts = record_policy_decision(response.decision)
    log_event(
        "ingest_completed",
        request_id=getattr(request.state, "request_id", None),
        run_id=str(response.run_id),
        decision=response.decision,
        risk_score=response.risk_score,
        duration_ms=round((time.perf_counter() - started) * 1000, 2),
        policy_decision_counts=decision_counts,
    )
    if settings.github_app_id:
        base_url = str(request.base_url).rstrip("/")
        await update_check_run_for_run(
            session,
            repo_full_name=receipt.repo_name,
            head_sha=receipt.commit_sha,
            decision=response.decision,
            run_id=str(response.run_id),
            violations=[v.model_dump() for v in response.violations],
            app_id=settings.github_app_id,
            app_private_key=settings.github_app_private_key,
            base_url=base_url,
        )
    return response


@router.post("/api/v1/github/webhook")
async def github_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
    session: Session = Depends(get_session),
) -> JSONResponse:
    body = await request.body()
    event_type = request.headers.get("X-GitHub-Event", "")
    delivery_guid = request.headers.get("X-GitHub-Delivery", "")

    if settings.github_webhook_secret:
        sig = request.headers.get("X-Hub-Signature-256")
        if not validate_github_signature(sig, body, settings.github_webhook_secret):
            raise EvidencePlaneError(
                401, "invalid_signature", "Invalid webhook signature."
            )

    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise EvidencePlaneError(400, "invalid_payload", "Webhook body is not valid JSON.")

    inst_data = payload.get("installation") or {}
    installation_id: int | None = inst_data.get("id") if isinstance(inst_data, dict) else None

    if delivery_guid:
        delivery = store_delivery(
            session,
            delivery_guid=delivery_guid,
            event_type=event_type,
            installation_id=installation_id,
        )
        if delivery is None:
            return JSONResponse({"ok": True, "skipped": True})

    error_msg: str | None = None
    try:
        if event_type in ("installation", "installation_repositories"):
            handle_installation_event(session, payload)
        elif event_type == "pull_request":
            await handle_pull_request_event(
                session,
                payload,
                app_id=settings.github_app_id,
                app_private_key=settings.github_app_private_key,
            )
    except Exception as exc:
        error_msg = str(exc)[:500]
        raise
    finally:
        if delivery_guid:
            mark_delivery_processed(session, delivery_guid, error=error_msg)

    return JSONResponse({"ok": True})


@router.get("/api/v1/runs", response_model=list[RunSummary])
def api_latest_runs(session: Session = Depends(get_session)) -> list[RunSummary]:
    return latest_runs(session)


@router.get("/api/v1/runs/{run_id}", response_model=RunDetail)
def api_run_detail(run_id: UUID, session: Session = Depends(get_session)) -> RunDetail:
    return get_run_detail(session, run_id)


@router.post("/api/v1/runs/{run_id}/review/approve", response_model=RunDetail)
async def approve_run_review(
    run_id: UUID,
    request: Request,
    actor: UserSession | None = Depends(require_reviewer),
    session: Session = Depends(get_session),
) -> RunDetail:
    review = await parse_review_request(request)
    identity = (
        actor.user.email if actor is not None else review.reviewer_identity
    )
    return review_run(
        session,
        run_id,
        outcome="approved",
        reviewer_identity=identity,
        review_note=review.review_note,
    )


@router.post("/api/v1/runs/{run_id}/review/reject", response_model=RunDetail)
async def reject_run_review(
    run_id: UUID,
    request: Request,
    actor: UserSession | None = Depends(require_reviewer),
    session: Session = Depends(get_session),
) -> RunDetail:
    review = await parse_review_request(request)
    identity = (
        actor.user.email if actor is not None else review.reviewer_identity
    )
    return review_run(
        session,
        run_id,
        outcome="rejected",
        reviewer_identity=identity,
        review_note=review.review_note,
    )


# --------------------------------------------------------------------------- #
# Auth endpoints
# --------------------------------------------------------------------------- #

@router.post("/api/v1/auth/users", response_model=ProvisionUserResponse)
async def create_user(
    body: ProvisionUserRequest,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> ProvisionUserResponse:
    from app.models.db import Organization
    from sqlalchemy import select as _select
    org = db.scalar(_select(Organization).limit(1))
    if org is None:
        raise EvidencePlaneError(500, "no_organization", "No organization found.")
    user, token = provision_user(
        db,
        organization_id=org.id,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
    )
    return ProvisionUserResponse(
        user_id=str(user.id),
        email=user.email,
        role=body.role,
        token=token,
    )


@router.post("/api/v1/auth/sessions", response_model=IssueSessionResponse)
async def create_session(
    request: Request,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> IssueSessionResponse:
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise EvidencePlaneError(422, "validation_error", "user_id is required.")
    from app.models.db import Organization
    from sqlalchemy import select as _select
    org = db.scalar(_select(Organization).limit(1))
    if org is None:
        raise EvidencePlaneError(500, "no_organization", "No organization found.")
    user_session, token = issue_session(
        db, user_id=UUID(user_id), organization_id=org.id
    )
    return IssueSessionResponse(
        session_id=str(user_session.id),
        role=user_session.role,
        token=token,
    )


@router.delete("/api/v1/auth/sessions/current", status_code=204)
async def revoke_current_session(
    request: Request,
    db: Session = Depends(get_session),
) -> None:
    token = _extract_bearer(request)
    if token is None:
        raise EvidencePlaneError(401, "auth_required", "Bearer token is required.")
    revoke_session(db, token)


@router.get("/api/v1/evidence/{run_id}")
def api_evidence(run_id: UUID, session: Session = Depends(get_session)) -> JSONResponse:
    try:
        body = get_evidence(session, run_id)
    except EvidencePlaneError as exc:
        log_event(
            "evidence_export_failed",
            run_id=str(run_id),
            error_code=exc.code,
            status_code=exc.status_code,
        )
        raise
    return JSONResponse(
        content=body,
        headers={
            "Content-Disposition": f'attachment; filename="evidence-{run_id}.json"'
        },
    )


# --------------------------------------------------------------------------- #
# Policy config endpoints
# --------------------------------------------------------------------------- #

@router.post("/api/v1/policy-configs", response_model=PolicyConfigResponse, status_code=201)
async def create_policy_config_endpoint(
    body: PolicyConfigRequest,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> PolicyConfigResponse:
    return create_policy_config(db, body)


@router.get("/api/v1/policy-configs", response_model=list[PolicyConfigResponse])
def list_policy_configs_endpoint(
    repo_name: str | None = None,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[PolicyConfigResponse]:
    return list_policy_configs(db, repo_name=repo_name)


@router.get("/api/v1/policy-configs/{config_id}", response_model=PolicyConfigResponse)
def get_policy_config_endpoint(
    config_id: UUID,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> PolicyConfigResponse:
    return get_policy_config(db, config_id)


@router.delete("/api/v1/policy-configs/{config_id}", status_code=204)
def deactivate_policy_config_endpoint(
    config_id: UUID,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    deactivate_policy_config(db, config_id)


@router.post("/api/v1/policy-configs/dry-run", response_model=DryRunResponse)
def dry_run_policy_config_endpoint(
    body: DryRunRequest,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> DryRunResponse:
    return dry_run_policy_config(db, body)


# --------------------------------------------------------------------------- #
# Evidence source endpoints
# --------------------------------------------------------------------------- #

@router.post("/api/v1/sources", response_model=EvidenceSourceResponse, status_code=201)
def create_source_endpoint(
    body: EvidenceSourceRequest,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> EvidenceSourceResponse:
    return create_evidence_source(db, body)


@router.get("/api/v1/sources", response_model=list[EvidenceSourceResponse])
def list_sources_endpoint(
    include_disabled: bool = False,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> list[EvidenceSourceResponse]:
    return list_evidence_sources(db, include_disabled=include_disabled)


@router.get("/api/v1/sources/{source_id}", response_model=EvidenceSourceResponse)
def get_source_endpoint(
    source_id: UUID,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> EvidenceSourceResponse:
    return get_evidence_source(db, source_id)


@router.delete("/api/v1/sources/{source_id}", status_code=204)
def disable_source_endpoint(
    source_id: UUID,
    _: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> None:
    disable_evidence_source(db, source_id)


# --------------------------------------------------------------------------- #
# Case Room endpoints
# --------------------------------------------------------------------------- #

@router.get("/api/v1/cases", response_model=list[CaseSummary])
def list_cases_endpoint(
    status: str | None = None,
    severity: str | None = None,
    case_type: str | None = None,
    limit: int = 50,
    _: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> list[CaseSummary]:
    return list_cases(db, status=status, severity=severity, case_type=case_type, limit=limit)


@router.get("/api/v1/cases/{case_id}", response_model=CaseResponse)
def get_case_endpoint(
    case_id: UUID,
    _: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> CaseResponse:
    return get_case(db, case_id)


@router.post("/api/v1/cases/{case_id}/resolve", response_model=CaseResponse)
def resolve_case_endpoint(
    case_id: UUID,
    body: CaseResolveRequest,
    actor: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> CaseResponse:
    if actor is not None:
        body = CaseResolveRequest(
            resolved_by=actor.user.email,
            resolution_note=body.resolution_note,
        )
    return resolve_case(db, case_id, body)


@router.post("/api/v1/cases/{case_id}/dismiss", response_model=CaseResponse)
def dismiss_case_endpoint(
    case_id: UUID,
    body: CaseResolveRequest,
    actor: UserSession | None = Depends(require_admin),
    db: Session = Depends(get_session),
) -> CaseResponse:
    identity = actor.user.email if actor is not None else body.resolved_by
    return dismiss_case(db, case_id, identity, note=body.resolution_note)


@router.post("/api/v1/cases/{case_id}/messages", response_model=CaseResponse)
def add_message_endpoint(
    case_id: UUID,
    body: CaseMessageRequest,
    actor: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> CaseResponse:
    if actor is not None:
        body = CaseMessageRequest(body=body.body, author_identity=actor.user.email)
    return add_message(db, case_id, body)


@router.post("/api/v1/cases/{case_id}/links", response_model=CaseResponse)
def add_link_endpoint(
    case_id: UUID,
    body: CaseExternalLinkRequest,
    actor: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> CaseResponse:
    identity = actor.user.email if actor is not None else (body.created_by or "unknown")
    return add_external_link(db, case_id, body, actor_identity=identity)


@router.get("/api/v1/runs/{run_id}/case", response_model=CaseResponse | None)
def get_run_case_endpoint(
    run_id: UUID,
    _: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> CaseResponse | None:
    return get_case_for_run(db, run_id)


@router.post("/api/v1/cases/{case_id}/advisor", response_model=AdvisorFindingResponse)
def request_advisor_finding_endpoint(
    case_id: UUID,
    _: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AdvisorFindingResponse:
    if not settings.anthropic_api_key:
        raise EvidencePlaneError(
            503, "advisor_not_configured", "Advisor is not configured on this instance."
        )
    return request_advisor_finding(db, case_id, settings.anthropic_api_key)


@router.get("/api/v1/cases/{case_id}/advisor", response_model=list[AdvisorFindingResponse])
def list_advisor_findings_endpoint(
    case_id: UUID,
    _: UserSession | None = Depends(require_reviewer),
    db: Session = Depends(get_session),
) -> list[AdvisorFindingResponse]:
    return list_advisor_findings(db, case_id)


@router.get("/")
def dashboard(request: Request, session: Session = Depends(get_session)):
    runs = latest_runs(session)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"runs": runs, "metrics": dashboard_metrics(runs)},
    )


@router.get("/runs/{run_id}")
def run_detail_page(request: Request, run_id: UUID, session: Session = Depends(get_session)):
    detail = get_run_detail(session, run_id)
    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={"run": detail},
    )
