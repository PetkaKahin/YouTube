"""Atomic registration shares the request's database session with User."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from channel.application.registration import AccountCreator, Channels, Register
from channel.infrastructure.registration import SqlChannels, UserAccountCreator


class ChannelProvider(Provider):
    scope = Scope.REQUEST

    accounts = provide(UserAccountCreator, provides=AccountCreator)
    channels = provide(SqlChannels, provides=Channels)
    register = provide(Register)
