"""Media control plane: authorisation/manifests here, object bytes in Nginx."""

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.http.videos import VideoSettingsResponse, actor_id, video_problem
from common.http.errors import problem
from common.http.schemas import Identifier
from video.public import (
    AuthenticationRequired,
    GetMedia,
    PlaylistDelivery,
    PublicationError,
    PublishVideo,
    StorageUnavailable,
    VideoNotFound,
)

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie", "X-Content-Type-Options": "nosniff"}


async def deliver(
    request: Request, video_id: int, track: str | None, filename: str, scenario: GetMedia
) -> Response:
    try:
        value = await scenario(actor_id(request), video_id, track, filename, method=request.method)
    except (AuthenticationRequired, VideoNotFound) as error:
        return video_problem(request, error)
    except StorageUnavailable:
        return problem(request, 503, "media_unavailable", "Media is unavailable", headers=HEADERS)
    if isinstance(value, PlaylistDelivery):
        body = value.text.encode("utf-8")
        return Response(
            content=b"" if request.method == "HEAD" else body,
            media_type=value.media_type,
            headers={**HEADERS, "Content-Length": str(len(body))},
        )
    return Response(
        headers={**HEADERS, "X-Accel-Redirect": value.redirect}, media_type=value.media_type
    )


def media_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Media"], route_class=DishkaRoute)
    security: dict[str, list[dict[str, list[str]]]] = {"security": [{}, {"SessionCookie": []}]}

    @router.post(
        "/{video_id}/publish",
        response_model=VideoSettingsResponse,
        openapi_extra={"security": [{"SessionCookie": []}]},
    )
    async def publish(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[PublishVideo],
    ) -> VideoSettingsResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), video_id)
        except (AuthenticationRequired, VideoNotFound) as error:
            return video_problem(request, error)
        except PublicationError:
            return problem(
                request,
                409,
                "upload_not_completed",
                "Upload must be completed before publishing",
                headers=HEADERS,
            )
        return VideoSettingsResponse.from_value(value)

    @router.head("/{video_id}/hls/master.m3u8", include_in_schema=False)
    @router.get(
        "/{video_id}/hls/master.m3u8",
        openapi_extra=security,
        response_class=Response,
        responses={200: {"content": {"application/vnd.apple.mpegurl": {}}}},
    )
    async def master(
        video_id: Identifier, request: Request, scenario: FromDishka[GetMedia]
    ) -> Response:
        return await deliver(request, video_id, None, "master.m3u8", scenario)

    @router.head("/{video_id}/hls/tracks/{track}/{filename}", include_in_schema=False)
    @router.get(
        "/{video_id}/hls/tracks/{track}/{filename}",
        openapi_extra=security,
        response_class=Response,
        responses={
            200: {
                "content": {
                    kind: {}
                    for kind in (
                        "application/vnd.apple.mpegurl",
                        "video/mp4",
                        "audio/mp4",
                        "text/vtt",
                    )
                }
            }
        },
    )
    async def track_file(
        video_id: Identifier,
        track: str,
        filename: str,
        request: Request,
        scenario: FromDishka[GetMedia],
    ) -> Response:
        return await deliver(request, video_id, track, filename, scenario)

    @router.head("/{video_id}/preview.webp", include_in_schema=False)
    @router.get(
        "/{video_id}/preview.webp",
        openapi_extra=security,
        response_class=Response,
        responses={200: {"content": {"image/webp": {}}}},
    )
    async def preview(
        video_id: Identifier, request: Request, scenario: FromDishka[GetMedia]
    ) -> Response:
        return await deliver(request, video_id, None, "preview.webp", scenario)

    return router
