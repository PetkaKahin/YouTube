"""Numeric boundaries cannot store booleans, non-finite values or overflowed integers."""

import pytest

from video.domain.watch_progress import InvalidWatchProgress, position_seconds


@pytest.mark.parametrize(
    "value", [-1, float("nan"), float("inf"), -float("inf"), 10**400, True, "1", None, {}]
)
def test_invalid_position(value):
    with pytest.raises(InvalidWatchProgress):
        position_seconds(value)


@pytest.mark.parametrize("value", [0, 0.125, 3600, 1e308])
def test_finite_nonnegative_position(value):
    assert position_seconds(value) == value
