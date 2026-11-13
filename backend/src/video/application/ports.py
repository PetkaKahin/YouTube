"""Ports owned by Video; account identity is checked through an adapter."""

from typing import Protocol

from video.domain.publications import VideoMetadata, VideoSettings


class VideoActors(Protocol):
    async def require(self, account_id: int) -> None: ...


class Publications(Protocol):
    async def create(self, owner_id: int, metadata: VideoMetadata) -> VideoSettings: ...

    async def owned(
        self, video_id: int, owner_id: int, *, lock: bool = False
    ) -> VideoSettings | None: ...

    async def update_metadata(self, value: VideoSettings) -> VideoSettings: ...
