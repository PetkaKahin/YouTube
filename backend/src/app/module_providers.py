"""The only external entry point allowed to import module DI providers."""

from dishka import Provider

from channel.infrastructure.di import ChannelProvider
from comment.infrastructure.di import CommentProvider
from moderation.infrastructure.di import ModerationProvider
from user.infrastructure.di import UserProvider
from video.infrastructure.di import VideoProvider


def module_providers() -> tuple[Provider, ...]:
    return (
        UserProvider(),
        VideoProvider(),
        ChannelProvider(),
        CommentProvider(),
        ModerationProvider(),
    )
