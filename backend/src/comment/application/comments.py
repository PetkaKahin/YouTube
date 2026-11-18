"""Public-video discussions and transactional target-state comment likes."""

from typing import Protocol

from comment.domain.comments import (
    SIZE,
    CommentAuthenticationRequired,
    CommentCursor,
    CommentItem,
    CommentLikeState,
    CommentPage,
    CommentRecord,
    CommentSort,
    DiscussionNotFound,
    InvalidComment,
    valid_id,
    validate_text,
)
from common.contracts.transactions import Transactions


class DiscussionAccess(Protocol):
    async def require(self, account_id: int | None, video_id: int) -> None: ...


class CommentAuthors(Protocol):
    async def names(self, account_ids: tuple[int, ...]) -> dict[int, str]: ...


class Comments(Protocol):
    async def create(self, video_id: int, author_id: int, text: str) -> CommentRecord: ...

    async def page(
        self, video_id: int, account_id: int | None, sort: CommentSort, after: CommentCursor | None
    ) -> tuple[CommentRecord, ...]: ...

    async def get(
        self, video_id: int, comment_id: int, account_id: int | None, *, lock: bool = False
    ) -> CommentRecord | None: ...

    async def set_like(self, comment_id: int, account_id: int, liked: bool) -> None: ...


class CommentActions:
    def __init__(
        self,
        access: DiscussionAccess,
        authors: CommentAuthors,
        comments: Comments,
        transactions: Transactions,
    ) -> None:
        self.access, self.authors, self.comments, self.transactions = (
            access,
            authors,
            comments,
            transactions,
        )

    async def require(self, account_id: int | None, video_id: int, *, write: bool = False) -> None:
        if (write and account_id is None) or (account_id is not None and not valid_id(account_id)):
            raise CommentAuthenticationRequired
        if not valid_id(video_id):
            raise DiscussionNotFound
        await self.access.require(account_id, video_id)

    async def items(self, rows: tuple[CommentRecord, ...]) -> tuple[CommentItem, ...]:
        names = await self.authors.names(tuple(dict.fromkeys(row.author_id for row in rows)))
        return tuple(
            CommentItem(
                row.id, names[row.author_id], row.text, row.created_at, row.liked, row.like_count
            )
            for row in rows
        )

    async def comment(
        self, video_id: int, comment_id: int, account_id: int | None, *, lock: bool = False
    ) -> CommentRecord:
        if not valid_id(comment_id):
            raise DiscussionNotFound
        row = await self.comments.get(video_id, comment_id, account_id, lock=lock)
        if row is None:
            raise DiscussionNotFound
        return row


class GetComments:
    def __init__(self, actions: CommentActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, sort: str = "newest", cursor: str | None = None
    ) -> CommentPage:
        async with self.actions.transactions.begin():
            await self.actions.require(account_id, video_id)
            if sort not in ("newest", "popular"):
                raise InvalidComment
            order = CommentSort(sort)
            after = CommentCursor.decode(cursor, video_id, order) if cursor is not None else None
            rows = await self.actions.comments.page(video_id, account_id, order, after)
            items = await self.actions.items(rows[:SIZE])
            last = rows[SIZE - 1] if len(rows) > SIZE else None
            token = (
                CommentCursor(
                    video_id,
                    order,
                    last.like_count if order == CommentSort.POPULAR else 0,
                    last.created_at,
                    last.id,
                ).encode()
                if last is not None
                else None
            )
            return CommentPage(items, token, account_id is not None)


class CreateComment:
    def __init__(self, actions: CommentActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int, text: object) -> CommentItem:
        async with self.actions.transactions.begin():
            await self.actions.require(account_id, video_id, write=True)
            if account_id is None:
                raise CommentAuthenticationRequired
            row = await self.actions.comments.create(video_id, account_id, validate_text(text))
            return (await self.actions.items((row,)))[0]


class GetCommentLike:
    def __init__(self, actions: CommentActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, comment_id: int
    ) -> CommentLikeState:
        async with self.actions.transactions.begin():
            await self.actions.require(account_id, video_id)
            row = await self.actions.comment(video_id, comment_id, account_id)
            return CommentLikeState(row.liked, row.like_count, account_id is not None)


class SetCommentLike:
    def __init__(self, actions: CommentActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, comment_id: int, liked: object
    ) -> CommentLikeState:
        async with self.actions.transactions.begin():
            await self.actions.require(account_id, video_id, write=True)
            if account_id is None:
                raise CommentAuthenticationRequired
            if type(liked) is not bool:
                raise InvalidComment
            await self.actions.comment(video_id, comment_id, account_id, lock=True)
            await self.actions.comments.set_like(comment_id, account_id, liked)
            row = await self.actions.comment(video_id, comment_id, account_id)
            return CommentLikeState(row.liked, row.like_count, True)
