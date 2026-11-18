"""Only a locked database role may produce a staff identity; Casbin is immutable."""

from typing import cast

import casbin  # pyright: ignore[reportMissingTypeStubs] -- upstream has no stubs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.contracts.authorization import Authorization
from common.infrastructure.authorization import MODEL, CasbinAuthorization, PolicyEngine
from user.application.staff import StaffRolePolicy
from user.domain.accounts import Role
from user.domain.staff import StaffIdentity
from user.infrastructure.models import AccountRow


class SqlStaffAccounts:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def locked(self, account_id: int) -> StaffIdentity | None:
        row = (
            await self.session.execute(
                select(AccountRow.id, AccountRow.role)
                .where(AccountRow.id == account_id)
                .with_for_update(read=True)
            )
        ).one_or_none()
        return StaffIdentity(row[0], Role(row[1])) if row is not None else None


class CasbinStaffPolicy:
    def __init__(self, authorization: Authorization) -> None:
        self.authorization = authorization

    def allows(self, role: Role) -> bool:
        return self.authorization.allows(role.value, "staff", "act")


def create_staff_policy() -> StaffRolePolicy:
    model = casbin.Model()
    model.load_model_from_text(MODEL)  # pyright: ignore[reportUnknownMemberType]
    engine = casbin.Enforcer(model)
    for role in (Role.MODERATOR, Role.ADMIN):
        engine.add_policy(role.value, "staff", "act")  # pyright: ignore[reportUnknownMemberType]
    return CasbinStaffPolicy(CasbinAuthorization(cast(PolicyEngine, engine)))
