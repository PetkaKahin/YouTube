"""Bindings for user ports and use cases are added with their implementations."""

from dishka import Provider, Scope


class UserProvider(Provider):
    scope = Scope.REQUEST
