"""Normalized risk events, evidence, and source sync state.

Revision ID: 20260924_01
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = "20260924_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "risk_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_event_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=True),
        sa.Column("normalized_payload", sa.JSON(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("source_reliability", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("fingerprint", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("vendor_id", "source", "fingerprint", name="uq_risk_event_fingerprint"),
    )
    for name, columns in (
        ("ix_risk_events_vendor_id", ["vendor_id"]),
        ("ix_risk_events_source", ["source"]),
        ("ix_risk_events_event_type", ["event_type"]),
        ("ix_risk_events_category", ["category"]),
        ("ix_risk_events_occurred_at", ["occurred_at"]),
        ("ix_risk_events_fingerprint", ["fingerprint"]),
    ):
        op.create_index(name, "risk_events", columns)

    op.create_table(
        "event_evidence",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("source_record_id", sa.String(), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("source_version", sa.String(), nullable=True),
        sa.Column("raw_reference", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_event_evidence_event_id", "event_evidence", ["event_id"])
    op.create_index("ix_event_evidence_content_hash", "event_evidence", ["content_hash"])

    op.create_table(
        "source_sync_states",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("last_successful_sync", sa.DateTime(), nullable=True),
        sa.Column("last_attempted_sync", sa.DateTime(), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("vendor_id", "source", name="uq_source_sync_vendor_source"),
    )
    op.create_index("ix_source_sync_states_vendor_id", "source_sync_states", ["vendor_id"])


def downgrade():
    op.drop_index("ix_source_sync_states_vendor_id", table_name="source_sync_states")
    op.drop_table("source_sync_states")
    op.drop_index("ix_event_evidence_content_hash", table_name="event_evidence")
    op.drop_index("ix_event_evidence_event_id", table_name="event_evidence")
    op.drop_table("event_evidence")
    for name in (
        "ix_risk_events_fingerprint", "ix_risk_events_occurred_at", "ix_risk_events_category",
        "ix_risk_events_event_type", "ix_risk_events_source", "ix_risk_events_vendor_id",
    ):
        op.drop_index(name, table_name="risk_events")
    op.drop_table("risk_events")
