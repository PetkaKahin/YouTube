"""Canonical owner/list-scoped boundaries preserve int64 and datetime precision."""

import base64
from datetime import UTC, datetime

import pytest

from video.domain.feed import FeedPosition
from video.domain.own_videos import TOKEN, InvalidVideoList, OwnedVideoBoundary
from video.domain.publications import PublicationState


@pytest.mark.parametrize(
    "when",
    [
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(2026, 11, 16, 12, 30, 0, 123456, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
@pytest.mark.parametrize("publication", list(PublicationState))
def test_boundary_roundtrip_precision_and_scope(when, publication):
    boundary = OwnedVideoBoundary(when, 2**63 - 1)
    owner = 2**63 - 2
    token = boundary.encode(owner, publication)
    assert len(token) == 36
    assert OwnedVideoBoundary.decode(token, owner, publication) == boundary
    for candidate, actor, state in (
        (token, owner - 1, publication),
        (token, owner, next(value for value in PublicationState if value != publication)),
        (token + "=", owner, publication),
        (token[:-1], owner, publication),
        (FeedPosition(when, boundary.id).encode(), owner, publication),
        ("x" * 10000, owner, publication),
        ("../private?token=secret", owner, publication),
    ):
        with pytest.raises(InvalidVideoList):
            OwnedVideoBoundary.decode(candidate, actor, state)


@pytest.mark.parametrize(
    "parts",
    [
        (0, 86, 1, 1, 0, 1),
        (1, 85, 1, 1, 0, 1),
        (1, 86, 0, 1, 0, 1),
        (1, 86, 1, 1, 2**63 - 1, 1),
        (1, 86, 1, 1, 0, 0),
        (1, 86, 1, 1, 0, 2**63),
    ],
)
def test_forged_boundary_versions_purpose_dates_and_ids(parts):
    token = base64.urlsafe_b64encode(TOKEN.pack(*parts)).decode()
    with pytest.raises(InvalidVideoList):
        OwnedVideoBoundary.decode(token, 1, PublicationState.DRAFT)
