"""Account invariants and application behavior without infrastructure."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

from user.application.accounts import Authenticate, CreateAccount, GetRole, SetRole
from user.domain.accounts import (
    Account,
    AccountError,
    AccountNotFound,
    Credentials,
    InvalidCredentials,
    NewAccount,
    Role,
    validate_name,
    validate_password,
)


@pytest.mark.parametrize("name", ["Ан", "A" * 50, "  Иван Петров  ", "Ёж", "Alice Smith"])
def test_name_accepts_boundaries_and_trims(name):
    assert validate_name(name) == name.strip()


@pytest.mark.parametrize("name", ["", " ", "A", "А" * 51, "Иван1", "A<script>", "A\tB"])
def test_name_rejects_invalid_values(name):
    with pytest.raises(AccountError) as error:
        validate_name(name)
    assert error.value.field == "name"


@pytest.mark.parametrize("password", ["a" * 6, "я" * 128, "      ", " Пароль "])
def test_password_accepts_boundaries_unicode_and_spaces(password):
    validate_password(password, password)


@pytest.mark.parametrize("password", ["", "a" * 5, "я" * 129])
def test_password_rejects_invalid_lengths(password):
    with pytest.raises(AccountError) as error:
        validate_password(password, password)
    assert error.value.field == "password"


def test_confirmation_is_exact_and_secret_values_are_not_in_repr():
    with pytest.raises(AccountError) as error:
        validate_password(" пароль ", "пароль")
    assert error.value.field == "password_confirmation"
    data = NewAccount("Иван", "a@example.com", "secret-one", "secret-two")
    assert "secret" not in repr(data)


def account():
    return Account(1, "Иван", "a@example.com", Role.USER, datetime.now(UTC))


def test_creation_validates_before_hashing_and_passes_only_hash_to_repository():
    async def exercise():
        repository = Mock(create=AsyncMock(return_value=account()))
        emails = Mock(normalize=Mock(return_value="a@example.com"))
        passwords = Mock(hash=AsyncMock(return_value="argon-hash"))
        scenario = CreateAccount(repository, emails, passwords)
        with pytest.raises(AccountError):
            await scenario(NewAccount("A", "a@example.com", "abcdef", "abcdef"))
        passwords.hash.assert_not_called()
        result = await scenario(NewAccount("  Иван  ", "A@example.com", " пароль ", " пароль "))
        passwords.hash.assert_awaited_once_with(" пароль ")
        repository.create.assert_awaited_once_with("Иван", "a@example.com", "argon-hash")
        assert result.role is Role.USER

    asyncio.run(exercise())


@pytest.mark.parametrize("found,valid", [(False, False), (True, False), (True, True)])
def test_authentication_checks_exact_password_even_for_unknown_email(found, valid):
    async def exercise():
        credentials = Credentials(account(), "argon-hash") if found else None
        repository = Mock(credentials=AsyncMock(return_value=credentials))
        passwords = Mock(verify=AsyncMock(return_value=valid))
        scenario = Authenticate(repository, Mock(normalize=lambda value: value), passwords)
        if valid:
            assert await scenario("a@example.com", " Пароль ") is credentials.account
        else:
            with pytest.raises(InvalidCredentials):
                await scenario("a@example.com", " Пароль ")
        passwords.verify.assert_awaited_once_with(" Пароль ", "argon-hash" if found else None)

    asyncio.run(exercise())


def test_role_assignment_requires_an_existing_account_and_valid_role():
    async def exercise():
        @asynccontextmanager
        async def begin():
            yield

        repository = Mock(credentials=AsyncMock(return_value=None), set_role=AsyncMock())
        scenario = SetRole(repository, Mock(normalize=lambda value: value), Mock(begin=begin))
        with pytest.raises(AccountError):
            await scenario("a@example.com", "owner")
        repository.credentials.assert_not_called()
        with pytest.raises(AccountNotFound):
            await scenario("a@example.com", "admin")
        repository.set_role.assert_not_called()
        repository.credentials.return_value = Credentials(account(), "hash")
        await scenario("a@example.com", "moderator")
        repository.set_role.assert_awaited_once_with(1, Role.MODERATOR)

    asyncio.run(exercise())


@pytest.mark.parametrize("role", list(Role))
def test_role_read_normalizes_email_and_returns_only_current_role(role):
    async def exercise():
        repository = Mock(role_by_email=AsyncMock(return_value=role), set_role=AsyncMock())
        emails = Mock(normalize=Mock(return_value="a@example.com"))
        assert await GetRole(repository, emails)(" A@EXAMPLE.COM ") is role
        emails.normalize.assert_called_once_with(" A@EXAMPLE.COM ")
        repository.role_by_email.assert_awaited_once_with("a@example.com")
        repository.set_role.assert_not_called()

    asyncio.run(exercise())


def test_role_read_rejects_missing_account_and_invalid_email_without_writing():
    async def exercise():
        repository = Mock(role_by_email=AsyncMock(return_value=None), set_role=AsyncMock())
        emails = Mock(normalize=Mock(return_value="a@example.com"))
        scenario = GetRole(repository, emails)
        with pytest.raises(AccountNotFound):
            await scenario("a@example.com")
        repository.role_by_email.reset_mock()
        emails.normalize.side_effect = AccountError("email", "invalid_email")
        with pytest.raises(AccountError):
            await scenario("invalid")
        repository.role_by_email.assert_not_called()
        repository.set_role.assert_not_called()

    asyncio.run(exercise())
