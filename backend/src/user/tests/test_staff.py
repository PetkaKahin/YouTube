"""Staff authority uses a fresh role and holds its real PostgreSQL account lock."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from user.public import (
    AccountNotFound,
    CreateAccount,
    GetStaffIdentity,
    NewAccount,
    SetRole,
    StaffForbidden,
)


@pytest.mark.parametrize("role", ["user", "moderator", "admin"])
def test_role_membership_is_server_owned_and_changes_without_a_cache(backend_runtime, role):
    async def exercise():
        async with backend_runtime() as (container, _):
            async with container() as operation:
                async with (await operation.get(AsyncSession)).begin():
                    account = await (await operation.get(CreateAccount))(
                        NewAccount("Автор", uuid4().hex + "@example.com", "abcdef", "abcdef")
                    )
            async with container() as operation:
                await (await operation.get(SetRole))(account.email, role)
            async with container() as operation:
                session = await operation.get(AsyncSession)
                async with session.begin():
                    if role == "user":
                        with pytest.raises(StaffForbidden):
                            await (await operation.get(GetStaffIdentity))(account.id)
                    else:
                        result = await (await operation.get(GetStaffIdentity))(account.id)
                        assert result.id == account.id and result.role.value == role
            async with container() as operation:
                await (await operation.get(SetRole))(account.email, "user")
            async with container() as operation:
                async with (await operation.get(AsyncSession)).begin():
                    with pytest.raises(StaffForbidden):
                        await (await operation.get(GetStaffIdentity))(account.id)

    asyncio.run(exercise())


@pytest.mark.parametrize("value", [None, True, False, 0, -1, 2**63, 2**62])
def test_missing_or_forged_actor_cannot_produce_staff_identity(backend_runtime, value):
    async def exercise():
        async with backend_runtime() as (container, _):
            async with container() as operation:
                async with (await operation.get(AsyncSession)).begin():
                    with pytest.raises(AccountNotFound):
                        await (await operation.get(GetStaffIdentity))(value)

    asyncio.run(exercise())
