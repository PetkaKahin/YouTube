"""Batch public profiles without exposing account details or owning transactions."""

from typing import Protocol

from channel.domain.profiles import PublicChannel


class PublicChannels(Protocol):
    async def get(self, owner_ids: tuple[int, ...]) -> dict[int, PublicChannel]: ...


class GetPublicChannels:
    def __init__(self, channels: PublicChannels) -> None:
        self.channels = channels

    async def __call__(self, owner_ids: tuple[int, ...]) -> dict[int, PublicChannel]:
        if any(type(value) is not int or not 0 < value <= 2**63 - 1 for value in owner_ids):
            raise ValueError("Invalid owner identifiers")
        return await self.channels.get(tuple(dict.fromkeys(owner_ids))) if owner_ids else {}
