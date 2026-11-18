"""Registration facade; storage and account coordination remain private."""

from channel.application.pages import FindChannel, GetChannel, GetChannelPlaylists
from channel.application.playlists import (
    CreatePlaylist,
    GetMyPlaylists,
    GetPlaylist,
    SetVideoPlaylists,
)
from channel.application.profiles import GetPublicChannels
from channel.application.registration import Register
from channel.application.search import FindChannels, FindPlaylists
from channel.application.subscription_lists import GetSubscribedChannels, GetSubscribedOwners
from channel.application.subscriptions import GetSubscription, SetSubscription
from channel.domain.pages import (
    ChannelIdentity,
    ChannelNotFound,
    ChannelPlaylistPage,
    ChannelProfile,
)
from channel.domain.playlists import Playlist, PlaylistAuthenticationRequired, PlaylistError, PlaylistList, PlaylistMetadata, PlaylistNotFound, PlaylistVideo, PlaylistView, PlaylistWatchProgress
from channel.domain.profiles import PublicChannel
from channel.domain.registration import Registered, Registration, RegistrationError
from channel.domain.search import ChannelSearchHit, PlaylistSearchHit
from channel.domain.subscription_lists import InvalidSubscriptionCursor, SubscribedChannelPage
from channel.domain.subscriptions import (
    SubscriptionAuthenticationRequired,
    SubscriptionError,
    SubscriptionState,
)

__all__ = [
    'GetSubscribedOwners',
    'GetSubscribedChannels',
    'InvalidSubscriptionCursor',
    'SubscribedChannelPage',
    'GetSubscription',
    'SetSubscription',
    'SubscriptionAuthenticationRequired',
    'SubscriptionError',
    'SubscriptionState',
    'FindChannel',
    'GetChannel',
    'GetChannelPlaylists',
    'ChannelIdentity',
    'ChannelNotFound',
    'ChannelPlaylistPage',
    'ChannelProfile',
    'FindPlaylists',
    'PlaylistSearchHit',
    'CreatePlaylist',
    'GetMyPlaylists',
    'GetPlaylist',
    'SetVideoPlaylists',
    'Playlist',
    'PlaylistAuthenticationRequired',
    'PlaylistError',
    'PlaylistList',
    'PlaylistMetadata',
    'PlaylistNotFound',
    'PlaylistVideo',
    'PlaylistWatchProgress',
    'PlaylistView',
    'FindChannels',
    'ChannelSearchHit',
    'GetPublicChannels',
    'PublicChannel',
    'Register',
    'Registered',
    'Registration',
    'RegistrationError',
]
