"""Plain account scenarios. CreateAccount participates in its caller's transaction."""

from common.contracts.transactions import Transactions
from user.application.ports import Accounts, EmailNormalizer, Passwords
from user.domain.accounts import (
    Account,
    AccountError,
    AccountNotFound,
    InvalidCredentials,
    NewAccount,
    Role,
    validate_name,
    validate_password,
)


class NormalizeEmail:
    def __init__(self, emails: EmailNormalizer) -> None:
        self.emails = emails

    def __call__(self, email: str) -> str:
        return self.emails.normalize(email)


class CreateAccount:
    def __init__(self, accounts: Accounts, emails: EmailNormalizer, passwords: Passwords) -> None:
        self.accounts = accounts
        self.emails = emails
        self.passwords = passwords

    async def __call__(self, data: NewAccount) -> Account:
        name = validate_name(data.name)
        validate_password(data.password, data.password_confirmation)
        email = self.emails.normalize(data.email)
        password_hash = await self.passwords.hash(data.password)
        return await self.accounts.create(name, email, password_hash)


class Authenticate:
    def __init__(self, accounts: Accounts, emails: EmailNormalizer, passwords: Passwords) -> None:
        self.accounts = accounts
        self.emails = emails
        self.passwords = passwords

    async def __call__(self, email: str, password: str) -> Account:
        if not 6 <= len(password) <= 128:
            raise InvalidCredentials
        try:
            normalized = self.emails.normalize(email)
        except AccountError:
            raise InvalidCredentials from None
        credentials = await self.accounts.credentials(normalized)
        valid = await self.passwords.verify(
            password, credentials.password_hash if credentials else None
        )
        if not valid or credentials is None:
            raise InvalidCredentials
        return credentials.account


class GetAccount:
    def __init__(self, accounts: Accounts) -> None:
        self.accounts = accounts

    async def __call__(self, account_id: int) -> Account:
        account = await self.accounts.get(account_id)
        if account is None:
            raise AccountNotFound
        return account


class GetRole:
    def __init__(self, accounts: Accounts, emails: EmailNormalizer) -> None:
        self.accounts = accounts
        self.emails = emails

    async def __call__(self, email: str) -> Role:
        role = await self.accounts.role_by_email(self.emails.normalize(email))
        if role is None:
            raise AccountNotFound
        return role


class SetRole:
    def __init__(
        self, accounts: Accounts, emails: EmailNormalizer, transactions: Transactions
    ) -> None:
        self.accounts = accounts
        self.emails = emails
        self.transactions = transactions

    async def __call__(self, email: str, role: str) -> Account:
        try:
            selected = Role(role)
        except ValueError:
            raise AccountError("role", "invalid_role") from None
        normalized = self.emails.normalize(email)
        async with self.transactions.begin():
            credentials = await self.accounts.credentials(normalized)
            if credentials is None:
                raise AccountNotFound
            return await self.accounts.set_role(credentials.account.id, selected)
