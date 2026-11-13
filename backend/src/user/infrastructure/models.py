"""Persistent accounts owned exclusively by User."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.infrastructure.database import Base


class AccountRow(Base):
    __tablename__ = "user_accounts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    email: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(9), server_default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_user_accounts_email", func.lower(email), unique=True),
        CheckConstraint("role IN ('user', 'moderator', 'admin')", name="valid_role"),
        CheckConstraint("char_length(name) BETWEEN 2 AND 50", name="name_length"),
    )
