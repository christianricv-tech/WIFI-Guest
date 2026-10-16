from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import Base


class AuditLog(Base):
    """
    Persistent record of every RADIUS authentication attempt.

    Written for both accepted and rejected outcomes (Req 7.1, 7.2).
    The voucher_code is stored exactly as submitted, even when the code
    does not exist in the vouchers table.
    """

    __tablename__ = "audit_logs"

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted', 'rejected')",
            name="ck_audit_logs_outcome",
        ),
        CheckConstraint(
            "source IN ('rlm_rest', 'rlm_sql')",
            name="ck_audit_logs_source",
        ),
        # Indexes for common query patterns (Req 7.4)
        Index("idx_audit_logs_logged_at", "logged_at", postgresql_ops={"logged_at": "DESC"}),
        Index("idx_audit_logs_voucher_code", "voucher_code"),
        Index("idx_audit_logs_client_mac", "client_mac"),
        Index("idx_audit_logs_outcome", "outcome"),
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="Auto-incrementing surrogate key (BIGSERIAL).",
    )

    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="UTC timestamp of the authentication attempt.",
    )

    voucher_code: Mapped[str] = mapped_column(
        String(12),
        nullable=False,
        comment="Voucher code as submitted; recorded even when not found in vouchers table.",
    )

    client_mac: Mapped[str | None] = mapped_column(
        String(17),
        nullable=True,
        comment="Client MAC address from RADIUS Calling-Station-Id (format AA:BB:CC:DD:EE:FF); nullable.",
    )

    outcome: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        comment="Validation outcome: 'accepted' or 'rejected'.",
    )

    rejection_reason: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment=(
            "Reason for rejection when outcome is 'rejected': "
            "not_found | consumed | revoked | expired | invalid_state. "
            "NULL when outcome is 'accepted'."
        ),
    )

    source: Mapped[str] = mapped_column(
        String(8),
        nullable=False,
        server_default="rlm_rest",
        comment="Integration path that produced this log entry: 'rlm_rest' or 'rlm_sql'.",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<AuditLog id={self.id} logged_at={self.logged_at!r} "
            f"voucher_code={self.voucher_code!r} outcome={self.outcome!r}>"
        )
