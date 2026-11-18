"""Video metadata ports and use cases in one request/CLI transaction."""

from dishka import (
    Provider,
    Scope,
    alias,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from video.application.access import (
    GetPrivateAccess,
    GetViewableVideo,
    PermissionActions,
    PermissionEmails,
    PrivateGrants,
    SetPrivateAccess,
    VideoAccess,
    VideoViews,
    ViewerIdentities,
    ViewingPolicy,
)
from video.application.delivery import GetMedia, GetThumbnailDelivery, MediaRedirects
from video.application.discussions import DiscussionPublications, RequireDiscussionVideo
from video.application.encoding import (
    EncodeTrack,
    EncodingJobs,
    MediaEncoder,
    PlanMedia,
    RepairEncoding,
)
from video.application.feed import (
    FeedAuthors,
    FeedCandidates,
    FeedChannels,
    GetChannelFeed,
    GetFeed,
    ReadFeed,
)
from video.application.likes import GetVideoLike, LikeActions, SetVideoLike, VideoLikes
from video.application.moderation import ModerationViews, ReportVideoAccess, VideoStaffActors
from video.application.originals import CleanupOriginals, OriginalDeletions, OriginalStorage
from video.application.playlist_cards import GetPlaylistVideoCards, PlaylistCardViews, PlaylistVideoOwnership
from video.application.ports import Publications, VideoActors
from video.application.processing import (
    InspectSource,
    RepairSourceProcessing,
    SourceInspector,
    SourceJobs,
)
from video.application.publications import CreateDraft, EditVideoSettings, GetVideoSettings, PublishVideo
from video.application.readiness import GetProcessing, ProcessingViews
from video.application.search import GetSearch, SearchChannels, SearchPlaylists, SearchVideos
from video.application.thumbnails import (
    CompleteThumbnailUpload,
    GetThumbnailImage,
    GetThumbnails,
    PrepareThumbnail,
    RefreshThumbnailUpload,
    RepairThumbnails,
    SelectThumbnail,
    StartThumbnailUpload,
    ThumbnailActions,
    ThumbnailJobs,
    ThumbnailProcessor,
    ThumbnailUploadOperations,
    ThumbnailUploads,
)
from video.application.uploads import (
    CompleteUpload,
    ExpireUploads,
    MultipartStorage,
    RefreshUpload,
    StartUpload,
    UploadAdmission,
    UploadOperations,
    Uploads,
)
from video.infrastructure.access import (
    AccountPermissionEmails,
    AccountViewers,
    SqlPrivateGrants,
    SqlVideoViews,
)
from video.infrastructure.actors import AccountActors
from video.infrastructure.delivery import S3MediaRedirects
from video.infrastructure.discussions import SqlDiscussionPublications
from video.infrastructure.encoder import S3MediaEncoder
from video.infrastructure.encoding_jobs import SqlEncodingJobs
from video.infrastructure.feed import ChannelFeedAuthors, ChannelFeedScope, SqlFeedCandidates
from video.infrastructure.likes import SqlVideoLikes
from video.infrastructure.moderation import AccountVideoStaff, SqlModerationViews
from video.infrastructure.multipart import S3Multipart
from video.infrastructure.originals import S3OriginalStorage, SqlOriginalDeletions
from video.infrastructure.playlist_cards import SqlPlaylistCardViews
from video.infrastructure.processing import SqlSourceJobs
from video.infrastructure.publications import SqlPublications
from video.infrastructure.readiness import SqlProcessingViews
from video.infrastructure.search import ChannelSearchResults, PlaylistSearchResults, SqlSearchVideos
from video.infrastructure.source_inspector import S3SourceInspector
from video.infrastructure.thumbnail_jobs import SqlThumbnailJobs, SqlThumbnailUploads
from video.infrastructure.thumbnail_media import S3ThumbnailProcessor
from video.infrastructure.uploads import SqlUploads
from video.infrastructure.viewing_policy import create_viewing_policy


class VideoProvider(Provider):
    scope = Scope.REQUEST
    permission_emails = provide(AccountPermissionEmails, provides=PermissionEmails)
    private_grants = provide(SqlPrivateGrants, provides=PrivateGrants)
    viewer_identities = provide(AccountViewers, provides=ViewerIdentities)
    video_views = provide(SqlVideoViews, provides=VideoViews)

    @provide(scope=Scope.APP)
    def viewing_policy(self) -> ViewingPolicy:
        return create_viewing_policy()

    permission_actions = provide(PermissionActions)
    get_private_access = provide(GetPrivateAccess)
    set_private_access = provide(SetPrivateAccess)
    get_viewable_video = provide(GetViewableVideo)
    video_access = provide(VideoAccess)
    video_staff = provide(AccountVideoStaff, provides=VideoStaffActors)
    moderation_views = provide(SqlModerationViews, provides=ModerationViews)
    report_video_access = provide(ReportVideoAccess)
    discussion_publications = provide(SqlDiscussionPublications, provides=DiscussionPublications)
    require_discussion_video = provide(RequireDiscussionVideo)
    video_likes = provide(SqlVideoLikes, provides=VideoLikes)
    like_actions = provide(LikeActions)
    get_video_like = provide(GetVideoLike)
    set_video_like = provide(SetVideoLike)
    publish_video = provide(PublishVideo)
    media_redirects = provide(S3MediaRedirects, provides=MediaRedirects)
    get_media = provide(GetMedia)
    feed_candidates = provide(SqlFeedCandidates, provides=FeedCandidates)
    feed_authors = provide(ChannelFeedAuthors, provides=FeedAuthors)
    get_feed = provide(GetFeed)
    read_feed = provide(ReadFeed)
    feed_channels = provide(ChannelFeedScope, provides=FeedChannels)
    get_channel_feed = provide(GetChannelFeed)
    playlist_card_views = provide(SqlPlaylistCardViews, provides=PlaylistCardViews)
    get_playlist_cards = provide(GetPlaylistVideoCards)
    playlist_ownership = provide(PlaylistVideoOwnership)
    search_videos = provide(SqlSearchVideos, provides=SearchVideos)
    search_channels = provide(ChannelSearchResults, provides=SearchChannels)
    search_playlists = provide(PlaylistSearchResults, provides=SearchPlaylists)
    get_search = provide(GetSearch)
    original_deletions = provide(SqlOriginalDeletions, provides=OriginalDeletions)
    original_storage = provide(S3OriginalStorage, provides=OriginalStorage)
    cleanup_originals = provide(CleanupOriginals)

    actors = provide(AccountActors, provides=VideoActors)
    publications = provide(SqlPublications, provides=Publications)
    create_draft = provide(CreateDraft)
    get_settings = provide(GetVideoSettings)
    processing_views = provide(SqlProcessingViews, provides=ProcessingViews)
    get_processing = provide(GetProcessing)
    edit_settings = provide(EditVideoSettings)
    upload_repository = provide(SqlUploads, provides=Uploads)
    multipart_storage = provide(S3Multipart, provides=MultipartStorage)
    upload_operations = provide(UploadOperations)
    start_upload = provide(StartUpload)
    refresh_upload = provide(RefreshUpload)
    complete_upload = provide(CompleteUpload)
    expire_uploads = provide(ExpireUploads)
    source_jobs = provide(SqlSourceJobs, provides=SourceJobs)
    upload_admission = alias(SourceJobs, provides=UploadAdmission)
    source_inspector = provide(S3SourceInspector, provides=SourceInspector)
    inspect_source = provide(InspectSource)
    repair_sources = provide(RepairSourceProcessing)
    encoding_jobs = provide(SqlEncodingJobs, provides=EncodingJobs)
    media_encoder = provide(S3MediaEncoder, provides=MediaEncoder)
    plan_media = provide(PlanMedia)
    encode_track = provide(EncodeTrack)
    repair_encoding = provide(RepairEncoding)
    thumbnail_uploads = provide(SqlThumbnailUploads, provides=ThumbnailUploads)
    thumbnail_jobs = provide(SqlThumbnailJobs, provides=ThumbnailJobs)
    thumbnail_processor = provide(S3ThumbnailProcessor, provides=ThumbnailProcessor)
    thumbnail_upload_operations = provide(ThumbnailUploadOperations)
    thumbnail_actions = provide(ThumbnailActions)
    start_thumbnail_upload = provide(StartThumbnailUpload)
    refresh_thumbnail_upload = provide(RefreshThumbnailUpload)
    complete_thumbnail_upload = provide(CompleteThumbnailUpload)
    get_thumbnails = provide(GetThumbnails)
    select_thumbnail = provide(SelectThumbnail)
    get_thumbnail_image = provide(GetThumbnailImage)
    get_thumbnail_delivery = provide(GetThumbnailDelivery)
    prepare_thumbnail = provide(PrepareThumbnail)
    repair_thumbnails = provide(RepairThumbnails)
