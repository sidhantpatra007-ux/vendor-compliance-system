"""Bulk company onboarding and durable n8n automation jobs.

Revision ID: 20261002_05
Revises: 20260927_04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261002_05"
down_revision = "20260927_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "automation_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_by", sa.String(), nullable=False, server_default="dashboard"),
        sa.Column("attempts", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_automation_jobs_job_type", "automation_jobs", ["job_type"])
    op.create_index("ix_automation_jobs_status", "automation_jobs", ["status"])
    op.create_index("ix_automation_jobs_claim_token", "automation_jobs", ["claim_token"])
    op.create_index("ix_automation_jobs_lease_expires_at", "automation_jobs", ["lease_expires_at"])


def downgrade():
    op.drop_index("ix_automation_jobs_lease_expires_at", table_name="automation_jobs")
    op.drop_index("ix_automation_jobs_claim_token", table_name="automation_jobs")
    op.drop_index("ix_automation_jobs_status", table_name="automation_jobs")
    op.drop_index("ix_automation_jobs_job_type", table_name="automation_jobs")
    op.drop_table("automation_jobs")
