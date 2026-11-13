"""Bindings for video ports and use cases are added with their implementations."""

from dishka import Provider, Scope


class VideoProvider(Provider):
    scope = Scope.REQUEST
