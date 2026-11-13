"""Video metadata ports and use cases in one request/CLI transaction."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from video.application.ports import Publications, VideoActors
from video.application.publications import CreateDraft, EditVideoSettings, GetVideoSettings
from video.infrastructure.actors import AccountActors
from video.infrastructure.publications import SqlPublications


class VideoProvider(Provider):
    scope = Scope.REQUEST

    actors = provide(AccountActors, provides=VideoActors)
    publications = provide(SqlPublications, provides=Publications)
    create_draft = provide(CreateDraft)
    get_settings = provide(GetVideoSettings)
    edit_settings = provide(EditVideoSettings)
