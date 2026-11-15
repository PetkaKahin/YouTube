"""Owner permission commands and ordinary viewing share trusted server identities."""

from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.ports import VideoActors
from video.application.publications import require_actor
from video.domain.access import VideoView, ViewConditions, Viewer, ViewingSnapshot
from video.domain.permissions import SIZE, PermissionCursor, PermissionGrant, PermissionPage
from video.domain.publications import VideoError, VideoNotFound, valid_identifier


class PermissionEmails(Protocol):
    def normalize(self, email: str) -> str: ...


class PrivateGrants(Protocol):
    async def lock_owned(self, video_id: int, owner_id: int) -> bool: ...
    async def page(self, video_id: int, before: int | None) -> tuple[PermissionGrant, ...]: ...
    async def set(self, video_id: int, email: str, allowed: bool) -> None: ...


class ViewerIdentities(Protocol):
    async def get(self, account_id: int) -> Viewer: ...


class VideoViews(Protocol):
    async def snapshot(
        self, video_id: int, viewer: Viewer, *, delivery: bool = False
    ) -> ViewingSnapshot | None: ...


class ViewingPolicy(Protocol):
    def allows(self, viewer: Viewer, conditions: ViewConditions) -> bool: ...


class PermissionActions:
    def __init__(
        self,
        actors: VideoActors,
        grants: PrivateGrants,
        emails: PermissionEmails,
        transactions: Transactions,
    ) -> None:
        self.actors, self.grants = actors, grants
        self.emails, self.transactions = emails, transactions

    async def owner(self, account_id: int | None, video_id: int) -> None:
        actor = require_actor(account_id)
        await self.actors.require(actor)
        if not valid_identifier(video_id) or not await self.grants.lock_owned(video_id, actor):
            raise VideoNotFound

    async def page(
        self, account_id: int, video_id: int, cursor: str | None = None
    ) -> PermissionPage:
        before = (
            PermissionCursor.decode(cursor, video_id, account_id).id if cursor is not None else None
        )
        rows = await self.grants.page(video_id, before)
        return PermissionPage(
            tuple(row.email for row in rows[:SIZE]),
            PermissionCursor(video_id, account_id, rows[SIZE - 1].id).encode()
            if len(rows) > SIZE
            else None,
        )


class GetPrivateAccess:
    def __init__(self, actions: PermissionActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, cursor: str | None = None
    ) -> PermissionPage:
        async with self.actions.transactions.begin():
            await self.actions.owner(account_id, video_id)
            return await self.actions.page(require_actor(account_id), video_id, cursor)


class SetPrivateAccess:
    def __init__(self, actions: PermissionActions) -> None:
        self.actions = actions

    async def __call__(
        self, account_id: int | None, video_id: int, email: object, allowed: bool
    ) -> PermissionPage:
        async with self.actions.transactions.begin():
            await self.actions.owner(account_id, video_id)
            if type(allowed) is not bool:
                raise VideoError("allowed", "invalid_boolean")
            if not isinstance(email, str):
                raise VideoError("email", "invalid_email")
            normalized = self.actions.emails.normalize(email)
            await self.actions.grants.set(video_id, normalized, allowed)
            return await self.actions.page(require_actor(account_id), video_id)


class VideoAccess:
    def __init__(
        self,
        viewers: ViewerIdentities,
        views: VideoViews,
        policy: ViewingPolicy,
    ) -> None:
        self.viewers, self.views, self.policy = viewers, views, policy

    async def viewer(self, account_id: int | None) -> Viewer:
        return (
            await self.viewers.get(require_actor(account_id))
            if account_id is not None
            else Viewer()
        )

    async def require(self, viewer: Viewer, video_id: int) -> VideoView:
        if not valid_identifier(video_id):
            raise VideoNotFound
        snapshot = await self.views.snapshot(video_id, viewer)
        if snapshot is None or not self.policy.allows(viewer, snapshot.conditions):
            raise VideoNotFound
        return snapshot.video

    async def read(self, account_id: int | None, video_id: int) -> VideoView:
        if not valid_identifier(video_id):
            raise VideoNotFound
        return await self.require(await self.viewer(account_id), video_id)


class GetViewableVideo:
    def __init__(self, access: VideoAccess, transactions: Transactions) -> None:
        self.access, self.transactions = access, transactions

    async def __call__(self, account_id: int | None, video_id: int) -> VideoView:
        async with self.transactions.begin():
            return await self.access.read(account_id, video_id)
