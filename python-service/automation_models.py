from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, JSON, String

from database import Base


class AutomationJob(Base):
    """A durable hand-off between dashboard requests and n8n workers."""

    __tablename__ = "automation_jobs"

    id = Column(BigInteger, primary_key=True)
    job_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    payload = Column(JSON, nullable=False, default=dict)
    result = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    created_by = Column(String, nullable=False, default="dashboard")
    attempts = Column(BigInteger, nullable=False, default=0)
    claim_token = Column(String, nullable=True, index=True)
    lease_expires_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class IntakeRequest(Base):
    """A one-time, expiring request for missing company due-diligence data."""

    __tablename__ = "intake_requests"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False, default="pending", index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    requested_fields = Column(JSON, nullable=False, default=list)
    recipient_name = Column(String, nullable=True)
    recipient_email = Column(String, nullable=False)
    message = Column(String, nullable=True)
    response = Column(JSON, nullable=True)
    created_by = Column(String, nullable=False, default="dashboard")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
