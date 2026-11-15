"""Owner-only metadata scenarios; callers provide a trusted actor, never an owner field."""

from common.contracts.transactions import Transactions
from video.application.ports import Publications, VideoActors
from video.domain.delivery import PublicationError
from video.domain.publications import (
    AuthenticationRequired,
    VideoMetadata,
    VideoNotFound,
    VideoSettings,
    valid_identifier,
)


def require_actor(account_id: int | None) -> int:
    if account_id is None or not valid_identifier(account_id):
        raise AuthenticationRequired
    return account_id


class CreateDraft:
    def __init__(
        self, actors: VideoActors, publications: Publications, transactions: Transactions
    ) -> None:
        self.actors = actors
        self.publications = publications
        self.transactions = transactions

    async def __call__(self, account_id: int | None, metadata: VideoMetadata) -> VideoSettings:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            normalized = metadata.normalized()
            return await self.publications.create(actor, normalized)


class GetVideoSettings:
    def __init__(
        self, actors: VideoActors, publications: Publications, transactions: Transactions
    ) -> None:
        self.actors = actors
        self.publications = publications
        self.transactions = transactions

    async def __call__(self, account_id: int | None, video_id: int) -> VideoSettings:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            if not valid_identifier(video_id):
                raise VideoNotFound
            value = await self.publications.owned(video_id, actor)
            if value is None:
                raise VideoNotFound
            return value


class EditVideoSettings:
    def __init__(
        self, actors: VideoActors, publications: Publications, transactions: Transactions
    ) -> None:
        self.actors = actors
        self.publications = publications
        self.transactions = transactions

    async def __call__(
        self, account_id: int | None, video_id: int, metadata: VideoMetadata
    ) -> VideoSettings:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            if not valid_identifier(video_id):
                raise VideoNotFound
            current = await self.publications.owned(video_id, actor, lock=True)
            if current is None:
                raise VideoNotFound
            edited = current.edit(metadata.normalized())
            if edited == current:
                return current
            return await self.publications.update_metadata(edited)


class PublishVideo:
    def __init__(
        self, actors: VideoActors, publications: Publications, transactions: Transactions
    ) -> None:
        self.actors, self.publications, self.transactions = actors, publications, transactions

    async def __call__(self, account_id: int | None, video_id: int) -> VideoSettings:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            if not valid_identifier(video_id):
                raise VideoNotFound
            current = await self.publications.owned(video_id, actor, lock=True)
            if current is None:
                raise VideoNotFound
            if current.upload.value != "uploaded":
                raise PublicationError("upload_not_completed")
            if current.publication.value == "published":
                return current
            return await self.publications.publish(video_id, actor)
