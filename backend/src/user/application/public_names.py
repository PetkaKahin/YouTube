"""Public display names participate in their caller's read transaction."""

from typing import Protocol


class PublicNames(Protocol):
    async def get(self, account_ids: tuple[int, ...]) -> dict[int, str]: ...


class GetPublicNames:
    def __init__(self, names: PublicNames) -> None:
        self.names = names

    async def __call__(self, account_ids: tuple[int, ...]) -> dict[int, str]:
        if any(type(value) is not int or not 0 < value <= 2**63 - 1 for value in account_ids):
            raise ValueError("Invalid account identifiers")
        return await self.names.get(tuple(dict.fromkeys(account_ids))) if account_ids else {}
