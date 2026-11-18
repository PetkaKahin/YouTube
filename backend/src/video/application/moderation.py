"""Video primitives used only inside a validated, audited report transaction."""

from typing import Protocol

from video.application.access import VideoAccess
from video.application.delivery import MediaRedirects, resolve_delivery
from video.application.publications import require_actor
from video.domain.access import VideoView, ViewingSnapshot
from video.domain.delivery import ObjectDelivery, PlaylistDelivery
from video.domain.publications import VideoNotFound, valid_identifier


class VideoStaffActors(Protocol):
    async def require(self, account_id: int) -> None: ...


class ModerationViews(Protocol):
    async def lock(self, video_id: int, *, exclusive: bool = False) -> bool: ...

    async def snapshot(
        self, video_id: int, *, delivery: bool = False
    ) -> ViewingSnapshot | None: ...

    async def hide(self, video_id: int) -> None: ...


class ReportVideoAccess:
    def __init__(
        self,
        access: VideoAccess,
        staff: VideoStaffActors,
        views: ModerationViews,
        redirects: MediaRedirects,
    ) -> None:
        self.access, self.staff, self.views, self.redirects = access, staff, views, redirects

    async def reportable(self, account_id: int, video_id: int) -> None:
        viewer = await self.access.viewer(require_actor(account_id))
        if not valid_identifier(video_id) or not await self.views.lock(video_id):
            raise VideoNotFound
        await self.access.require(viewer, video_id)

    async def lock(self, account_id: int, video_id: int, *, exclusive: bool = False) -> None:
        await self.staff.require(require_actor(account_id))
        if not valid_identifier(video_id) or not await self.views.lock(
            video_id, exclusive=exclusive
        ):
            raise VideoNotFound

    async def prepared(self, video_id: int, *, delivery: bool = False) -> ViewingSnapshot:
        snapshot = await self.views.snapshot(video_id, delivery=delivery)
        if (
            snapshot is None
            or not snapshot.conditions.published
            or not snapshot.conditions.playable
        ):
            raise VideoNotFound
        return snapshot

    async def hide(self, account_id: int, video_id: int) -> None:
        await self.lock(account_id, video_id, exclusive=True)
        await self.views.hide(video_id)

    async def view(self, account_id: int, video_id: int) -> VideoView:
        await self.lock(account_id, video_id)
        return (await self.prepared(video_id)).video

    async def media(
        self, account_id: int, video_id: int, track: str | None, filename: str, method: str
    ) -> PlaylistDelivery | ObjectDelivery:
        if method not in ("GET", "HEAD"):
            raise VideoNotFound
        await self.lock(account_id, video_id)
        snapshot = await self.prepared(video_id, delivery=True)
        if snapshot.delivery is None:
            raise VideoNotFound
        media = resolve_delivery(snapshot.delivery, track, filename)
        if isinstance(media, PlaylistDelivery):
            return media
        return ObjectDelivery(await self.redirects.sign(media, method), media.media_type)
