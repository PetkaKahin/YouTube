"""Comment owns discussion entries and like pairs; foreign keys preserve identities."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base


class CommentRow(Base):
    __tablename__ = "comment_entries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    video_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("video_publications.id"))
    author_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_accounts.id"))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )

    __table_args__ = (
        CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="text_length"),
        CheckConstraint("text !~ '^[[:space:]]*$'", name="nonempty_text"),
        Index("ix_comment_entries_video_order", video_id, created_at.desc(), id.desc()),
    )


class CommentLikeRow(Base):
    __tablename__ = "comment_likes"

    comment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("comment_entries.id"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("user_accounts.id"), primary_key=True
    )
