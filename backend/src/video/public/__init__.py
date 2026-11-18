"""Stable Video metadata scenarios and values; no infrastructure exports."""

from video.application.access import GetPrivateAccess, GetViewableVideo, SetPrivateAccess
from video.application.delivery import GetMedia, GetThumbnailDelivery
from video.application.discussions import RequireDiscussionVideo
from video.application.encoding import EncodeTrack, PlanMedia, RepairEncoding
from video.application.feed import GetChannelFeed, GetFeed
from video.application.likes import GetVideoLike, SetVideoLike
from video.application.originals import CleanupOriginals
from video.application.playlist_cards import GetPlaylistVideoCards, PlaylistVideoOwnership
from video.application.processing import (
    INSPECTION_TASK,
    MEDIA_QUEUE,
    InspectSource,
    RepairSourceProcessing,
)
from video.application.publications import CreateDraft, EditVideoSettings, GetVideoSettings, PublishVideo
from video.application.readiness import GetProcessing
from video.application.search import GetSearch
from video.application.thumbnails import (
    CompleteThumbnailUpload,
    GetThumbnailImage,
    GetThumbnails,
    PrepareThumbnail,
    RefreshThumbnailUpload,
    RepairThumbnails,
    SelectThumbnail,
    StartThumbnailUpload,
)
from video.application.uploads import CompleteUpload, ExpireUploads, RefreshUpload, StartUpload
from video.domain.access import VideoView
from video.domain.delivery import ObjectDelivery, PlaylistDelivery, PublicationError
from video.domain.encoding import ENCODE_TASK, PLAN_TASK
from video.domain.feed import FeedCard, FeedPage, InvalidFeedCursor
from video.domain.likes import InvalidLike, LikeState
from video.domain.permissions import InvalidPermissionCursor, PermissionPage
from video.domain.processing import SourceInspectionError, TemporarySourceError
from video.domain.publications import (
    AuthenticationRequired,
    VideoError,
    VideoMetadata,
    VideoNotFound,
    VideoSettings,
)
from video.domain.readiness import ProcessingStatus
from video.domain.search import InvalidSearch, SearchChannel, SearchPage, SearchQuery
from video.domain.thumbnails import PREPARE_THUMBNAIL_TASK, ThumbnailError, ThumbnailOptions
from video.domain.uploads import StorageUnavailable, UploadError, UploadPlan

__all__ = [
    'RequireDiscussionVideo',
    'GetVideoLike',
    'SetVideoLike',
    'InvalidLike',
    'LikeState',
    'InvalidPermissionCursor',
    'PermissionPage',
    'GetChannelFeed',
    'GetPlaylistVideoCards',
    'PlaylistVideoOwnership',
    'GetSearch',
    'SearchQuery',
    'SearchPage',
    'SearchChannel',
    'InvalidSearch',
    'GetFeed',
    'FeedCard',
    'FeedPage',
    'InvalidFeedCursor',
    'GetMedia',
    'GetThumbnailDelivery',
    'PublishVideo',
    'ObjectDelivery',
    'PlaylistDelivery',
    'PublicationError',
    'GetPrivateAccess',
    'SetPrivateAccess',
    'GetViewableVideo',
    'VideoView',
    'CleanupOriginals',
    'GetProcessing',
    'ProcessingStatus',
    'StartThumbnailUpload',
    'RefreshThumbnailUpload',
    'CompleteThumbnailUpload',
    'GetThumbnails',
    'SelectThumbnail',
    'GetThumbnailImage',
    'PrepareThumbnail',
    'RepairThumbnails',
    'PREPARE_THUMBNAIL_TASK',
    'ThumbnailError',
    'ThumbnailOptions',
    'PlanMedia',
    'EncodeTrack',
    'RepairEncoding',
    'PLAN_TASK',
    'ENCODE_TASK',
    'INSPECTION_TASK',
    'MEDIA_QUEUE',
    'InspectSource',
    'RepairSourceProcessing',
    'SourceInspectionError',
    'TemporarySourceError',
    'CompleteUpload',
    'ExpireUploads',
    'RefreshUpload',
    'StartUpload',
    'StorageUnavailable',
    'UploadError',
    'UploadPlan',
    'AuthenticationRequired',
    'CreateDraft',
    'EditVideoSettings',
    'GetVideoSettings',
    'VideoError',
    'VideoMetadata',
    'VideoNotFound',
    'VideoSettings',
]
