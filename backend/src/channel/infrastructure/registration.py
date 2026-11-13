"""Channel persistence and an adapter calling only User.public."""

from psycopg import Error as PsycopgError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from channel.domain.registration import Registered, Registration, RegistrationError
from channel.infrastructure.models import ChannelRow
from user.public import AccountError, CreateAccount, NewAccount


class UserAccountCreator:
    def __init__(self, create_account: CreateAccount) -> None:
        self.create_account = create_account

    async def create(self, data: Registration) -> int:
        try:
            account = await self.create_account(
                NewAccount(data.name, data.email, data.password, data.password_confirmation)
            )
        except AccountError as error:
            raise RegistrationError(error.field, error.code) from None
        return account.id


class SqlChannels:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, owner_id: int, tag: str) -> Registered:
        row = ChannelRow(owner_id=owner_id, tag=tag)
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as error:
            if (
                isinstance(error.orig, PsycopgError)
                and error.orig.diag.constraint_name == "uq_channel_profiles_tag"
            ):
                raise RegistrationError("tag", "already_taken") from None
            raise
        return Registered(owner_id, row.id, row.tag, row.created_at)
