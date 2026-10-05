"""Dashboard-originated bulk jobs and questionnaire requests.

Revision ID: 20261002_06
Revises: 20261002_05
"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_06"
down_revision = "20261002_05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "intake_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("requested_fields", sa.JSON(), nullable=False),
        sa.Column("recipient_name", sa.String(), nullable=True),
        sa.Column("recipient_email", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False, server_default="dashboard"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_intake_requests_vendor_id", "intake_requests", ["vendor_id"])
    op.create_index("ix_intake_requests_status", "intake_requests", ["status"])
    op.create_index("ix_intake_requests_token_hash", "intake_requests", ["token_hash"], unique=True)
    op.create_index("ix_intake_requests_expires_at", "intake_requests", ["expires_at"])


def downgrade():
    op.drop_index("ix_intake_requests_expires_at", table_name="intake_requests")
    op.drop_index("ix_intake_requests_token_hash", table_name="intake_requests")
    op.drop_index("ix_intake_requests_status", table_name="intake_requests")
    op.drop_index("ix_intake_requests_vendor_id", table_name="intake_requests")
    op.drop_table("intake_requests")
