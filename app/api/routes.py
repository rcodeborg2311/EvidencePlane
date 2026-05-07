from __future__ import annotations

from pathlib import Path
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_session
from app.errors import EvidencePlaneError
from app.models.schemas import DecisionResponse, RunDetail, RunReceipt, RunSummary
from app.observability import log_event, record_policy_decision
from app.services.runs import get_evidence, get_run_detail, latest_runs, store_run
from app.services.security import SIGNATURE_HEADER, signature_is_valid

router = APIRouter()
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[1] / "templates")
)


async def require_valid_signature(
    request: Request, settings: Settings = Depends(get_settings)
) -> bytes:
    body = await request.body()
    signature = request.headers.get(SIGNATURE_HEADER)
    if not signature_is_valid(signature, body, settings.hmac_secret):
        raise EvidencePlaneError(401, "invalid_signature", "Invalid request signature.")
    return body


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
        context={"runs": runs},
    )


@router.get("/runs/{run_id}")
def run_detail_page(request: Request, run_id: UUID, session: Session = Depends(get_session)):
    detail = get_run_detail(session, run_id)
    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={"run": detail},
    )
