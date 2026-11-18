"""Cross-module adapters use only User.public and Video.public."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from moderation.domain.reports import (
    ModerationAuthenticationRequired,
    ModerationForbidden,
    ReportedQuality,
    ReportedVideo,
    ReportNotFound,
    ReviewMediaUnavailable,
    ReviewObject,
    ReviewPlaylist,
)
from user.public import AccountNotFound, GetStaffIdentity, StaffForbidden
from video.public import (
    AuthenticationRequired,
    PlaylistDelivery,
    StorageUnavailable,
    VideoNotFound,
)
from video.public.moderation import ReportVideoAccess


class UserStaffActors:
    def __init__(self, identities: GetStaffIdentity) -> None:
        self.identities = identities

    async def require(self, account_id: int | None) -> int:
        try:
            return (await self.identities(account_id)).id
        except AccountNotFound:
            raise ModerationAuthenticationRequired from None
        except StaffForbidden:
            raise ModerationForbidden from None


@asynccontextmanager
async def video_errors() -> AsyncGenerator[None]:
    try:
        yield
    except AuthenticationRequired:
        raise ModerationAuthenticationRequired from None
    except VideoNotFound:
        raise ReportNotFound from None
    except StorageUnavailable:
        raise ReviewMediaUnavailable from None


class VideoReports:
    def __init__(self, videos: ReportVideoAccess) -> None:
        self.videos = videos

    async def reportable(self, account_id: int, video_id: int) -> None:
        async with video_errors():
            await self.videos.reportable(account_id, video_id)

    async def lock(self, account_id: int, video_id: int, *, exclusive: bool = False) -> None:
        async with video_errors():
            await self.videos.lock(account_id, video_id, exclusive=exclusive)

    async def hide(self, account_id: int, video_id: int) -> None:
        async with video_errors():
            await self.videos.hide(account_id, video_id)

    async def view(self, account_id: int, video_id: int) -> ReportedVideo:
        async with video_errors():
            value = await self.videos.view(account_id, video_id)
            return ReportedVideo(
                value.id,
                value.title,
                value.description,
                value.visibility,
                value.added_at,
                value.updated_at,
                tuple(ReportedQuality(item.quality, item.range) for item in value.qualities),
                value.hd,
                value.uhd,
                value.hdr,
                value.uhd_hdr,
            )

    async def media(
        self, account_id: int, video_id: int, track: str | None, filename: str, method: str
    ) -> ReviewPlaylist | ReviewObject:
        async with video_errors():
            value = await self.videos.media(account_id, video_id, track, filename, method)
            if isinstance(value, PlaylistDelivery):
                return ReviewPlaylist(value.text, value.media_type)
            return ReviewObject(value.redirect, value.media_type)
