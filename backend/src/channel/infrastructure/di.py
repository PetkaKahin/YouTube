"""Atomic registration shares the request's database session with User."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from channel.application.pages import ChannelPages, FindChannel, GetChannel, GetChannelPlaylists
from channel.application.playlists import (
    CreatePlaylist,
    GetMyPlaylists,
    GetPlaylist,
    PlaylistActions,
    PlaylistActors,
    PlaylistStore,
    PlaylistVideos,
    SetVideoPlaylists,
)
from channel.application.profiles import GetPublicChannels, PublicChannels
from channel.application.registration import AccountCreator, Channels, Register
from channel.application.search import ChannelSearch, FindChannels, FindPlaylists, PlaylistSearch
from channel.application.subscription_lists import GetSubscribedChannels, GetSubscribedOwners
from channel.application.subscriptions import (
    GetSubscription,
    SetSubscription,
    SubscriptionActions,
    SubscriptionActors,
    Subscriptions,
)
from channel.application.watch_save import GetSavedPlaylists, SetSavedPlaylist
from channel.infrastructure.pages import SqlChannelPages
from channel.infrastructure.playlists import (
    SqlPlaylistStore,
    UserPlaylistActors,
    VideoPlaylistAdapter,
)
from channel.infrastructure.profiles import SqlPublicChannels
from channel.infrastructure.registration import SqlChannels, UserAccountCreator
from channel.infrastructure.search import SqlChannelSearch, SqlPlaylistSearch
from channel.infrastructure.subscriptions import SqlSubscriptions, UserSubscriptionActors


class ChannelProvider(Provider):
    scope = Scope.REQUEST

    accounts = provide(UserAccountCreator, provides=AccountCreator)
    channels = provide(SqlChannels, provides=Channels)
    public_channels = provide(SqlPublicChannels, provides=PublicChannels)
    get_public_channels = provide(GetPublicChannels)
    channel_pages = provide(SqlChannelPages, provides=ChannelPages)
    find_channel = provide(FindChannel)
    get_channel = provide(GetChannel)
    get_channel_playlists = provide(GetChannelPlaylists)
    channel_search = provide(SqlChannelSearch, provides=ChannelSearch)
    find_channels = provide(FindChannels)
    playlist_search = provide(SqlPlaylistSearch, provides=PlaylistSearch)
    find_playlists = provide(FindPlaylists)
    register = provide(Register)
    subscription_actors = provide(UserSubscriptionActors, provides=SubscriptionActors)
    subscriptions = provide(SqlSubscriptions, provides=Subscriptions)
    subscription_actions = provide(SubscriptionActions)
    get_subscription = provide(GetSubscription)
    set_subscription = provide(SetSubscription)
    subscribed_channels = provide(GetSubscribedChannels)
    subscribed_owners = provide(GetSubscribedOwners)
    playlist_actors = provide(UserPlaylistActors, provides=PlaylistActors)
    playlist_store = provide(SqlPlaylistStore, provides=PlaylistStore)
    playlist_videos = provide(VideoPlaylistAdapter, provides=PlaylistVideos)
    playlist_actions = provide(PlaylistActions)
    create_playlist = provide(CreatePlaylist)
    get_my_playlists = provide(GetMyPlaylists)
    get_playlist = provide(GetPlaylist)
    set_video_playlists = provide(SetVideoPlaylists)
    get_saved_playlists = provide(GetSavedPlaylists)
    set_saved_playlist = provide(SetSavedPlaylist)
