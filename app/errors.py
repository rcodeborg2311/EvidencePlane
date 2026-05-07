from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.config import ConfigurationError


class EvidencePlaneError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


async def evidence_plane_error_handler(
    request: Request, exc: EvidencePlaneError
) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return error_response(422, "validation_error", "Request body failed validation.")


async def operational_error_handler(
    request: Request, exc: OperationalError
) -> JSONResponse:
    return error_response(503, "service_unavailable", "Database is unreachable.")


async def configuration_error_handler(
    request: Request, exc: ConfigurationError
) -> JSONResponse:
    return error_response(503, "service_unavailable", str(exc))
