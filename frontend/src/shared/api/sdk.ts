import * as wire from './generated/sdk.gen';
import {
  parseApiJson,
  serializeApiJson,
  type ExactIdentifiers,
  type Identifier,
} from './identifiers';

export function mineV1VideosMineGet(
  options?: Parameters<typeof wire.mineV1VideosMineGet>[0],
) {
  return exact(wire.mineV1VideosMineGet<false>({ ...options, ...jsonOptions }));
}

// The generated SDK reads the raw text; only this boundary assigns the adapted type.
async function exact<T, Error>(
  promise: Promise<{
    data?: T;
    error?: Error;
    response?: Response;
    request?: Request;
  }>,
) {
  const result = await promise;
  if (!result.response?.ok)
    return { ...result, data: undefined as ExactIdentifiers<T> | undefined };
  const data =
    result.response.status === 204
      ? undefined
      : parseApiJson(String(result.data));
  return { ...result, data: data as ExactIdentifiers<T> };
}

const jsonOptions = {
  parseAs: 'text',
  throwOnError: false,
  responseStyle: 'fields',
  bodySerializer: serializeApiJson,
} as const;

export function writeV1VideosVideoIdProgressPut(
  options: ExactPath<
    Parameters<typeof wire.writeV1VideosVideoIdProgressPut>[0]
  >,
) {
  return exact(
    wire.writeV1VideosVideoIdProgressPut<false>({
      ...pathOptions<
        Parameters<typeof wire.writeV1VideosVideoIdProgressPut>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function submitV1VideosVideoIdReportsPost(
  options: ExactPath<
    Parameters<typeof wire.submitV1VideosVideoIdReportsPost>[0]
  >,
) {
  return exact(
    wire.submitV1VideosVideoIdReportsPost<false>({
      ...pathOptions<
        Parameters<typeof wire.submitV1VideosVideoIdReportsPost>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function meV1AuthMeGet(
  options?: Parameters<typeof wire.meV1AuthMeGet>[0],
) {
  return exact(wire.meV1AuthMeGet<false>({ ...options, ...jsonOptions }));
}
export function loginV1AuthLoginPost(
  options: Parameters<typeof wire.loginV1AuthLoginPost>[0],
) {
  return exact(
    wire.loginV1AuthLoginPost<false>({ ...options, ...jsonOptions }),
  );
}
export function registerV1AuthRegisterPost(
  options: Parameters<typeof wire.registerV1AuthRegisterPost>[0],
) {
  return exact(
    wire.registerV1AuthRegisterPost<false>({ ...options, ...jsonOptions }),
  );
}
export function logoutV1AuthLogoutPost(
  options?: Parameters<typeof wire.logoutV1AuthLogoutPost>[0],
) {
  return exact(
    wire.logoutV1AuthLogoutPost<false>({ ...options, ...jsonOptions }),
  );
}
export function feedV1VideosFeedGet(
  options?: Parameters<typeof wire.feedV1VideosFeedGet>[0],
) {
  return exact(wire.feedV1VideosFeedGet<false>({ ...options, ...jsonOptions }));
}
export function searchV1SearchGet(
  options?: Parameters<typeof wire.searchV1SearchGet>[0],
) {
  return exact(wire.searchV1SearchGet<false>({ ...options, ...jsonOptions }));
}

type VideoOptions = Parameters<typeof wire.viewV1VideosVideoIdGet>[0];
export function viewV1VideosVideoIdGet(
  options: Omit<VideoOptions, 'path'> & { path: { video_id: Identifier } },
) {
  // Path serializers accept strings at runtime. Keep exact digits at this boundary.
  return exact(
    wire.viewV1VideosVideoIdGet<false>({
      ...options,
      path: options.path as unknown as VideoOptions['path'],
      ...jsonOptions,
    }),
  );
}

type ExactPath<T extends { path?: unknown }> = Omit<T, 'path'> & {
  path: ExactIdentifiers<NonNullable<T['path']>>;
};
function pathOptions<T extends { path?: unknown }>(options: ExactPath<T>): T {
  return options as unknown as T;
}
export function stateV1VideosVideoIdLikeGet(
  options: ExactPath<Parameters<typeof wire.stateV1VideosVideoIdLikeGet>[0]>,
) {
  return exact(
    wire.stateV1VideosVideoIdLikeGet<false>({
      ...pathOptions<Parameters<typeof wire.stateV1VideosVideoIdLikeGet>[0]>(
        options,
      ),
      ...jsonOptions,
    }),
  );
}
export function setStateV1VideosVideoIdLikePut(
  options: ExactPath<Parameters<typeof wire.setStateV1VideosVideoIdLikePut>[0]>,
) {
  return exact(
    wire.setStateV1VideosVideoIdLikePut<false>({
      ...pathOptions<Parameters<typeof wire.setStateV1VideosVideoIdLikePut>[0]>(
        options,
      ),
      ...jsonOptions,
    }),
  );
}
export function subscriptionV1ChannelsTagSubscriptionGet(
  options: Parameters<typeof wire.subscriptionV1ChannelsTagSubscriptionGet>[0],
) {
  return exact(
    wire.subscriptionV1ChannelsTagSubscriptionGet<false>({
      ...options,
      ...jsonOptions,
    }),
  );
}
export function setSubscriptionV1ChannelsTagSubscriptionPut(
  options: Parameters<
    typeof wire.setSubscriptionV1ChannelsTagSubscriptionPut
  >[0],
) {
  return exact(
    wire.setSubscriptionV1ChannelsTagSubscriptionPut<false>({
      ...options,
      ...jsonOptions,
    }),
  );
}
export function pageV1VideosVideoIdCommentsGet(
  options: ExactPath<Parameters<typeof wire.pageV1VideosVideoIdCommentsGet>[0]>,
) {
  return exact(
    wire.pageV1VideosVideoIdCommentsGet<false>({
      ...pathOptions<Parameters<typeof wire.pageV1VideosVideoIdCommentsGet>[0]>(
        options,
      ),
      ...jsonOptions,
    }),
  );
}
export function createV1VideosVideoIdCommentsPost(
  options: ExactPath<
    Parameters<typeof wire.createV1VideosVideoIdCommentsPost>[0]
  >,
) {
  return exact(
    wire.createV1VideosVideoIdCommentsPost<false>({
      ...pathOptions<
        Parameters<typeof wire.createV1VideosVideoIdCommentsPost>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}
export function setStateV1VideosVideoIdCommentsCommentIdLikePut(
  options: ExactPath<
    Parameters<typeof wire.setStateV1VideosVideoIdCommentsCommentIdLikePut>[0]
  >,
) {
  return exact(
    wire.setStateV1VideosVideoIdCommentsCommentIdLikePut<false>({
      ...pathOptions<
        Parameters<
          typeof wire.setStateV1VideosVideoIdCommentsCommentIdLikePut
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function channelV1ChannelsTagGet(
  options: Parameters<typeof wire.channelV1ChannelsTagGet>[0],
) {
  return exact(
    wire.channelV1ChannelsTagGet<false>({ ...options, ...jsonOptions }),
  );
}
export function videosV1ChannelsTagVideosGet(
  options: Parameters<typeof wire.videosV1ChannelsTagVideosGet>[0],
) {
  return exact(
    wire.videosV1ChannelsTagVideosGet<false>({ ...options, ...jsonOptions }),
  );
}
export function playlistsV1ChannelsTagPlaylistsGet(
  options: Parameters<typeof wire.playlistsV1ChannelsTagPlaylistsGet>[0],
) {
  return exact(
    wire.playlistsV1ChannelsTagPlaylistsGet<false>({
      ...options,
      ...jsonOptions,
    }),
  );
}
export function viewV1PlaylistsPlaylistIdGet(
  options: ExactPath<Parameters<typeof wire.viewV1PlaylistsPlaylistIdGet>[0]>,
) {
  return exact(
    wire.viewV1PlaylistsPlaylistIdGet<false>({
      ...pathOptions<Parameters<typeof wire.viewV1PlaylistsPlaylistIdGet>[0]>(
        options,
      ),
      ...jsonOptions,
    }),
  );
}

export function mineV1PlaylistsMineGet(
  options?: Parameters<typeof wire.mineV1PlaylistsMineGet>[0],
) {
  return exact(
    wire.mineV1PlaylistsMineGet<false>({ ...options, ...jsonOptions }),
  );
}
export function createV1PlaylistsPost(
  options: Omit<Parameters<typeof wire.createV1PlaylistsPost>[0], 'body'> & {
    body: ExactIdentifiers<
      NonNullable<Parameters<typeof wire.createV1PlaylistsPost>[0]['body']>
    >;
  },
) {
  return exact(
    wire.createV1PlaylistsPost<false>({
      ...(options as unknown as Parameters<
        typeof wire.createV1PlaylistsPost
      >[0]),
      ...jsonOptions,
    }),
  );
}

export function createV1VideosPost(
  options: Parameters<typeof wire.createV1VideosPost>[0],
) {
  return exact(wire.createV1VideosPost<false>({ ...options, ...jsonOptions }));
}

export function settingsV1VideosVideoIdSettingsGet(
  options: ExactPath<
    Parameters<typeof wire.settingsV1VideosVideoIdSettingsGet>[0]
  >,
) {
  return exact(
    wire.settingsV1VideosVideoIdSettingsGet<false>({
      ...pathOptions<
        Parameters<typeof wire.settingsV1VideosVideoIdSettingsGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function editV1VideosVideoIdSettingsPut(
  options: ExactPath<Parameters<typeof wire.editV1VideosVideoIdSettingsPut>[0]>,
) {
  return exact(
    wire.editV1VideosVideoIdSettingsPut<false>({
      ...pathOptions<Parameters<typeof wire.editV1VideosVideoIdSettingsPut>[0]>(
        options,
      ),
      ...jsonOptions,
    }),
  );
}

export function processingV1VideosVideoIdProcessingGet(
  options: ExactPath<
    Parameters<typeof wire.processingV1VideosVideoIdProcessingGet>[0]
  >,
) {
  return exact(
    wire.processingV1VideosVideoIdProcessingGet<false>({
      ...pathOptions<
        Parameters<typeof wire.processingV1VideosVideoIdProcessingGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function startV1VideosVideoIdUploadPost(
  options: ExactPath<Parameters<typeof wire.startV1VideosVideoIdUploadPost>[0]>,
) {
  return exact(
    wire.startV1VideosVideoIdUploadPost<false>({
      ...pathOptions<Parameters<typeof wire.startV1VideosVideoIdUploadPost>[0]>(
        options,
      ),
      ...jsonOptions,
    }),
  );
}

export function refreshV1VideosVideoIdUploadUploadIdRefreshPost(
  options: ExactPath<
    Parameters<typeof wire.refreshV1VideosVideoIdUploadUploadIdRefreshPost>[0]
  >,
) {
  return exact(
    wire.refreshV1VideosVideoIdUploadUploadIdRefreshPost<false>({
      ...pathOptions<
        Parameters<
          typeof wire.refreshV1VideosVideoIdUploadUploadIdRefreshPost
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function completeV1VideosVideoIdUploadUploadIdCompletePost(
  options: ExactPath<
    Parameters<typeof wire.completeV1VideosVideoIdUploadUploadIdCompletePost>[0]
  >,
) {
  return exact(
    wire.completeV1VideosVideoIdUploadUploadIdCompletePost<false>({
      ...pathOptions<
        Parameters<
          typeof wire.completeV1VideosVideoIdUploadUploadIdCompletePost
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function publishV1VideosVideoIdPublishPost(
  options: ExactPath<
    Parameters<typeof wire.publishV1VideosVideoIdPublishPost>[0]
  >,
) {
  return exact(
    wire.publishV1VideosVideoIdPublishPost<false>({
      ...pathOptions<
        Parameters<typeof wire.publishV1VideosVideoIdPublishPost>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function choicesV1VideosVideoIdPlaylistsGet(
  options: ExactPath<
    Parameters<typeof wire.choicesV1VideosVideoIdPlaylistsGet>[0]
  >,
) {
  return exact(
    wire.choicesV1VideosVideoIdPlaylistsGet<false>({
      ...pathOptions<
        Parameters<typeof wire.choicesV1VideosVideoIdPlaylistsGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function selectV1VideosVideoIdPlaylistsPut(
  options: Omit<
    ExactPath<Parameters<typeof wire.selectV1VideosVideoIdPlaylistsPut>[0]>,
    'body'
  > & { body: { playlist_ids: Identifier[] } },
) {
  return exact(
    wire.selectV1VideosVideoIdPlaylistsPut<false>({
      ...(options as unknown as Parameters<
        typeof wire.selectV1VideosVideoIdPlaylistsPut
      >[0]),
      ...jsonOptions,
    }),
  );
}

export function permissionsV1VideosVideoIdPermissionsGet(
  options: ExactPath<
    Parameters<typeof wire.permissionsV1VideosVideoIdPermissionsGet>[0]
  >,
) {
  return exact(
    wire.permissionsV1VideosVideoIdPermissionsGet<false>({
      ...pathOptions<
        Parameters<typeof wire.permissionsV1VideosVideoIdPermissionsGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function setPermissionV1VideosVideoIdPermissionsPut(
  options: ExactPath<
    Parameters<typeof wire.setPermissionV1VideosVideoIdPermissionsPut>[0]
  >,
) {
  return exact(
    wire.setPermissionV1VideosVideoIdPermissionsPut<false>({
      ...pathOptions<
        Parameters<typeof wire.setPermissionV1VideosVideoIdPermissionsPut>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function optionsV1VideosVideoIdThumbnailGet(
  options: ExactPath<
    Parameters<typeof wire.optionsV1VideosVideoIdThumbnailGet>[0]
  >,
) {
  return exact(
    wire.optionsV1VideosVideoIdThumbnailGet<false>({
      ...pathOptions<
        Parameters<typeof wire.optionsV1VideosVideoIdThumbnailGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function selectV1VideosVideoIdThumbnailPut(
  options: ExactPath<
    Parameters<typeof wire.selectV1VideosVideoIdThumbnailPut>[0]
  >,
) {
  return exact(
    wire.selectV1VideosVideoIdThumbnailPut<false>({
      ...pathOptions<
        Parameters<typeof wire.selectV1VideosVideoIdThumbnailPut>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function startV1VideosVideoIdThumbnailUploadPost(
  options: ExactPath<
    Parameters<typeof wire.startV1VideosVideoIdThumbnailUploadPost>[0]
  >,
) {
  return exact(
    wire.startV1VideosVideoIdThumbnailUploadPost<false>({
      ...pathOptions<
        Parameters<typeof wire.startV1VideosVideoIdThumbnailUploadPost>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost(
  options: ExactPath<
    Parameters<
      typeof wire.refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost
    >[0]
  >,
) {
  return exact(
    wire.refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost<false>({
      ...pathOptions<
        Parameters<
          typeof wire.refreshV1VideosVideoIdThumbnailUploadUploadIdRefreshPost
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost(
  options: ExactPath<
    Parameters<
      typeof wire.completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost
    >[0]
  >,
) {
  return exact(
    wire.completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost<false>({
      ...pathOptions<
        Parameters<
          typeof wire.completeV1VideosVideoIdThumbnailUploadUploadIdCompletePost
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function reportsV1ModerationReportsGet(
  options?: Parameters<typeof wire.reportsV1ModerationReportsGet>[0],
) {
  return exact(
    wire.reportsV1ModerationReportsGet<false>({ ...options, ...jsonOptions }),
  );
}
export function reportV1ModerationReportsReportIdGet(
  options: ExactPath<
    Parameters<typeof wire.reportV1ModerationReportsReportIdGet>[0]
  >,
) {
  return exact(
    wire.reportV1ModerationReportsReportIdGet<false>({
      ...pathOptions<
        Parameters<typeof wire.reportV1ModerationReportsReportIdGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}
export function reportedVideoV1ModerationReportsReportIdVideoGet(
  options: ExactPath<
    Parameters<typeof wire.reportedVideoV1ModerationReportsReportIdVideoGet>[0]
  >,
) {
  return exact(
    wire.reportedVideoV1ModerationReportsReportIdVideoGet<false>({
      ...pathOptions<
        Parameters<
          typeof wire.reportedVideoV1ModerationReportsReportIdVideoGet
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}
export function decideV1ModerationReportsReportIdDecisionPut(
  options: ExactPath<
    Parameters<typeof wire.decideV1ModerationReportsReportIdDecisionPut>[0]
  >,
) {
  return exact(
    wire.decideV1ModerationReportsReportIdDecisionPut<false>({
      ...pathOptions<
        Parameters<typeof wire.decideV1ModerationReportsReportIdDecisionPut>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function errorsV1AdminJobErrorsGet(
  options?: Parameters<typeof wire.errorsV1AdminJobErrorsGet>[0],
) {
  return exact(
    wire.errorsV1AdminJobErrorsGet<false>({ ...options, ...jsonOptions }),
  );
}
export function retryOneV1AdminJobsJobIdRetryPost(
  options: ExactPath<
    Parameters<typeof wire.retryOneV1AdminJobsJobIdRetryPost>[0]
  >,
) {
  return exact(
    wire.retryOneV1AdminJobsJobIdRetryPost<false>({
      ...pathOptions<
        Parameters<typeof wire.retryOneV1AdminJobsJobIdRetryPost>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}
export function retryStatusV1AdminJobRetriesRequestIdGet(
  options: ExactPath<
    Parameters<typeof wire.retryStatusV1AdminJobRetriesRequestIdGet>[0]
  >,
) {
  return exact(
    wire.retryStatusV1AdminJobRetriesRequestIdGet<false>({
      ...pathOptions<
        Parameters<typeof wire.retryStatusV1AdminJobRetriesRequestIdGet>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}
export function retryV1AdminJobRetriesPost(
  options: Omit<
    Parameters<typeof wire.retryV1AdminJobRetriesPost>[0],
    'body'
  > & { body: { mode: 'all' } | { mode: 'selected'; job_ids: Identifier[] } },
) {
  return exact(
    wire.retryV1AdminJobRetriesPost<false>({
      ...(options as unknown as Parameters<
        typeof wire.retryV1AdminJobRetriesPost
      >[0]),
      ...jsonOptions,
    }),
  );
}

export function subscriptionsV1ChannelsSubscriptionsGet(
  options?: Parameters<typeof wire.subscriptionsV1ChannelsSubscriptionsGet>[0],
) {
  return exact(
    wire.subscriptionsV1ChannelsSubscriptionsGet<false>({
      ...options,
      ...jsonOptions,
    }),
  );
}

export function librarySubscriptionsV1VideosSubscriptionsGet(
  options?: Parameters<
    typeof wire.librarySubscriptionsV1VideosSubscriptionsGet
  >[0],
) {
  return exact(
    wire.librarySubscriptionsV1VideosSubscriptionsGet<false>({
      ...options,
      ...jsonOptions,
    }),
  );
}

export function libraryLikedV1VideosLikedGet(
  options?: Parameters<typeof wire.libraryLikedV1VideosLikedGet>[0],
) {
  return exact(
    wire.libraryLikedV1VideosLikedGet<false>({ ...options, ...jsonOptions }),
  );
}

export function libraryHistoryV1VideosHistoryGet(
  options?: Parameters<typeof wire.libraryHistoryV1VideosHistoryGet>[0],
) {
  return exact(
    wire.libraryHistoryV1VideosHistoryGet<false>({
      ...options,
      ...jsonOptions,
    }),
  );
}

export function savedPlaylistsV1VideosVideoIdSavedPlaylistsGet(
  options: ExactPath<
    Parameters<typeof wire.savedPlaylistsV1VideosVideoIdSavedPlaylistsGet>[0]
  >,
) {
  return exact(
    wire.savedPlaylistsV1VideosVideoIdSavedPlaylistsGet<false>({
      ...pathOptions<
        Parameters<
          typeof wire.savedPlaylistsV1VideosVideoIdSavedPlaylistsGet
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut(
  options: ExactPath<
    Parameters<
      typeof wire.savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut
    >[0]
  >,
) {
  return exact(
    wire.savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut<false>({
      ...pathOptions<
        Parameters<
          typeof wire.savedSelectionV1PlaylistsPlaylistIdVideosVideoIdPut
        >[0]
      >(options),
      ...jsonOptions,
    }),
  );
}

export function setDislikeV1VideosVideoIdDislikePut(
  options: ExactPath<
    Parameters<typeof wire.setDislikeV1VideosVideoIdDislikePut>[0]
  >,
) {
  return exact(
    wire.setDislikeV1VideosVideoIdDislikePut<false>({
      ...pathOptions<
        Parameters<typeof wire.setDislikeV1VideosVideoIdDislikePut>[0]
      >(options),
      ...jsonOptions,
    }),
  );
}
