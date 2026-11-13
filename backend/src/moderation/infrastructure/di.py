"""Bindings for moderation ports and use cases are added with their implementations."""

from dishka import Provider, Scope


class ModerationProvider(Provider):
    scope = Scope.REQUEST
