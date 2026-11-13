"""Account values and registration invariants; no HTTP or storage dependencies."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Role(StrEnum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class AccountError(Exception):
    def __init__(self, field: str, code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


class InvalidCredentials(Exception):
    pass


class AccountNotFound(Exception):
    pass


@dataclass(frozen=True)
class Account:
    id: int
    name: str
    email: str
    role: Role
    created_at: datetime


@dataclass(frozen=True)
class Credentials:
    account: Account
    password_hash: str = field(repr=False)


@dataclass(frozen=True)
class NewAccount:
    name: str
    email: str
    password: str = field(repr=False)
    password_confirmation: str = field(repr=False)


def validate_name(value: str) -> str:
    name = value.strip()
    if not 2 <= len(name) <= 50:
        raise AccountError("name", "invalid_length")
    if any(
        char != " "
        and not (char.isalpha() and ("a" <= char.lower() <= "z" or "\u0400" <= char <= "\u052f"))
        for char in name
    ):
        raise AccountError("name", "invalid_characters")
    return name


def validate_password(password: str, confirmation: str) -> None:
    if not 6 <= len(password) <= 128:
        raise AccountError("password", "invalid_length")
    if password != confirmation:
        raise AccountError("password_confirmation", "password_mismatch")
