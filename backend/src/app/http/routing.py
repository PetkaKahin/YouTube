"""Versioned HTTP routing; business modules expose use cases, never HTTP routes."""

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request

from app.http.accounts import accounts_router
from app.resources import Resources
from common.http.errors import problem
from common.http.schemas import JSONModel


class Ready(JSONModel):
    status: str


def api_router() -> APIRouter:
    router = APIRouter(
        prefix="/v1",
        route_class=DishkaRoute,
        responses={
            status: {
                "description": "Problem Details",
                "content": {
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                },
            }
            for status in (400, 401, 403, 404, 405, 409, 422, 500, 503)
        },
    )

    @router.get("/health/ready", tags=["System"], response_model=Ready)
    async def ready(request: Request, resources: FromDishka[Resources]):
        try:
            await resources.check()
        except Exception:
            return problem(request, 503, "dependencies_unavailable", "Service is not ready")
        return Ready(status="ready")

    router.include_router(accounts_router())
    return router
