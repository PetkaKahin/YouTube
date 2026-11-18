"""Trusted staff identity contains only an account identifier and the stored role."""

from dataclasses import dataclass

from user.domain.accounts import Role


class StaffForbidden(Exception):
    pass


@dataclass(frozen=True)
class StaffIdentity:
    id: int
    role: Role
