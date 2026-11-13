"""Channel identity and the registration boundary."""

import re
from dataclasses import dataclass, field
from datetime import datetime


class RegistrationError(Exception):
    def __init__(self, field: str, code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


@dataclass(frozen=True)
class Registration:
    name: str
    email: str
    tag: str
    password: str = field(repr=False)
    password_confirmation: str = field(repr=False)


@dataclass(frozen=True)
class Registered:
    account_id: int
    channel_id: int
    tag: str
    created_at: datetime


def validate_tag(value: str) -> str:
    tag = value.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,30}", tag):
        raise RegistrationError("tag", "invalid_tag")
    return tag.lower()
