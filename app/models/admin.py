"""
SQLAlchemy model for the `admins` table.

Stores admin accounts whose passwords are always bcrypt-hashed (cost >= 10),
satisfying Requirement 11.2: plaintext passwords are never stored.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    username: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    # bcrypt hash only — plaintext passwords are never stored (Requirement 11.2)
    password_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_admins_username", "username"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Admin id={self.id!s} username={self.username!r}>"
