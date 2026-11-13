"""Atomic registration, depending on account creation through a port."""

from typing import Protocol

from channel.domain.registration import Registered, Registration, validate_tag
from common.contracts.transactions import Transactions


class AccountCreator(Protocol):
    async def create(self, data: Registration) -> int: ...


class Channels(Protocol):
    async def create(self, owner_id: int, tag: str) -> Registered: ...


class Register:
    def __init__(
        self, accounts: AccountCreator, channels: Channels, transactions: Transactions
    ) -> None:
        self.accounts = accounts
        self.channels = channels
        self.transactions = transactions

    async def __call__(self, data: Registration) -> Registered:
        tag = validate_tag(data.tag)
        async with self.transactions.begin():
            account_id = await self.accounts.create(data)
            return await self.channels.create(account_id, tag)
