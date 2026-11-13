"""Ports for account persistence, email syntax and password hashing."""

from typing import Protocol

from user.domain.accounts import Account, Credentials, Role


class Accounts(Protocol):
    async def create(self, name: str, email: str, password_hash: str) -> Account: ...

    async def credentials(self, email: str) -> Credentials | None: ...

    async def get(self, account_id: int) -> Account | None: ...

    async def role_by_email(self, email: str) -> Role | None: ...

    async def set_role(self, account_id: int, role: Role) -> Account: ...


class EmailNormalizer(Protocol):
    def normalize(self, value: str) -> str: ...


class Passwords(Protocol):
    async def hash(self, password: str) -> str: ...

    async def verify(self, password: str, password_hash: str | None) -> bool: ...
