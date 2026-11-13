"""Bindings for comment ports and use cases are added with their implementations."""

from dishka import Provider, Scope


class CommentProvider(Provider):
    scope = Scope.REQUEST
