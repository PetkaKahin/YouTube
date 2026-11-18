"""Own-table keyset sources; subscribed owners come from Channel.public."""

from datetime import datetime
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from channel.public import GetSubscribedOwners, SubscriptionAuthenticationRequired
from video.domain.library import SIZE, LibraryBoundary, LibraryCandidate, LibraryKind
from video.domain.publications import AuthenticationRequired

SOURCES = {
    LibraryKind.SUBSCRIPTIONS: (
        "p.added_at",
        "",
        "AND p.visibility='public' AND p.owner_id=ANY(:owners)",
    ),
    LibraryKind.LIKED: (
        "x.created_at",
        "JOIN video_likes x ON x.video_id=p.id",
        "AND x.account_id=:actor AND x.reaction='like'",
    ),
    LibraryKind.HISTORY: (
        "x.updated_at",
        "JOIN video_watch_progress x ON x.video_id=p.id",
        "AND x.account_id=:actor",
    ),
}
QUERIES = {
    kind: text(f"""
SELECT p.id,p.owner_id,{date} AS date
FROM video_publications p {join}
WHERE p.publication='published' AND p.moderation='visible' AND p.added_at IS NOT NULL
 {scope}
 AND (CAST(:date AS timestamptz) IS NULL OR
      ({date},p.id)<(CAST(:date AS timestamptz),CAST(:id AS bigint)))
ORDER BY {date} DESC,p.id DESC LIMIT :limit
""")  # noqa: S608 -- fixed module fragments, all request values bound
    for kind, (date, join, scope) in SOURCES.items()
}


class SqlLibraryCandidates:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def scan(
        self,
        actor: int,
        kind: LibraryKind,
        after: LibraryBoundary | None,
        owner_ids: tuple[int, ...],
    ) -> tuple[LibraryCandidate, ...]:
        rows = (
            await self.session.execute(
                QUERIES[kind],
                {
                    "actor": actor,
                    "date": after.date if after else None,
                    "id": after.id if after else None,
                    "limit": SIZE + 1,
                    "owners": list(owner_ids),
                },
            )
        ).mappings()
        return tuple(
            LibraryCandidate(
                LibraryBoundary(cast(datetime, row["date"]), cast(int, row["id"])),
                cast(int, row["owner_id"]),
            )
            for row in rows
        )


class ChannelSubscribedOwners:
    def __init__(self, subscriptions: GetSubscribedOwners) -> None:
        self.subscriptions = subscriptions

    async def get(self, actor: int) -> tuple[int, ...]:
        try:
            return await self.subscriptions(actor)
        except SubscriptionAuthenticationRequired:
            raise AuthenticationRequired from None
