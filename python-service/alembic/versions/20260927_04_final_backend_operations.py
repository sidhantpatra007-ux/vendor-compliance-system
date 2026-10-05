"""Final backend operations: policy metadata, retention, and refresh locks.

Revision ID: 20260927_04
Revises: 20260926_03
"""
from alembic import op
import sqlalchemy as sa


revision = "20260927_04"
down_revision = "20260926_03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vendors", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("compliance_snapshots", sa.Column("financial_document_hash", sa.String(), nullable=True))
    op.create_index("ix_compliance_snapshots_financial_document_hash", "compliance_snapshots", ["financial_document_hash"])
    op.add_column("alerts", sa.Column("notification_last_error", sa.String(), nullable=True))
    op.create_table(
        "vendor_refresh_locks",
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("acquired_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("vendor_refresh_locks")
    op.drop_column("alerts", "notification_last_error")
    op.drop_index("ix_compliance_snapshots_financial_document_hash", table_name="compliance_snapshots")
    op.drop_column("compliance_snapshots", "financial_document_hash")
    op.drop_column("vendors", "archived_at")
