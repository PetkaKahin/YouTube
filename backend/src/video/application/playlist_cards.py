"""Narrow playlist integration: owned edits and batched ordinary-view cards."""

from typing import Protocol

from video.application.access import ViewerIdentities, ViewingPolicy
from video.application.feed import FeedAuthors
from video.application.ports import Publications, VideoActors
from video.application.publications import require_actor
from video.domain.access import ViewConditions, Viewer
from video.domain.feed import FeedCandidate, FeedCard
from video.domain.publications import VideoNotFound, valid_identifier


class PlaylistCardViews(Protocol):
    async def candidates(
        self, ids: tuple[int, ...], viewer: Viewer
    ) -> tuple[tuple[FeedCandidate, ViewConditions], ...]: ...


class PlaylistVideoOwnership:
    def __init__(self, actors: VideoActors, publications: Publications) -> None:
        self.actors, self.publications = actors, publications

    async def lock(self, account_id: int, video_id: int) -> None:
        actor = require_actor(account_id)
        await self.actors.require(actor)
        if (
            not valid_identifier(video_id)
            or await self.publications.owned(video_id, actor, lock=True) is None
        ):
            raise VideoNotFound

    async def touch(self, account_id: int, video_id: int) -> None:
        await self.lock(account_id, video_id)
        await self.publications.touch(video_id, account_id)


class GetPlaylistVideoCards:
    def __init__(
        self,
        views: PlaylistCardViews,
        viewers: ViewerIdentities,
        policy: ViewingPolicy,
        authors: FeedAuthors,
    ) -> None:
        self.views, self.viewers, self.policy, self.authors = views, viewers, policy, authors

    async def __call__(self, account_id: int | None, ids: tuple[int, ...]) -> dict[int, FeedCard]:
        if any(not valid_identifier(value) for value in ids):
            raise VideoNotFound
        viewer = (
            await self.viewers.get(require_actor(account_id))
            if account_id is not None
            else Viewer()
        )
        candidates = await self.views.candidates(tuple(dict.fromkeys(ids)), viewer) if ids else ()
        allowed = [
            candidate
            for candidate, conditions in candidates
            if self.policy.allows(viewer, conditions)
        ]
        authors = await self.authors.get(tuple(dict.fromkeys(item.owner_id for item in allowed)))
        return {
            item.position.id: FeedCard(
                item.position.id,
                item.title,
                item.position.added_at,
                item.duration_seconds,
                authors[item.owner_id],
                item.preview_ready,
            )
            for item in allowed
            if item.owner_id in authors
        }
