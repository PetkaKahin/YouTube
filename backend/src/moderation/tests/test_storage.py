"""Database constraints protect pairs, revisions, reason enums and audit references."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from moderation.domain.reports import Reason
from moderation.infrastructure.models import DecisionRow, ReportRow, ReviewAccessRow
from moderation.infrastructure.reports import SqlReports
from user.public import CreateAccount, NewAccount
from video.public import CreateDraft, VideoMetadata


@pytest.fixture(scope="module")
def identities(backend_runtime):
    async def seed():
        async with backend_runtime() as (container, resources):
            async with container() as operation:
                async with (await operation.get(AsyncSession)).begin():
                    account = await (await operation.get(CreateAccount))(
                        NewAccount("Автор", uuid4().hex + "@example.com", "abcdef", "abcdef")
                    )
            async with container() as operation:
                video = await (await operation.get(CreateDraft))(
                    account.id, VideoMetadata("report-storage", "", "private")
                )
            async with resources.sessions.begin() as session:
                await SqlReports(session).submit(account.id, video.id, Reason.OTHER)
                report_id = await session.scalar(
                    select(ReportRow.id).where(ReportRow.video_id == video.id)
                )
            return account.id, video.id, report_id

    return asyncio.run(seed())


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "arbitrary"},
        {"revision": 0},
        {"state": "invalid"},
        {"video_id": 2**62},
        {"reporter_id": 2**62},
        {},
    ],
)
def test_invalid_or_duplicate_report_fails_at_database(backend_runtime, identities, changes):
    async def exercise():
        account, video, _ = identities
        async with backend_runtime() as (_, resources):
            with pytest.raises(IntegrityError):
                async with resources.sessions.begin() as session:
                    await session.execute(
                        insert(ReportRow).values(
                            {
                                "reporter_id": account,
                                "video_id": video,
                                "reason": "other",
                                **changes,
                            }
                        )
                    )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": None},
        {"reason": "free text"},
        {"revision": 0},
        {"action": "restore"},
        {"actor_id": 2**62},
        {"report_id": 2**62},
        {"action": "dismiss", "reason": "other"},
    ],
)
def test_invalid_decision_fails_at_database(backend_runtime, identities, changes):
    async def exercise():
        account, _, report = identities
        async with backend_runtime() as (_, resources):
            with pytest.raises(IntegrityError):
                async with resources.sessions.begin() as session:
                    await session.execute(
                        insert(DecisionRow).values(
                            {
                                "report_id": report,
                                "revision": 1,
                                "actor_id": account,
                                "action": "hide",
                                "reason": "other",
                                **changes,
                            }
                        )
                    )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "changes",
    [
        {"reason": "free text"},
        {"revision": 0},
        {"resource": "arbitrary-url"},
        {"actor_id": 2**62},
        {"report_id": 2**62},
    ],
)
def test_invalid_access_evidence_fails_at_database(backend_runtime, identities, changes):
    async def exercise():
        account, _, report = identities
        async with backend_runtime() as (_, resources):
            with pytest.raises(IntegrityError):
                async with resources.sessions.begin() as session:
                    await session.execute(
                        insert(ReviewAccessRow).values(
                            {
                                "report_id": report,
                                "revision": 1,
                                "actor_id": account,
                                "resource": "video",
                                "reason": "other",
                                **changes,
                            }
                        )
                    )

    asyncio.run(exercise())
