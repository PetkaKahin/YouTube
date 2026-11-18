"""Trusted operator imports of previously verified seed media; no HTTP entry point."""

from pathlib import Path
from typing import Protocol

from common.contracts.transactions import Transactions
from video.application.ports import VideoActors
from video.application.publications import require_actor
from video.domain.publications import VideoMetadata, VideoNotFound, VideoSettings, valid_identifier


class PreparedMediaError(ValueError):
    pass


class PreparedMedia(Protocol):
    async def validate(self, directory: Path) -> None: ...
    async def export(self, actor: int, video_id: int, directory: Path) -> None: ...
    async def import_video(
        self, actor: int, metadata: VideoMetadata, directory: Path, video_id: int | None = None
    ) -> VideoSettings: ...


class ExportPreparedVideo:
    def __init__(
        self, actors: VideoActors, media: PreparedMedia, transactions: Transactions
    ) -> None:
        self.actors, self.media, self.transactions = actors, media, transactions

    async def __call__(self, actor_id: int, video_id: int, directory: Path) -> None:
        actor = require_actor(actor_id)
        if not valid_identifier(video_id):
            raise VideoNotFound
        async with self.transactions.begin():
            await self.actors.require(actor)
            await self.media.export(actor, video_id, directory)


class ImportPreparedVideo:
    def __init__(
        self, actors: VideoActors, media: PreparedMedia, transactions: Transactions
    ) -> None:
        self.actors, self.media, self.transactions = actors, media, transactions

    async def __call__(
        self, actor_id: int, metadata: VideoMetadata, directory: Path, video_id: int | None = None
    ) -> VideoSettings:
        actor = require_actor(actor_id)
        normalized = metadata.normalized()
        async with self.transactions.begin():
            await self.actors.require(actor)
            return await self.media.import_video(actor, normalized, directory, video_id)


class CheckPreparedVideo:
    def __init__(self, media: PreparedMedia) -> None:
        self.media = media

    async def __call__(self, directory: Path) -> None:
        await self.media.validate(directory)
