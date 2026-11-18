"""Request-scoped account adapters and plain application scenarios."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from user.application.accounts import (
    Authenticate,
    CreateAccount,
    GetAccount,
    GetRole,
    NormalizeEmail,
    SetRole,
)
from user.application.ports import Accounts, EmailNormalizer, Passwords
from user.application.public_names import GetPublicNames, PublicNames
from user.application.staff import GetStaffIdentity, StaffAccounts, StaffRolePolicy
from user.infrastructure.accounts import SqlAccounts
from user.infrastructure.public_names import SqlPublicNames
from user.infrastructure.security import ArgonPasswords, StandardEmail
from user.infrastructure.staff import SqlStaffAccounts, create_staff_policy


class UserProvider(Provider):
    scope = Scope.REQUEST

    accounts = provide(SqlAccounts, provides=Accounts)
    public_names = provide(SqlPublicNames, provides=PublicNames)
    get_public_names = provide(GetPublicNames)
    emails = provide(StandardEmail, provides=EmailNormalizer, scope=Scope.APP)
    passwords = provide(ArgonPasswords, provides=Passwords, scope=Scope.APP)
    create_account = provide(CreateAccount)
    authenticate = provide(Authenticate)
    get_account = provide(GetAccount)
    normalize_email = provide(NormalizeEmail)
    get_role = provide(GetRole)
    set_role = provide(SetRole)
    staff_accounts = provide(SqlStaffAccounts, provides=StaffAccounts)
    get_staff_identity = provide(GetStaffIdentity)

    @provide(scope=Scope.APP)
    def staff_policy(self) -> StaffRolePolicy:
        return create_staff_policy()
