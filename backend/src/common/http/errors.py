"""RFC 9457 responses, without echoing request values or exception messages."""

from collections.abc import Mapping
from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from common.http.schemas import FieldError, Problem


def problem(
    request: Request,
    status: int,
    code: str,
    detail: str,
    errors: list[FieldError] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    title = HTTPStatus(status).phrase if status in HTTPStatus else "HTTP error"
    payload = Problem(
        title=title,
        status=status,
        detail=detail,
        code=code,
        request_id=request_id,
        errors=errors or [],
    )
    return JSONResponse(
        status_code=status,
        content=payload.model_dump(),
        media_type="application/problem+json",
        headers={
            "Cache-Control": "no-store",
            "Vary": "Cookie",
            **(headers or {}),
            "X-Request-ID": request_id,
        },
    )


async def http_error(request: Request, exception: Exception) -> JSONResponse:
    if not isinstance(exception, HTTPException):
        return problem(request, 500, "internal_error", "An unexpected error occurred")
    # Only a stable public description is returned; arbitrary exception.detail can contain input.
    status = exception.status_code
    codes = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    return problem(
        request,
        status,
        codes.get(status, "http_error"),
        HTTPStatus(status).phrase if status in HTTPStatus else "HTTP error",
        headers=exception.headers,
    )


async def validation_error(request: Request, exception: Exception) -> JSONResponse:
    if not isinstance(exception, RequestValidationError):
        return problem(request, 500, "internal_error", "An unexpected error occurred")
    errors = [
        FieldError(location=list(error["loc"]), code=error["type"]) for error in exception.errors()
    ]
    return problem(request, 422, "validation_error", "Request validation failed", errors)
