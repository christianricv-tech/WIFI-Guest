"""003_create_audit_logs

Create the ``audit_logs`` table with CHECK constraints and indexes for
common query and filter patterns.

Requirements: 12.4, 12.5

Revision ID: 003
Revises: 002
Create Date: 2024-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# ---------------------------------------------------------------------------
# Alembic revision identifiers
# ---------------------------------------------------------------------------
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the ``audit_logs`` table."""
    op.create_table(
        "audit_logs",
        # Auto-incrementing surrogate key (BIGSERIAL)
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        # UTC timestamp of the authentication attempt — defaults to NOW()
        sa.Column(
            "logged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        # Voucher code as submitted; recorded even when not found in vouchers table
        sa.Column("voucher_code", sa.String(12), nullable=False),
        # Client MAC from RADIUS Calling-Station-Id (AA:BB:CC:DD:EE:FF); nullable
        sa.Column("client_mac", sa.String(17), nullable=True),
        # Validation outcome: 'accepted' or 'rejected'
        sa.Column("outcome", sa.String(8), nullable=False),
        # Reason for rejection when outcome='rejected'; NULL on accept
        sa.Column("rejection_reason", sa.String(32), nullable=True),
        # Integration path that produced this entry; defaults to 'rlm_rest'
        sa.Column(
            "source",
            sa.String(8),
            nullable=False,
            server_default="rlm_rest",
        ),
        # CHECK constraint: outcome must be one of the two valid values
        sa.CheckConstraint(
            "outcome IN ('accepted', 'rejected')",
            name="ck_audit_logs_outcome",
        ),
        # CHECK constraint: source must be one of the two integration paths
        sa.CheckConstraint(
            "source IN ('rlm_rest', 'rlm_sql')",
            name="ck_audit_logs_source",
        ),
    )

    # Indexes for audit log query patterns (Req 7.4)
    op.create_index(
        "idx_audit_logs_logged_at",
        "audit_logs",
        [sa.text("logged_at DESC")],
    )
    op.create_index("idx_audit_logs_voucher_code", "audit_logs", ["voucher_code"])
    op.create_index("idx_audit_logs_client_mac", "audit_logs", ["client_mac"])
    op.create_index("idx_audit_logs_outcome", "audit_logs", ["outcome"])


def downgrade() -> None:
    """Drop the ``audit_logs`` table and all associated objects."""
    op.drop_index("idx_audit_logs_outcome", table_name="audit_logs")
    op.drop_index("idx_audit_logs_client_mac", table_name="audit_logs")
    op.drop_index("idx_audit_logs_voucher_code", table_name="audit_logs")
    op.drop_index("idx_audit_logs_logged_at", table_name="audit_logs")
    op.drop_table("audit_logs")
