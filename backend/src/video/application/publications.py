"""Owner-only metadata scenarios; callers provide a trusted actor, never an owner field."""

from common.contracts.transactions import Transactions
from video.application.ports import Publications, VideoActors
from video.domain.delivery import PublicationError
from video.domain.own_videos import SIZE, InvalidVideoList, OwnedVideoBoundary, VideoSettingsPage
from video.domain.publications import (
    AuthenticationRequired,
    PublicationState,
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


class GetMyVideos:
    def __init__(
        self, actors: VideoActors, publications: Publications, transactions: Transactions
    ) -> None:
        self.actors, self.publications, self.transactions = actors, publications, transactions

    async def __call__(
        self, account_id: int | None, publication: str = "published", cursor: str | None = None
    ) -> VideoSettingsPage:
        actor = require_actor(account_id)
        async with self.transactions.begin():
            await self.actors.require(actor)
            try:
                state = PublicationState(publication)
            except ValueError, TypeError:
                raise InvalidVideoList from None
            after = OwnedVideoBoundary.decode(cursor, actor, state) if cursor is not None else None
            rows = await self.publications.list_owned(actor, state, after)
            return VideoSettingsPage(
                rows[:SIZE],
                OwnedVideoBoundary(rows[SIZE - 1].created_at, rows[SIZE - 1].id).encode(
                    actor, state
                )
                if len(rows) > SIZE
                else None,
            )


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
