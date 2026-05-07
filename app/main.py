from __future__ import annotations

from pathlib import Path
import time
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api.routes import router
from app.config import ConfigurationError, MAX_PAYLOAD_BYTES, env_flag
from app.errors import (
    EvidencePlaneError,
    configuration_error_handler,
    error_response,
    evidence_plane_error_handler,
    operational_error_handler,
    validation_error_handler,
)
from app.observability import configure_logging, log_event


def configure_otel() -> None:
    try:
        from app.config import get_settings

        if not get_settings().enable_otel:
            return
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
    except (ConfigurationError, ImportError):
        return

    trace.set_tracer_provider(TracerProvider())


def create_app() -> FastAPI:
    configure_logging()
    docs_enabled = env_flag("EVIDENCEPLANE_ENABLE_DOCS")
    app = FastAPI(
        title="EvidencePlane",
        debug=False,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
            if len(body) > MAX_PAYLOAD_BYTES:
                response = error_response(
                    413,
                    "payload_too_large",
                    "Request payload exceeds 256 KB.",
                )
                response.headers["X-Request-ID"] = request_id
                log_event(
                    "http_request",
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=413,
                    duration_ms=round((time.perf_counter() - started) * 1000, 2),
                )
                return response
            request._body = body
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        log_event(
            "http_request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return response

    app.add_exception_handler(EvidencePlaneError, evidence_plane_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(OperationalError, operational_error_handler)
    app.add_exception_handler(ConfigurationError, configuration_error_handler)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.include_router(router)
    configure_otel()
    return app


app = create_app()
