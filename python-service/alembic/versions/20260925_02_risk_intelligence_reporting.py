"""Risk dimensions, score history, review decisions, and report snapshots.

Revision ID: 20260925_02
Revises: 20260924_01
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "20260925_02"
down_revision = "20260924_01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "risk_dimension_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), sa.ForeignKey("compliance_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dimension", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("score", sa.BigInteger(), nullable=True),
        sa.Column("confidence", sa.String(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "dimension", name="uq_risk_dimension_snapshot"),
    )
    op.create_index("ix_risk_dimension_snapshots_vendor_id", "risk_dimension_snapshots", ["vendor_id"])
    op.create_index("ix_risk_dimension_snapshots_snapshot_id", "risk_dimension_snapshots", ["snapshot_id"])

    op.create_table(
        "score_change_records",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), sa.ForeignKey("compliance_snapshots.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("previous_snapshot_id", sa.BigInteger(), sa.ForeignKey("compliance_snapshots.id"), nullable=True),
        sa.Column("previous_score", sa.BigInteger(), nullable=True),
        sa.Column("current_score", sa.BigInteger(), nullable=False),
        sa.Column("score_delta", sa.BigInteger(), nullable=True),
        sa.Column("trend", sa.String(), nullable=False),
        sa.Column("explanation", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_score_change_records_vendor_id", "score_change_records", ["vendor_id"])

    op.create_table(
        "risk_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("rule_key", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=True),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("conditions", sa.JSON(), nullable=False),
        sa.Column("base_delta", sa.BigInteger(), nullable=True),
        sa.Column("affects_score", sa.Boolean(), nullable=False),
        sa.Column("affects_alert", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_risk_rules_rule_key", "risk_rules", ["rule_key"])

    op.create_table(
        "source_reliability_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source", sa.String(), nullable=False, unique=True),
        sa.Column("reliability", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_source_reliability_configs_source", "source_reliability_configs", ["source"])

    op.create_table(
        "event_review_decisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("event_id", sa.BigInteger(), sa.ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("decision", sa.String(), nullable=False),
        sa.Column("reviewer", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_event_review_decisions_event_id", "event_review_decisions", ["event_id"])

    op.create_table(
        "audit_report_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("vendor_id", sa.BigInteger(), sa.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), sa.ForeignKey("compliance_snapshots.id", ondelete="SET NULL"), nullable=True),
        sa.Column("report_version", sa.String(), nullable=False),
        sa.Column("generated_by", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_report_snapshots_vendor_id", "audit_report_snapshots", ["vendor_id"])

    now = datetime.utcnow()
    sources = sa.table("source_reliability_configs", sa.column("source", sa.String), sa.column("reliability", sa.String), sa.column("enabled", sa.Boolean), sa.column("description", sa.String), sa.column("metadata", sa.JSON), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))
    op.bulk_insert(sources, [
        {"source": "companies_house", "reliability": "PRIMARY", "enabled": True, "description": "Official UK corporate registry source.", "metadata": {"data_types": ["corporate", "financial", "ownership"]}, "created_at": now, "updated_at": now},
        {"source": "uk_sanctions_list", "reliability": "AUTHORITATIVE", "enabled": True, "description": "Official UK sanctions list for screening candidates.", "metadata": {"data_types": ["compliance"], "limitation": "Name similarity is not identity confirmation."}, "created_at": now, "updated_at": now},
        {"source": "gazette", "reliability": "PRIMARY", "enabled": False, "description": "Reserved for future Gazette notice evidence.", "metadata": {"data_types": ["corporate", "financial"]}, "created_at": now, "updated_at": now},
        {"source": "news", "reliability": "SECONDARY", "enabled": False, "description": "Reserved for future reputation evidence.", "metadata": {"data_types": ["reputation"]}, "created_at": now, "updated_at": now},
        {"source": "pep", "reliability": "EXTERNAL_SIGNAL", "enabled": False, "description": "Reserved for a future PEP provider.", "metadata": {"data_types": ["compliance"]}, "created_at": now, "updated_at": now},
        {"source": "debarment", "reliability": "EXTERNAL_SIGNAL", "enabled": False, "description": "Reserved for future debarment providers.", "metadata": {"data_types": ["compliance"]}, "created_at": now, "updated_at": now},
        {"source": "regulatory_enforcement", "reliability": "EXTERNAL_SIGNAL", "enabled": False, "description": "Reserved for regulatory enforcement sources.", "metadata": {"data_types": ["compliance", "reputation"]}, "created_at": now, "updated_at": now},
        {"source": "cyber", "reliability": "EXTERNAL_SIGNAL", "enabled": False, "description": "Reserved for passive cyber-risk providers.", "metadata": {"data_types": ["cyber"]}, "created_at": now, "updated_at": now},
    ])

    rules = sa.table("risk_rules", sa.column("rule_key", sa.String), sa.column("name", sa.String), sa.column("category", sa.String), sa.column("event_type", sa.String), sa.column("severity", sa.String), sa.column("conditions", sa.JSON), sa.column("base_delta", sa.BigInteger), sa.column("affects_score", sa.Boolean), sa.column("affects_alert", sa.Boolean), sa.column("enabled", sa.Boolean), sa.column("version", sa.String), sa.column("created_at", sa.DateTime), sa.column("updated_at", sa.DateTime))
    op.bulk_insert(rules, [
        {"rule_key": "officer_resignation", "name": "Officer resignation monitoring rule", "category": "corporate", "event_type": "OFFICER_CHANGED", "severity": "low", "conditions": {"signal": "recent_resignations", "minimum": 1}, "base_delta": 0, "affects_score": False, "affects_alert": True, "enabled": True, "version": "1.0.0", "created_at": now, "updated_at": now},
        {"rule_key": "new_charge", "name": "New charge monitoring rule", "category": "financial", "event_type": "CHARGE_REGISTERED", "severity": "medium", "conditions": {"source": "companies_house"}, "base_delta": None, "affects_score": False, "affects_alert": True, "enabled": True, "version": "1.0.0", "created_at": now, "updated_at": now},
        {"rule_key": "sanctions_review", "name": "Sanctions candidate review rule", "category": "compliance", "event_type": "SANCTIONS_REVIEW_REQUIRED", "severity": "high", "conditions": {"manual_review_required": True}, "base_delta": None, "affects_score": False, "affects_alert": True, "enabled": True, "version": "1.0.0", "created_at": now, "updated_at": now},
        {"rule_key": "confirmed_sanctions_match", "name": "Confirmed sanctions decision rule", "category": "compliance", "event_type": "SANCTIONS_CONFIRMED_MATCH", "severity": "critical", "conditions": {"requires_human_confirmation": True}, "base_delta": None, "affects_score": False, "affects_alert": True, "enabled": True, "version": "1.0.0", "created_at": now, "updated_at": now},
    ])


def downgrade():
    op.drop_index("ix_audit_report_snapshots_vendor_id", table_name="audit_report_snapshots")
    op.drop_table("audit_report_snapshots")
    op.drop_index("ix_event_review_decisions_event_id", table_name="event_review_decisions")
    op.drop_table("event_review_decisions")
    op.drop_index("ix_source_reliability_configs_source", table_name="source_reliability_configs")
    op.drop_table("source_reliability_configs")
    op.drop_index("ix_risk_rules_rule_key", table_name="risk_rules")
    op.drop_table("risk_rules")
    op.drop_index("ix_score_change_records_vendor_id", table_name="score_change_records")
    op.drop_table("score_change_records")
    op.drop_index("ix_risk_dimension_snapshots_snapshot_id", table_name="risk_dimension_snapshots")
    op.drop_index("ix_risk_dimension_snapshots_vendor_id", table_name="risk_dimension_snapshots")
    op.drop_table("risk_dimension_snapshots")
