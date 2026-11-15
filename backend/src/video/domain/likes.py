"""A like is an active pair; totals never grant access to the underlying video."""

from dataclasses import dataclass


class InvalidLike(Exception):
    pass


@dataclass(frozen=True)
class LikeTotals:
    liked: bool
    like_count: int


@dataclass(frozen=True)
class LikeState:
    liked: bool
    like_count: int
    can_like: bool
