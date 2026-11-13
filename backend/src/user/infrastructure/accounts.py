"""PostgreSQL account repository. Never commits its caller's transaction."""

from psycopg import Error as PsycopgError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from user.domain.accounts import Account, AccountError, Credentials, Role
from user.infrastructure.models import AccountRow


def account_value(row: AccountRow) -> Account:
    return Account(row.id, row.name, row.email, Role(row.role), row.created_at)


class SqlAccounts:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, name: str, email: str, password_hash: str) -> Account:
        row = AccountRow(name=name, email=email, password_hash=password_hash, role=Role.USER.value)
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as error:
            if (
                isinstance(error.orig, PsycopgError)
                and error.orig.diag.constraint_name == "uq_user_accounts_email"
            ):
                raise AccountError("email", "already_taken") from None
            raise
        return account_value(row)

    async def credentials(self, email: str) -> Credentials | None:
        row = await self.session.scalar(select(AccountRow).where(AccountRow.email == email))
        return Credentials(account_value(row), row.password_hash) if row is not None else None

    async def get(self, account_id: int) -> Account | None:
        row = await self.session.get(AccountRow, account_id, populate_existing=True)
        return account_value(row) if row is not None else None

    async def set_role(self, account_id: int, role: Role) -> Account:
        row = (
            await self.session.scalars(
                update(AccountRow)
                .where(AccountRow.id == account_id)
                .values(role=role.value)
                .returning(AccountRow)
            )
        ).one()
        return account_value(row)

    async def role_by_email(self, email: str) -> Role | None:
        value = await self.session.scalar(select(AccountRow.role).where(AccountRow.email == email))
        return Role(value) if value is not None else None
