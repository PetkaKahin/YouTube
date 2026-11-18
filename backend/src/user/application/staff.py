"""A caller-owned transaction keeps the trusted role stable until its action commits."""

from typing import Protocol

from user.domain.accounts import AccountNotFound, Role
from user.domain.staff import StaffForbidden, StaffIdentity


class StaffAccounts(Protocol):
    async def locked(self, account_id: int) -> StaffIdentity | None: ...


class StaffRolePolicy(Protocol):
    def allows(self, role: Role) -> bool: ...


class GetStaffIdentity:
    def __init__(self, accounts: StaffAccounts, policy: StaffRolePolicy) -> None:
        self.accounts, self.policy = accounts, policy

    async def __call__(self, account_id: int | None) -> StaffIdentity:
        if type(account_id) is not int or not 0 < account_id < 2**63:
            raise AccountNotFound
        identity = await self.accounts.locked(account_id)
        if identity is None:
            raise AccountNotFound
        if not self.policy.allows(identity.role):
            raise StaffForbidden
        return identity
