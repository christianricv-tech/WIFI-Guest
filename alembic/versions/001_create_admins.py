"""Create admins table.

Revision ID: 001
Revises: None
Create Date: 2024-01-01 00:00:00.000000

Creates the `admins` table with all columns defined in the Admin SQLAlchemy
model (app/models/admin.py) and the `idx_admins_username` index.

Requirements: 12.4, 12.5
"""

from alembic import op
import sqlalchemy as sa

# Alembic revision identifiers
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admins",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "username",
            sa.String(64),
            nullable=False,
            unique=True,
        ),
        # bcrypt hash only — plaintext passwords are never stored (Requirement 11.2)
        sa.Column(
            "password_hash",
            sa.String(128),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "last_login_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_index(
        "idx_admins_username",
        "admins",
        ["username"],
    )


def downgrade() -> None:
    op.drop_table("admins")
