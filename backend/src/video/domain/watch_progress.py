"""Last accepted main-player position, not a maximum or viewing history."""

import math
from dataclasses import dataclass
from datetime import datetime


class InvalidWatchProgress(Exception):
    pass


def position_seconds(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidWatchProgress
    try:
        position = float(value)
    except OverflowError:
        raise InvalidWatchProgress from None
    if not math.isfinite(position) or position < 0:
        raise InvalidWatchProgress
    return position


@dataclass(frozen=True)
class WatchProgress:
    position_seconds: float
    duration_seconds: float
    updated_at: datetime
