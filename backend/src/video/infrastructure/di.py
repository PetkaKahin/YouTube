"""Video metadata ports and use cases in one request/CLI transaction."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from video.application.ports import Publications, VideoActors
from video.application.publications import CreateDraft, EditVideoSettings, GetVideoSettings
from video.application.uploads import (
    CompleteUpload,
    ExpireUploads,
    MultipartStorage,
    RefreshUpload,
    StartUpload,
    UploadOperations,
    Uploads,
)
from video.infrastructure.actors import AccountActors
from video.infrastructure.multipart import S3Multipart
from video.infrastructure.publications import SqlPublications
from video.infrastructure.uploads import SqlUploads


class VideoProvider(Provider):
    scope = Scope.REQUEST

    actors = provide(AccountActors, provides=VideoActors)
    publications = provide(SqlPublications, provides=Publications)
    create_draft = provide(CreateDraft)
    get_settings = provide(GetVideoSettings)
    edit_settings = provide(EditVideoSettings)
    upload_repository = provide(SqlUploads, provides=Uploads)
    multipart_storage = provide(S3Multipart, provides=MultipartStorage)
    upload_operations = provide(UploadOperations)
    start_upload = provide(StartUpload)
    refresh_upload = provide(RefreshUpload)
    complete_upload = provide(CompleteUpload)
    expire_uploads = provide(ExpireUploads)
