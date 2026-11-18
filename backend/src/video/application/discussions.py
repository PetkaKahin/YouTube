"""Discussion access participates in its caller's transaction and holds visibility stable."""

from typing import Protocol

from video.application.access import VideoAccess
from video.domain.publications import VideoNotFound, valid_identifier


class DiscussionPublications(Protocol):
    async def lock(self, video_id: int) -> bool: ...


class RequireDiscussionVideo:
    def __init__(self, access: VideoAccess, publications: DiscussionPublications) -> None:
        self.access, self.publications = access, publications

    async def __call__(self, account_id: int | None, video_id: int) -> None:
        viewer = await self.access.viewer(account_id)
        if not valid_identifier(video_id) or not await self.publications.lock(video_id):
            raise VideoNotFound
        video = await self.access.require(viewer, video_id)
        if video.visibility != "public":
            raise VideoNotFound
