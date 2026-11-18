"""Session-only personal video lists using the established card response."""

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.http.feed import FeedCardResponse, FeedResponse
from app.http.videos import actor_id, video_problem
from common.http.errors import problem
from video.public import AuthenticationRequired, GetLibraryVideos, InvalidLibraryCursor

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}


def library_router() -> APIRouter:
    router = APIRouter(prefix="/videos", tags=["Library"], route_class=DishkaRoute)

    def register(kind: str) -> None:
        @router.get(
            "/" + kind,
            name="library_" + kind,
            response_model=FeedResponse,
            openapi_extra={"security": [{"SessionCookie": []}]},
        )
        async def listing(
            request: Request,
            response: Response,
            scenario: FromDishka[GetLibraryVideos],
            cursor: str | None = None,
        ) -> FeedResponse | JSONResponse:
            response.headers.update(HEADERS)
            if any(
                key != "cursor" or len(request.query_params.getlist(key)) != 1
                for key in request.query_params
            ):
                cursor = ""
            try:
                page = await scenario(actor_id(request), kind, cursor)
            except AuthenticationRequired as error:
                return video_problem(request, error)
            except InvalidLibraryCursor:
                return problem(
                    request, 422, "invalid_cursor", "Invalid library cursor", headers=HEADERS
                )
            except Exception:
                return problem(
                    request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS
                )
            return FeedResponse(
                items=[FeedCardResponse.from_value(card) for card in page.items],
                next_cursor=page.next_cursor,
            )

    for kind in ("subscriptions", "liked", "history"):
        register(kind)
    return router
