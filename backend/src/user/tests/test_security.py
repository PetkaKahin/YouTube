"""Exercise the selected email and password libraries behind User's ports."""

import asyncio

import pytest
from pwdlib import PasswordHash

from user.domain.accounts import AccountError
from user.infrastructure.security import ArgonPasswords, StandardEmail


def test_email_normalization_requires_syntax_but_not_delivery():
    emails = StandardEmail()
    assert emails.normalize("  Alice@EXAMPLE.COM  ") == "alice@example.com"
    assert emails.normalize("person@this-domain-does-not-exist-379248.com")
    with pytest.raises(AccountError):
        emails.normalize("invalid")


def test_argon_hash_does_not_store_password_and_preserves_its_spaces():
    async def exercise():
        passwords = ArgonPasswords(PasswordHash.recommended())
        value = " Пароль 123 "
        encoded = await passwords.hash(value)
        assert encoded.startswith("$argon2id$") and value not in encoded
        assert await passwords.verify(value, encoded)
        assert not await passwords.verify(value.strip(), encoded)
        assert not await passwords.verify(value, None)

    asyncio.run(exercise())
