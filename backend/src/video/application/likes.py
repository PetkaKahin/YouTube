"""Fresh normal-viewing checks and serialized target-state like mutations."""

from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.access import VideoAccess
from video.application.publications import require_actor
from video.domain.access import Viewer
from video.domain.likes import InvalidLike, LikeState, LikeTotals
from video.domain.publications import VideoNotFound, valid_identifier


class VideoLikes(Protocol):
    async def lock(self, video_id: int) -> bool: ...

    async def set(self, video_id: int, account_id: int, liked: bool) -> None: ...
    async def set_dislike(self, video_id: int, account_id: int, disliked: bool) -> None: ...

    async def totals(self, video_id: int, account_id: int | None) -> LikeTotals: ...


class LikeActions:
    def __init__(self, access: VideoAccess, likes: VideoLikes, transactions: Transactions) -> None:
        self.access, self.likes, self.transactions = access, likes, transactions

    async def state(self, viewer: Viewer, video_id: int) -> LikeState:
        totals = await self.likes.totals(video_id, viewer.account_id)
        return LikeState(
            totals.liked,
            totals.like_count,
            viewer.authenticated,
            totals.disliked,
            viewer.authenticated,
        )

    async def change(
        self, account_id: int | None, video_id: int, selected: object, *, dislike: bool
    ) -> LikeState:
        async with self.transactions.begin():
            actor = require_actor(account_id)
            viewer = await self.access.viewer(actor)
            if not valid_identifier(video_id):
                raise VideoNotFound
            if type(selected) is not bool:
                raise InvalidLike
            if not await self.likes.lock(video_id):
                raise VideoNotFound
            await self.access.require(viewer, video_id)
            if dislike:
                await self.likes.set_dislike(video_id, actor, selected)
            else:
                await self.likes.set(video_id, actor, selected)
            return await self.state(viewer, video_id)


class GetVideoLike:
    def __init__(self, actions: LikeActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int) -> LikeState:
        async with self.actions.transactions.begin():
            if not valid_identifier(video_id):
                raise VideoNotFound
            viewer = await self.actions.access.viewer(account_id)
            await self.actions.access.require(viewer, video_id)
            return await self.actions.state(viewer, video_id)


class SetVideoLike:
    def __init__(self, actions: LikeActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int, liked: object) -> LikeState:
        return await self.actions.change(account_id, video_id, liked, dislike=False)


class SetVideoDislike:
    def __init__(self, actions: LikeActions) -> None:
        self.actions = actions

    async def __call__(self, account_id: int | None, video_id: int, disliked: object) -> LikeState:
        return await self.actions.change(account_id, video_id, disliked, dislike=True)
