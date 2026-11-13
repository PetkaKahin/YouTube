"""Channel identity and transaction coordination tests."""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from channel.application.registration import Register
from channel.domain.registration import Registration, RegistrationError, validate_tag


@pytest.mark.parametrize("tag", ["AbC", "@Name", "a" * 30, "a.b_c-d", "123"])
def test_valid_tag_is_canonical(tag):
    assert validate_tag(tag) == tag.removeprefix("@").lower()


@pytest.mark.parametrize("tag", ["", "ab", "a" * 31, "@", "@@abc", "тег", "a b", " abc", "abc\n"])
def test_invalid_tag_is_rejected(tag):
    with pytest.raises(RegistrationError):
        validate_tag(tag)


def test_channel_failure_rolls_back_outer_registration_transaction():
    async def exercise():
        events = []

        @asynccontextmanager
        async def begin():
            events.append("begin")
            try:
                yield
            except RegistrationError:
                events.append("rollback")
                raise
            else:
                events.append("commit")

        accounts = Mock(create=AsyncMock(return_value=12))
        channels = Mock(create=AsyncMock(side_effect=RegistrationError("tag", "already_taken")))
        scenario = Register(accounts, channels, Mock(begin=begin))
        data = Registration("Иван", "a@example.com", "@AbC", "abcdef", "abcdef")
        with pytest.raises(RegistrationError):
            await scenario(data)
        accounts.create.assert_awaited_once_with(data)
        channels.create.assert_awaited_once_with(12, "abc")
        assert events == ["begin", "rollback"]

    asyncio.run(exercise())
