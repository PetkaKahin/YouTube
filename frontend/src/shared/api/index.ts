export {
  setDislikeV1VideosVideoIdDislikePut,
  savedPlaylistsV1VideosVideoIdSavedPlaylistsGet,
  savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut,
  subscriptionsV1ChannelsSubscriptionsGet,
  librarySubscriptionsV1VideosSubscriptionsGet,
  libraryLikedV1VideosLikedGet,
  libraryHistoryV1VideosHistoryGet,
  writeV1VideosVideoIdProgressPut,
  errorsV1AdminJobErrorsGet,
  retryV1AdminJobRetriesPost,
  retryOneV1AdminJobsJobIdRetryPost,
  retryStatusV1AdminJobRetriesRequestIdGet,
  reportsV1ModerationReportsGet,
  reportV1ModerationReportsReportIdGet,
  reportedVideoV1ModerationReportsReportIdVideoGet,
  decideV1ModerationReportsReportIdDecisionPut,
  loginV1AuthLoginPost,
  logoutV1AuthLogoutPost,
  meV1AuthMeGet,
  registerV1AuthRegisterPost,
  feedV1VideosFeedGet,
  searchV1SearchGet,
  viewV1VideosVideoIdGet,
  stateV1VideosVideoIdLikeGet,
  setStateV1VideosVideoIdLikePut,
  subscriptionV1ChannelsTagSubscriptionGet,
  setSubscriptionV1ChannelsTagSubscriptionPut,
  pageV1VideosVideoIdCommentsGet,
  createV1VideosVideoIdCommentsPost,
  setStateV1VideosVideoIdCommentsCommentIdLikePut,
  channelV1ChannelsTagGet,
  videosV1ChannelsTagVideosGet,
  playlistsV1ChannelsTagPlaylistsGet,
  viewV1PlaylistsPlaylistIdGet,
  mineV1PlaylistsMineGet,
  mineV1VideosMineGet,
  submitV1VideosVideoIdReportsPost,
  createV1PlaylistsPost,
  createV1VideosPost,
  settingsV1VideosVideoIdSettingsGet,
  editV1VideosVideoIdSettingsPut,
  processingV1VideosVideoIdProcessingGet,
  startV1VideosVideoIdUploadPost,
  refreshV1VideosVideoIdUploadUploadIdRefreshPost,
  completeV1VideosVideoIdUploadUploadIdCompletePost,
  publishV1VideosVideoIdPublishPost,
  choicesV1VideosVideoIdPlaylistsGet,
  selectV1VideosVideoIdPlaylistsPut,
  permissionsV1VideosVideoIdPermissionsGet,
  setPermissionV1VideosVideoIdPermissionsPut,
  optionsV1VideosVideoIdThumbnailGet,
  selectV1VideosVideoIdThumbnailPut,
  startV1VideosVideoIdThumbnailUploadPost,
  refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost,
  completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost,
} from './sdk';
import type * as Wire from './generated/types.gen';
import type { ExactIdentifiers } from './identifiers';
export type AccountBody = ExactIdentifiers<Wire.AccountBody>;
export type FeedCardResponse = ExactIdentifiers<Wire.FeedCardResponse>;
export type SearchItemResponse = ExactIdentifiers<Wire.SearchItemResponse>;
export type VideoDetailsResponse = ExactIdentifiers<Wire.VideoDetailsResponse>;
export type CommentResponse = ExactIdentifiers<Wire.CommentResponse>;
export type ChannelResponse = ExactIdentifiers<Wire.ChannelResponse>;
export type PlaylistResponse = ExactIdentifiers<Wire.PlaylistResponse>;
export type RegisterBody = Wire.RegisterBody;
export { isIdentifier, type Identifier } from './identifiers';
export { ApiError, apiMessage, configureApi, unwrap } from './transport';

export type UploadResponse = ExactIdentifiers<Wire.UploadResponse>;
export type VideoSettingsResponse =
  ExactIdentifiers<Wire.VideoSettingsResponse>;
export type VideoSettingsListResponse =
  ExactIdentifiers<Wire.VideoSettingsListResponse>;

export type PlaylistListResponse = ExactIdentifiers<Wire.PlaylistListResponse>;

export type ThumbnailResponse = ExactIdentifiers<Wire.ThumbnailResponse>;

export type PermissionsResponse = ExactIdentifiers<Wire.PermissionsResponse>;

export type ReportResponse = ExactIdentifiers<Wire.ReportResponse>;
