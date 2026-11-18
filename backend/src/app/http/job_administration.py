"""Administrator-only diagnostic history and durable retry intake."""

from datetime import datetime
from typing import Annotated, Literal

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field

from app.http.videos import actor_id
from app.job_administration import JobAdministration
from common.contracts.job_administration import (
    InvalidJobCursor,
    InvalidJobRequest,
    JobAdminAuthenticationRequired,
    JobAdminForbidden,
    JobAdminNotFound,
)
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"}
SECURITY: dict[str, object] = {"security": [{"SessionCookie": []}]}


class SelectedRetry(JSONModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    mode: Literal["selected"]
    job_ids: list[Identifier] = Field(min_length=1)


class AllRetries(JSONModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    mode: Literal["all"]


RetryBody = Annotated[SelectedRetry | AllRetries, Field(discriminator="mode")]


class TraceResponse(JSONModel):
    file: str
    function: str
    line: int


class JobErrorResponse(JSONModel):
    id: Identifier
    job_id: Identifier
    attempt: Identifier
    stage: str
    occurred_at: datetime
    reason: str
    exception_type: str
    trace: list[TraceResponse]
    error_code: str | None
    task: str | None
    job_status: str | None
    replacement_job_id: Identifier | None


class ErrorPageResponse(JSONModel):
    items: list[JobErrorResponse] = Field(max_length=20)
    next_cursor: str | None


class RetryItemResponse(JSONModel):
    job_id: Identifier
    replacement_job_id: Identifier | None
    status: Literal["queued", "skipped", "unavailable"]
    reason: str | None


class RetryResponse(JSONModel):
    id: Identifier
    actor_id: Identifier
    mode: Literal["selected", "all"]
    status: Literal["pending", "submitted", "cancelled"]
    created_at: datetime
    queued: int
    skipped: int
    unavailable: int
    items: list[RetryItemResponse] = Field(max_length=20)
    next_cursor: str | None


def admin_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, JobAdminAuthenticationRequired):
        return problem(request, 401, "unauthenticated", "Authentication required", headers=HEADERS)
    if isinstance(error, JobAdminForbidden):
        return problem(request, 403, "forbidden", "Administrator role required", headers=HEADERS)
    if isinstance(error, JobAdminNotFound):
        return problem(request, 404, "not_found", "Retry request not found", headers=HEADERS)
    if isinstance(error, (InvalidJobRequest, InvalidJobCursor)):
        return problem(
            request, 422, "invalid_job_request", "Invalid job request or cursor", headers=HEADERS
        )
    return problem(
        request,
        503,
        "job_administration_unavailable",
        "Job administration is unavailable",
        headers=HEADERS,
    )


def job_administration_router() -> APIRouter:
    router = APIRouter(tags=["Job administration"], route_class=DishkaRoute)

    @router.get("/admin/job-errors", response_model=ErrorPageResponse, openapi_extra=SECURITY)
    async def errors(
        request: Request,
        response: Response,
        scenario: FromDishka[JobAdministration],
        cursor: str | None = None,
    ) -> ErrorPageResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            if len(request.query_params.getlist("cursor")) > 1:
                raise InvalidJobCursor
            return ErrorPageResponse.model_validate(
                await scenario.errors(actor_id(request), cursor)
            )
        except Exception as error:
            return admin_problem(request, error)

    @router.post(
        "/admin/job-retries", status_code=202, response_model=RetryResponse, openapi_extra=SECURITY
    )
    async def retry(
        body: RetryBody,
        request: Request,
        response: Response,
        scenario: FromDishka[JobAdministration],
    ) -> RetryResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            selection = tuple(body.job_ids) if isinstance(body, SelectedRetry) else None
            return RetryResponse.model_validate(
                await scenario.request(actor_id(request), selection)
            )
        except Exception as error:
            return admin_problem(request, error)

    @router.post(
        "/admin/jobs/{job_id}/retry",
        status_code=202,
        response_model=RetryResponse,
        openapi_extra=SECURITY,
    )
    async def retry_one(
        job_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[JobAdministration],
    ) -> RetryResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return RetryResponse.model_validate(
                await scenario.request(actor_id(request), (job_id,))
            )
        except Exception as error:
            return admin_problem(request, error)

    @router.get(
        "/admin/job-retries/{request_id}", response_model=RetryResponse, openapi_extra=SECURITY
    )
    async def retry_status(
        request_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[JobAdministration],
        cursor: str | None = None,
    ) -> RetryResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            if len(request.query_params.getlist("cursor")) > 1:
                raise InvalidJobCursor
            return RetryResponse.model_validate(
                await scenario.details(actor_id(request), request_id, cursor)
            )
        except Exception as error:
            return admin_problem(request, error)

    return router
