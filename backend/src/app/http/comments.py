"""Public discussion endpoints keep identity in the session and text in JSON data."""

from datetime import datetime
from typing import Literal

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool

from app.http.videos import actor_id
from comment.public import (
    CommentAuthenticationRequired,
    CommentItem,
    CreateComment,
    DiscussionNotFound,
    GetCommentLike,
    GetComments,
    InvalidComment,
    InvalidCommentCursor,
    SetCommentLike,
)
from common.http.errors import problem
from common.http.schemas import Identifier, JSONModel

HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie"}
READ_SECURITY: dict[str, object] = {"security": [{}, {"SessionCookie": []}]}
WRITE_SECURITY: dict[str, object] = {"security": [{"SessionCookie": []}]}


class CommentBody(JSONModel):
    text: str = Field(min_length=1, max_length=2000)


class CommentResponse(JSONModel):
    id: Identifier
    author_name: str
    text: str
    created_at: datetime
    liked: bool
    like_count: int = Field(ge=0)

    @classmethod
    def from_value(cls, value: CommentItem) -> CommentResponse:
        return cls(
            id=value.id,
            author_name=value.author_name,
            text=value.text,
            created_at=value.created_at,
            liked=value.liked,
            like_count=value.like_count,
        )


class CommentsResponse(JSONModel):
    items: list[CommentResponse]
    next_cursor: str | None
    can_comment: bool


class CommentLikeBody(JSONModel):
    liked: StrictBool


class CommentLikeResponse(JSONModel):
    liked: bool
    like_count: int = Field(ge=0)
    can_like: bool


def comment_problem(request: Request, error: Exception) -> JSONResponse:
    if isinstance(error, CommentAuthenticationRequired):
        return problem(request, 401, "unauthenticated", "Authentication required", headers=HEADERS)
    if isinstance(error, DiscussionNotFound):
        return problem(request, 404, "not_found", "Discussion not found", headers=HEADERS)
    if isinstance(error, InvalidCommentCursor):
        return problem(request, 422, "invalid_cursor", "Invalid comment cursor", headers=HEADERS)
    if isinstance(error, InvalidComment):
        return problem(request, 422, "invalid_comment", "Invalid comment request", headers=HEADERS)
    return problem(request, 500, "internal_error", "An unexpected error occurred", headers=HEADERS)


def comments_router() -> APIRouter:
    router = APIRouter(
        prefix="/videos/{video_id}/comments", tags=["Comments"], route_class=DishkaRoute
    )

    @router.get("", response_model=CommentsResponse, openapi_extra=READ_SECURITY)
    async def page(
        video_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetComments],
        sort: Literal["newest", "popular"] = "newest",
        cursor: str | None = None,
    ) -> CommentsResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            if any(len(request.query_params.getlist(key)) > 1 for key in ("sort", "cursor")):
                raise InvalidCommentCursor
            value = await scenario(actor_id(request), video_id, sort, cursor)
            return CommentsResponse(
                items=[CommentResponse.from_value(item) for item in value.items],
                next_cursor=value.next_cursor,
                can_comment=value.can_comment,
            )
        except Exception as error:
            return comment_problem(request, error)

    @router.post("", status_code=201, response_model=CommentResponse, openapi_extra=WRITE_SECURITY)
    async def create(
        video_id: Identifier,
        body: CommentBody,
        request: Request,
        response: Response,
        scenario: FromDishka[CreateComment],
    ) -> CommentResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            return CommentResponse.from_value(
                await scenario(actor_id(request), video_id, body.text)
            )
        except Exception as error:
            return comment_problem(request, error)

    @router.get(
        "/{comment_id}/like", response_model=CommentLikeResponse, openapi_extra=READ_SECURITY
    )
    async def state(
        video_id: Identifier,
        comment_id: Identifier,
        request: Request,
        response: Response,
        scenario: FromDishka[GetCommentLike],
    ) -> CommentLikeResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), video_id, comment_id)
            return CommentLikeResponse(
                liked=value.liked, like_count=value.like_count, can_like=value.can_like
            )
        except Exception as error:
            return comment_problem(request, error)

    @router.put(
        "/{comment_id}/like", response_model=CommentLikeResponse, openapi_extra=WRITE_SECURITY
    )
    async def set_state(
        video_id: Identifier,
        comment_id: Identifier,
        body: CommentLikeBody,
        request: Request,
        response: Response,
        scenario: FromDishka[SetCommentLike],
    ) -> CommentLikeResponse | JSONResponse:
        response.headers.update(HEADERS)
        try:
            value = await scenario(actor_id(request), video_id, comment_id, body.liked)
            return CommentLikeResponse(
                liked=value.liked, like_count=value.like_count, can_like=value.can_like
            )
        except Exception as error:
            return comment_problem(request, error)

    return router
