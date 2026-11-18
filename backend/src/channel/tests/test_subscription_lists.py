"""Subscribed channel boundaries are account-bound and distinct from video cursors."""

import base64
from datetime import UTC, datetime

import pytest

from channel.domain.subscription_lists import TOKEN, InvalidSubscriptionCursor, SubscriptionBoundary


@pytest.mark.parametrize(
    "date",
    [
        datetime(1, 1, 1, tzinfo=UTC),
        datetime(2026, 11, 18, 12, 1, 2, 123456, tzinfo=UTC),
        datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
    ],
)
def test_subscribed_channel_boundary(date):
    boundary = SubscriptionBoundary(date, 2**63 - 1)
    token = boundary.encode(2**63 - 2)
    assert len(token) == 35 and SubscriptionBoundary.decode(token, 2**63 - 2) == boundary
    for malformed in ("", token + "=", token[:-1], "x" * 10000, "' OR TRUE; --"):
        with pytest.raises(InvalidSubscriptionCursor):
            SubscriptionBoundary.decode(malformed, 2**63 - 2)
    with pytest.raises(InvalidSubscriptionCursor):
        SubscriptionBoundary.decode(token, 1)


@pytest.mark.parametrize(
    "parts",
    [
        (0, 67, 1, 0, 1),
        (1, 76, 1, 0, 1),
        (1, 67, 1, 2**63 - 1, 1),
        (1, 67, 1, 0, 0),
        (1, 67, 1, 0, 2**63),
    ],
)
def test_subscribed_channel_cursor_rejects_forged_fields(parts):
    token = base64.urlsafe_b64encode(TOKEN.pack(*parts)).decode().rstrip("=")
    with pytest.raises(InvalidSubscriptionCursor):
        SubscriptionBoundary.decode(token, 1)
