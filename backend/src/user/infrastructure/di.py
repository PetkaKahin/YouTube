"""Request-scoped account adapters and plain application scenarios."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from user.application.accounts import Authenticate, CreateAccount, GetAccount, GetRole, SetRole
from user.application.ports import Accounts, EmailNormalizer, Passwords
from user.infrastructure.accounts import SqlAccounts
from user.infrastructure.security import ArgonPasswords, StandardEmail


class UserProvider(Provider):
    scope = Scope.REQUEST

    accounts = provide(SqlAccounts, provides=Accounts)
    emails = provide(StandardEmail, provides=EmailNormalizer, scope=Scope.APP)
    passwords = provide(ArgonPasswords, provides=Passwords, scope=Scope.APP)
    create_account = provide(CreateAccount)
    authenticate = provide(Authenticate)
    get_account = provide(GetAccount)
    get_role = provide(GetRole)
    set_role = provide(SetRole)
