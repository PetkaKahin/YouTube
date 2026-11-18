"""Own-table integrity and aggregation on a migrated real PostgreSQL database."""

import asyncio

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from comment.domain.comments import CommentSort
from comment.infrastructure.comments import SqlComments
from comment.infrastructure.models import CommentLikeRow, CommentRow
from user.public import CreateAccount, NewAccount
from video.public import CreateDraft, VideoMetadata


@pytest.fixture(scope="module")
def identities(backend_runtime):
    async def seed():
        async with backend_runtime() as (container, _):
            async with container() as operation:
                async with (await operation.get(AsyncSession)).begin():
                    author = await (await operation.get(CreateAccount))(
                        NewAccount("Автор", "comment-storage@example.com", "abcdef", "abcdef")
                    )
            async with container() as operation:
                video = await (await operation.get(CreateDraft))(
                    author.id, VideoMetadata("storage", "", "private")
                )
            return author.id, video.id

    return asyncio.run(seed())


@pytest.mark.parametrize("value", ["", " \t\n\r", "a" * 2001])
def test_database_also_rejects_empty_whitespace_and_oversize(backend_runtime, identities, value):
    async def exercise():
        author, video = identities
        async with backend_runtime() as (_, resources):
            with pytest.raises(IntegrityError):
                async with resources.sessions.begin() as session:
                    await session.execute(
                        insert(CommentRow).values(video_id=video, author_id=author, text=value)
                    )

    asyncio.run(exercise())


def test_guest_zero_like_regression_pairs_foreign_keys_and_exact_text(backend_runtime, identities):
    async def exercise():
        author, video = identities
        async with backend_runtime() as (_, resources):
            async with resources.sessions.begin() as session:
                store = SqlComments(session)
                entry = await store.create(video, author, "🙂" * 2000)
                assert entry.text == "🙂" * 2000
                for viewer in (None, author):
                    state = await store.get(video, entry.id, viewer)
                    assert state is not None and not state.liked and state.like_count == 0
                    for sort in CommentSort:
                        rows = await store.page(video, viewer, sort, None)
                        assert not any(row.liked for row in rows)
                await store.set_like(entry.id, author, True)
                await store.set_like(entry.id, author, True)
                assert (await store.get(video, entry.id, author)).like_count == 1
                assert not (await store.get(video, entry.id, None)).liked
                assert await store.get(2**62, entry.id, author) is None
            for comment_id, account_id in ((entry.id, author), (2**62, author), (entry.id, 2**62)):
                with pytest.raises(IntegrityError):
                    async with resources.sessions.begin() as session:
                        await session.execute(
                            insert(CommentLikeRow).values(
                                comment_id=comment_id, account_id=account_id
                            )
                        )
            for video_id, account_id in ((2**62, author), (video, 2**62)):
                with pytest.raises(IntegrityError):
                    async with resources.sessions.begin() as session:
                        await session.execute(
                            insert(CommentRow).values(
                                video_id=video_id, author_id=account_id, text="foreign"
                            )
                        )
            async with resources.sessions.begin() as session:
                store = SqlComments(session)
                await store.set_like(entry.id, author, False)
                await store.set_like(entry.id, author, False)
                assert (await store.get(video, entry.id, None)).like_count == 0

    asyncio.run(exercise())
