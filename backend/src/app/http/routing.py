"""Versioned HTTP routing; business modules expose use cases, never HTTP routes."""

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request

from app.http.accounts import accounts_router
from app.http.channels import channels_router
from app.http.comments import comments_router
from app.http.feed import feed_router
from app.http.job_administration import job_administration_router
from app.http.media import media_router
from app.http.moderation import moderation_router
from app.http.playlists import playlists_router
from app.http.search import search_router
from app.http.thumbnails import thumbnails_router
from app.http.uploads import upload_router
from app.http.video_access import video_access_router
from app.http.video_likes import video_likes_router
from app.http.videos import videos_router
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
    router.include_router(channels_router())
    router.include_router(feed_router())
    router.include_router(playlists_router())
    router.include_router(search_router())
    router.include_router(videos_router())
    router.include_router(video_access_router())
    router.include_router(video_likes_router())
    router.include_router(comments_router())
    router.include_router(job_administration_router())
    router.include_router(moderation_router())
    router.include_router(media_router())
    router.include_router(upload_router())
    router.include_router(thumbnails_router())
    return router
