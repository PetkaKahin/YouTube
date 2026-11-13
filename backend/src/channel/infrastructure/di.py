"""Bindings for channel ports and use cases are added with their implementations."""

from dishka import Provider, Scope


class ChannelProvider(Provider):
    scope = Scope.REQUEST
