"""Operational alerts, financial evidence review, and dashboard coverage.

Revision ID: 20260926_03
Revises: 20260925_02
"""
from alembic import op
import sqlalchemy as sa


revision = "20260926_03"
down_revision = "20260925_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("alerts", sa.Column("acknowledged_by", sa.String(), nullable=True))
    op.add_column("alerts", sa.Column("resolved_by", sa.String(), nullable=True))
    op.add_column("alerts", sa.Column("notification_state", sa.String(), nullable=False, server_default="pending"))
    op.add_column("alerts", sa.Column("notification_count", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("alerts", sa.Column("last_notified_at", sa.DateTime(), nullable=True))
    op.alter_column("alerts", "notification_state", server_default=None)
    op.alter_column("alerts", "notification_count", server_default=None)

    op.create_table(
        "alert_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("alert_id", sa.BigInteger(), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_alert_actions_alert_id", "alert_actions", ["alert_id"])

    op.create_table(
        "financial_evidence_reviews",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), sa.ForeignKey("compliance_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("reviewer", sa.String(), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("vendor_id", "snapshot_id", name="uq_financial_evidence_review_snapshot"),
    )
    op.create_index("ix_financial_evidence_reviews_vendor_id", "financial_evidence_reviews", ["vendor_id"])
    op.create_index("ix_financial_evidence_reviews_snapshot_id", "financial_evidence_reviews", ["snapshot_id"])
    op.execute("""
        INSERT INTO financial_evidence_reviews (vendor_id, snapshot_id, status, created_at, updated_at)
        SELECT cs.vendor_id, cs.id,
               CASE WHEN EXISTS (
                   SELECT 1 FROM filing_evidence_pages fp WHERE fp.snapshot_id = cs.id
               ) THEN 'pending' ELSE 'unavailable' END,
               NOW(), NOW()
        FROM compliance_snapshots cs
        ON CONFLICT ON CONSTRAINT uq_financial_evidence_review_snapshot DO NOTHING
    """)


def downgrade():
    op.drop_index("ix_financial_evidence_reviews_snapshot_id", table_name="financial_evidence_reviews")
    op.drop_index("ix_financial_evidence_reviews_vendor_id", table_name="financial_evidence_reviews")
    op.drop_table("financial_evidence_reviews")
    op.drop_index("ix_alert_actions_alert_id", table_name="alert_actions")
    op.drop_table("alert_actions")
    op.drop_column("alerts", "last_notified_at")
    op.drop_column("alerts", "notification_count")
    op.drop_column("alerts", "notification_state")
    op.drop_column("alerts", "resolved_by")
    op.drop_column("alerts", "acknowledged_by")
