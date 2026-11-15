"""Only public channel identity is used by video cards."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PublicChannel:
    id: int
    name: str
    tag: str
