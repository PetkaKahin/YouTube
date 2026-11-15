"""Bounded channel scans, public names through User.public, no foreign joins."""

from datetime import datetime

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.profiles import PublicChannel
from channel.domain.search import ChannelSearchHit, PlaylistSearchHit
from channel.infrastructure.models import ChannelRow, PlaylistRow
from channel.infrastructure.playlists import value
from user.public import GetPublicNames


class SqlChannelSearch:
    def __init__(self, session: AsyncSession, names: GetPublicNames) -> None:
        self.session, self.names = session, names

    async def find(
        self,
        query: str,
        before_at: datetime | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[ChannelSearchHit, ...]:
        hits: list[ChannelSearchHit] = []
        while True:
            statement = select(
                ChannelRow.id, ChannelRow.owner_id, ChannelRow.tag, ChannelRow.created_at
            )
            if before_at is not None:
                statement = (
                    statement.where(ChannelRow.created_at <= before_at)
                    if before_id is None
                    else statement.where(
                        tuple_(ChannelRow.created_at, ChannelRow.id) < (before_at, before_id)
                    )
                )
            if since is not None:
                statement = statement.where(
                    ChannelRow.created_at >= since, ChannelRow.created_at <= until
                )
            rows = (
                await self.session.execute(
                    statement.order_by(ChannelRow.created_at.desc(), ChannelRow.id.desc()).limit(21)
                )
            ).all()
            names = await self.names(tuple(row.owner_id for row in rows))
            for row in rows:
                name = names.get(row.owner_id)
                if name is not None and (
                    query.lower() in name.lower() or query.lower() in ("@" + row.tag).lower()
                ):
                    hits.append(ChannelSearchHit(row.id, name, row.tag, row.created_at))
                if len(hits) == 21:
                    return tuple(hits)
            if len(rows) < 21:
                return tuple(hits)
            before_at, before_id = rows[-1].created_at, rows[-1].id


class SqlPlaylistSearch:
    def __init__(self, session: AsyncSession, names: GetPublicNames) -> None:
        self.session, self.names = session, names

    async def find(
        self,
        query: str,
        sort: str,
        before_at: datetime | None,
        before_id: int | None,
        since: datetime | None,
        until: datetime | None,
    ) -> tuple[PlaylistSearchHit, ...]:
        column = PlaylistRow.created_at if sort == "created" else PlaylistRow.updated_at
        pattern = "%" + query.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
        statement = (
            select(PlaylistRow, ChannelRow.owner_id, ChannelRow.tag)
            .join(ChannelRow, ChannelRow.id == PlaylistRow.channel_id)
            .where(PlaylistRow.visibility == "public", PlaylistRow.title.ilike(pattern, escape="!"))
        )
        if since is not None:
            statement = statement.where(
                PlaylistRow.created_at >= since, PlaylistRow.created_at <= until
            )
        hits: list[PlaylistSearchHit] = []
        while True:
            page = statement
            if before_at is not None:
                page = (
                    page.where(column <= before_at)
                    if before_id is None
                    else page.where(tuple_(column, PlaylistRow.id) < (before_at, before_id))
                )
            rows = (
                await self.session.execute(
                    page.order_by(column.desc(), PlaylistRow.id.desc()).limit(21)
                )
            ).all()
            names = await self.names(tuple(dict.fromkeys(owner for _, owner, _ in rows)))
            for playlist, owner, tag in rows:
                name = names.get(owner)
                if name is not None:
                    hits.append(
                        PlaylistSearchHit(
                            value(playlist), PublicChannel(playlist.channel_id, name, tag)
                        )
                    )
                if len(hits) == 21:
                    return tuple(hits)
            if len(rows) < 21:
                return tuple(hits)
            last = rows[-1][0]
            before_at, before_id = (
                (last.created_at if sort == "created" else last.updated_at),
                last.id,
            )
