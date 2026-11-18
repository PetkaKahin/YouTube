"""Every media request performs the ordinary viewing checks before signing storage I/O."""

from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.access import VideoViews, ViewerIdentities, ViewingPolicy
from video.application.publications import require_actor
from video.application.thumbnails import ThumbnailActions
from video.domain.access import Viewer
from video.domain.delivery import MediaDelivery, ObjectDelivery, PlaylistDelivery, StoredMedia
from video.domain.publications import VideoNotFound, valid_identifier


class MediaRedirects(Protocol):
    async def sign(self, media: StoredMedia, method: str) -> str: ...


def resolve_delivery(
    delivery: MediaDelivery, track: str | None, filename: str
) -> PlaylistDelivery | StoredMedia:
    if track is None:
        if filename == "master.m3u8":
            return PlaylistDelivery(delivery.master)
        if filename != "preview.webp" or delivery.preview is None:
            raise VideoNotFound
        return delivery.preview
    package = delivery.tracks.get(track)
    if package is None:
        raise VideoNotFound
    if filename == "index.m3u8":
        return PlaylistDelivery(package.playlist)
    media = package.files.get(filename)
    if media is None:
        raise VideoNotFound
    return media


class GetMedia:
    def __init__(
        self,
        viewers: ViewerIdentities,
        views: VideoViews,
        policy: ViewingPolicy,
        redirects: MediaRedirects,
        transactions: Transactions,
    ) -> None:
        self.viewers, self.views, self.policy, self.redirects, self.transactions = (
            viewers,
            views,
            policy,
            redirects,
            transactions,
        )

    async def __call__(
        self,
        account_id: int | None,
        video_id: int,
        track: str | None,
        filename: str,
        *,
        method: str = "GET",
    ) -> PlaylistDelivery | ObjectDelivery:
        if not valid_identifier(video_id) or method not in {"GET", "HEAD"}:
            raise VideoNotFound
        async with self.transactions.begin():
            viewer = (
                await self.viewers.get(require_actor(account_id))
                if account_id is not None
                else Viewer()
            )
            snapshot = await self.views.snapshot(video_id, viewer, delivery=True)
            if (
                snapshot is None
                or not self.policy.allows(viewer, snapshot.conditions)
                or snapshot.delivery is None
            ):
                raise VideoNotFound
            media = resolve_delivery(snapshot.delivery, track, filename)
            if isinstance(media, PlaylistDelivery):
                return media
        return ObjectDelivery(await self.redirects.sign(media, method), media.media_type)


class GetThumbnailDelivery:
    def __init__(self, actions: ThumbnailActions, redirects: MediaRedirects) -> None:
        self.actions, self.redirects = actions, redirects

    async def __call__(
        self, account_id: int | None, video_id: int, *, slot: int | None = None, method: str = "GET"
    ) -> ObjectDelivery:
        async with self.actions.ops.transactions.begin():
            await self.actions.ops.owner(account_id, video_id)
            if method not in {"GET", "HEAD"} or (
                slot is not None and (type(slot) is not int or slot not in range(3))
            ):
                raise VideoNotFound
            media = await self.actions.jobs.image_media(video_id, slot)
        return ObjectDelivery(await self.redirects.sign(media, method), media.media_type)
