from __future__ import annotations

from datetime import datetime
import hmac
from pathlib import Path
import time
from urllib.parse import parse_qs
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_session
from app.errors import EvidencePlaneError
from app.models.schemas import (
    DecisionResponse,
    ReviewRequest,
    RunDetail,
    RunReceipt,
    RunSummary,
)
from app.observability import log_event, record_policy_decision
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


def require_admin(
    request: Request, settings: Settings = Depends(get_settings)
) -> None:
    header = request.headers.get("Authorization")
    if header is None or not header.startswith("Bearer "):
        raise EvidencePlaneError(
            401,
            "admin_auth_required",
            "Admin bearer token is required.",
        )
    supplied = header.removeprefix("Bearer ").strip()
    if not supplied or not hmac.compare_digest(supplied, settings.admin_token):
        raise EvidencePlaneError(
            403,
            "admin_auth_invalid",
            "Admin bearer token is invalid.",
        )


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
    return response


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
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> RunDetail:
    review = await parse_review_request(request)
    return review_run(
        session,
        run_id,
        outcome="approved",
        reviewer_identity=review.reviewer_identity,
        review_note=review.review_note,
    )


@router.post("/api/v1/runs/{run_id}/review/reject", response_model=RunDetail)
async def reject_run_review(
    run_id: UUID,
    request: Request,
    _: None = Depends(require_admin),
    session: Session = Depends(get_session),
) -> RunDetail:
    review = await parse_review_request(request)
    return review_run(
        session,
        run_id,
        outcome="rejected",
        reviewer_identity=review.reviewer_identity,
        review_note=review.review_note,
    )


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
