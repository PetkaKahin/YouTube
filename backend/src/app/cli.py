"""Operator entry point; invokes User.public instead of editing tables."""

import argparse
import asyncio

from app.di import create_container
from app.resources import create_cache
from app.secrets import read_secret
from app.settings import Settings
from user.public import AccountError, AccountNotFound, GetRole, Role, SetRole


async def manage_role(action: str, email: str, role: str | None = None) -> str:
    settings = Settings()
    cache = create_cache(settings, read_secret("VALKEY_PASSWORD"))
    container = create_container(settings, cache)
    try:
        async with container() as operation:
            if action == "get":
                current = await (await operation.get(GetRole))(email)
                return current.value
            if role is None:
                raise AccountError("role", "required")
            await (await operation.get(SetRole))(email, role)
            return "Role updated."
    finally:
        await container.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="YouTube account administration")
    commands = parser.add_subparsers(dest="command", required=True)
    role_command = commands.add_parser("role", help="Read or change an account role")
    actions = role_command.add_subparsers(dest="action", required=True)
    read = actions.add_parser("get", help="Print the current role")
    read.add_argument("email")
    write = actions.add_parser("set", help="Assign a role to an existing account")
    write.add_argument("email")
    write.add_argument("role", choices=[role.value for role in Role])
    args = parser.parse_args()
    try:
        result = asyncio.run(manage_role(args.action, args.email, getattr(args, "role", None)))
    except AccountNotFound:
        parser.exit(1, "Account not found.\n")
    except AccountError:
        parser.exit(1, "Invalid account data.\n")
    print(result)


if __name__ == "__main__":
    main()
