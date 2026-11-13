"""Selected email/Argon2 libraries behind application ports."""

from anyio import to_thread
from email_validator import EmailNotValidError, validate_email
from pwdlib import PasswordHash

from user.domain.accounts import AccountError


class StandardEmail:
    def normalize(self, value: str) -> str:
        try:
            return validate_email(value.strip(), check_deliverability=False).normalized.casefold()
        except EmailNotValidError:
            raise AccountError("email", "invalid_email") from None


class ArgonPasswords:
    def __init__(self, hasher: PasswordHash) -> None:
        self.hasher = hasher
        # Unknown accounts still perform a password verification.
        self.dummy_hash = hasher.hash("dummy-password-for-missing-account")

    async def hash(self, password: str) -> str:
        return await to_thread.run_sync(self.hasher.hash, password)

    async def verify(self, password: str, password_hash: str | None) -> bool:
        valid = await to_thread.run_sync(
            self.hasher.verify, password, password_hash or self.dummy_hash
        )
        return valid and password_hash is not None
