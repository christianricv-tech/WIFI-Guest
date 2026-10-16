"""004_create_radius_view

Revision ID: 004
Revises: 003
Create Date: 2024-01-01 00:00:00.000000

Creates the radius_voucher_view used by FreeRADIUS rlm_sql to validate voucher
codes directly against PostgreSQL without going through the REST API.

Requirements: 10.3, 10.4
"""

from alembic import op

# revision identifiers, used by Alembic
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE VIEW radius_voucher_view AS
        SELECT
            code AS username,
            code AS password,
            CASE
                WHEN status = 'unused'
                     AND (
                         (activation_mode = 'pre-activated' AND expires_at > NOW())
                         OR (activation_mode = 'use-activated' AND expires_at IS NULL)
                         OR (activation_mode = 'use-activated' AND expires_at > NOW())
                     )
                THEN 1
                ELSE 0
            END AS is_valid
        FROM vouchers;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS radius_voucher_view")
