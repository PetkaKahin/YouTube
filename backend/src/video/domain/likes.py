"""A pair has one active reaction; only like totals are exposed."""

from dataclasses import dataclass


class InvalidLike(Exception):
    pass


@dataclass(frozen=True)
class LikeTotals:
    liked: bool
    like_count: int
    disliked: bool


@dataclass(frozen=True)
class LikeState:
    liked: bool
    like_count: int
    can_like: bool
    disliked: bool
    can_dislike: bool
