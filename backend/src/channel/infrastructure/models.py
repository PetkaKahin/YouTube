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
