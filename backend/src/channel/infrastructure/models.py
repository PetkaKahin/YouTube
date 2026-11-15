"""Channel profile; account details remain behind User.public."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base


class ChannelRow(Base):
    __tablename__ = "channel_profiles"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_accounts.id"), unique=True)
    tag: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_channel_profiles_tag", func.lower(tag), unique=True),
        CheckConstraint("tag ~ '^[A-Za-z0-9_.-]{3,30}$'", name="valid_tag"),
    )


class SubscriptionRow(Base):
    __tablename__ = "channel_subscriptions"

    subscriber_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_accounts.id"), primary_key=True
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("channel_profiles.id"), primary_key=True
    )


class PlaylistRow(Base):
    __tablename__ = "channel_playlists"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channel_profiles.id"))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    visibility: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 100", name="title_length"),
        CheckConstraint("char_length(description) <= 5000", name="description_length"),
        CheckConstraint("visibility IN ('public','unlisted','private')", name="valid_visibility"),
        Index("ix_channel_playlists_owner_order", channel_id, created_at.desc(), id.desc()),
    )


class PlaylistEntryRow(Base):
    __tablename__ = "channel_playlist_entries"
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    playlist_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("channel_playlists.id"))
    video_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("video_publications.id"), index=True
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
    __table_args__ = (
        UniqueConstraint("playlist_id", "video_id", name="uq_channel_playlist_entries_video"),
        Index("ix_channel_playlist_entries_order", "playlist_id", "id"),
    )
