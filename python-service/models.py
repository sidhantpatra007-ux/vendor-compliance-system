from sqlalchemy import (
    Column, BigInteger, String, DateTime, ForeignKey, JSON, Boolean,
    Numeric, Date, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class Vendor(Base):
    """
    ONLY onboarding form data — what a human typed in. Never touched by
    OCR/XBRL extraction; that lives in FinancialRecord instead. Kept
    deliberately separate so intake data and extracted data can never
    silently overwrite each other.
    """
    __tablename__ = "vendors"

    id = Column(BigInteger, primary_key=True)
    company_number = Column(String, nullable=False)
    display_name = Column(String, nullable=False)

    trading_name = Column(String, nullable=True)
    address_street = Column(String, nullable=True)
    address_city = Column(String, nullable=True)
    address_postcode = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    vendor_category = Column(String, nullable=True)
    goods_or_services = Column(String, nullable=True)
    supplier_criticality = Column(String, nullable=True)
    annual_spend_band = Column(String, nullable=True)
    access_to_client_systems_or_data = Column(String, nullable=True)
    processes_personal_data = Column(String, nullable=True)
    delivery_countries = Column(String, nullable=True)
    uses_subcontractors = Column(String, nullable=True)
    supplier_declaration_accepted = Column(Boolean, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    snapshots = relationship(
        "ComplianceSnapshot", back_populates="vendor", order_by="ComplianceSnapshot.checked_at"
    )
    financial_records = relationship("FinancialRecord", back_populates="vendor")
    filing_evidence_pages = relationship("FilingEvidencePage", back_populates="vendor")
    company_events = relationship("CompanyEvent", back_populates="vendor")
    alerts = relationship("Alert", back_populates="vendor")
    review_decisions = relationship("ReviewerDecision", back_populates="vendor", order_by="ReviewerDecision.created_at.desc()")

    __table_args__ = (UniqueConstraint("company_number", name="uq_vendor_company"),)


class ComplianceSnapshot(Base):
    """
    The COMPUTED score for one point-in-time check — composite_score,
    risk_grade, signals/categories/factors. Never overwritten; every
    refresh creates a new row, so history and score-drop diffing both
    work off this table. Holds no raw extracted numbers itself — those
    live in FinancialRecord, linked back here via snapshot_id.
    """
    __tablename__ = "compliance_snapshots"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=False)
    checked_at = Column(DateTime, default=datetime.utcnow)

    composite_score = Column(BigInteger, nullable=False)
    risk_grade = Column(String, nullable=False)
    scoring_version = Column(String, nullable=False)

    signals = Column(JSON, nullable=False)
    categories = Column(JSON, nullable=False)
    factors = Column(JSON, nullable=False)

    recommend_manual_review = Column(Boolean, default=False, nullable=False)

    vendor = relationship("Vendor", back_populates="snapshots")
    financial_records = relationship("FinancialRecord", back_populates="snapshot")


class FinancialRecord(Base):
    """
    ONE ROW PER EXTRACTED LINE ITEM (net_assets, turnover, cash, etc),
    not one JSON blob per check — this is the table a client actually
    studies when something looks wrong. evidence_image_path points at a
    file in Supabase Storage, NOT a base64 blob (fixes the 1.6MB n8n
    payload problem at the source, not just downstream of it).

    Also replaces the old separate VendorOverride table: a client
    correction lives directly on the record it corrects (client_confirmed*
    columns below), scoped to VENDOR + concept across refreshes — the
    override survives into the next snapshot for the same concept unless
    the client changes it again. Never influences composite_score/
    risk_grade, which are computed from real signals only, upstream of
    any override.
    """
    __tablename__ = "financial_records"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=False)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id"), nullable=False)

    concept = Column(String, nullable=False)          # "net_assets", "turnover", "cash"
    value = Column(Numeric, nullable=True)
    currency = Column(String, nullable=True)
    extraction_method = Column(String, nullable=False)  # "xbrl" | "ocr" | "pdf_text"
    state = Column(String, nullable=False)               # "PRESENT" | "AMBIGUOUS_MULTIPLE_VALUES" | "NIL"

    evidence_image_path = Column(String, nullable=True)  # Supabase Storage path
    evidence_page = Column(BigInteger, nullable=True)
    source_filing_date = Column(Date, nullable=True)

    client_confirmed = Column(Boolean, default=False, nullable=False)
    client_confirmed_value = Column(Numeric, nullable=True)
    client_confirmed_by = Column(String, nullable=True)
    client_confirmed_at = Column(DateTime, nullable=True)
    client_note = Column(String, nullable=True)

    extracted_at = Column(DateTime, default=datetime.utcnow)

    vendor = relationship("Vendor", back_populates="financial_records")
    snapshot = relationship("ComplianceSnapshot", back_populates="financial_records")


class FilingEvidencePage(Base):
    __tablename__ = "filing_evidence_pages"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=False, index=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id"), nullable=False, index=True)
    page_number = Column(BigInteger, nullable=False)
    image_path = Column(String, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    vendor = relationship("Vendor", back_populates="filing_evidence_pages")
    snapshot = relationship("ComplianceSnapshot")

    __table_args__ = (UniqueConstraint("snapshot_id", "page_number", name="uq_filing_evidence_page"),)


class StreamCheckpoint(Base):
    """Last safely processed Companies House event for one stream."""
    __tablename__ = "stream_checkpoints"

    stream_name = Column(String, primary_key=True)
    timepoint = Column(BigInteger, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class CompanyEvent(Base):
    """
    An immutable audit record of a Companies House stream event relevant to a
    monitored vendor.  The unique stream/timepoint pair makes reconnects safe:
    the stream can redeliver an event without creating duplicate snapshots.
    """
    __tablename__ = "company_events"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=True)
    company_number = Column(String, nullable=True, index=True)
    stream_name = Column(String, nullable=False)
    timepoint = Column(BigInteger, nullable=False)
    event_type = Column(String, nullable=False)
    resource_uri = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id"), nullable=True)
    processing_error = Column(String, nullable=True)
    payload = Column(JSON, nullable=False)

    vendor = relationship("Vendor", back_populates="company_events")
    snapshot = relationship("ComplianceSnapshot")

    __table_args__ = (
        UniqueConstraint("stream_name", "timepoint", "vendor_id", name="uq_stream_event_vendor"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=False, index=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id"), nullable=False)
    dedup_key = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False)
    status = Column(String, nullable=False, default="open")
    title = Column(String, nullable=False)
    reason = Column(String, nullable=False)
    evidence = Column(JSON, nullable=False, default=list)
    assigned_to = Column(String, nullable=True)
    acknowledged_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolution_note = Column(String, nullable=True)
    sla_due_at = Column(DateTime, nullable=False)
    escalated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vendor = relationship("Vendor", back_populates="alerts")
    snapshot = relationship("ComplianceSnapshot")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, nullable=True, index=True)
    alert_id = Column(BigInteger, nullable=True, index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ReviewerDecision(Base):
    __tablename__ = "reviewer_decisions"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=False, index=True)
    decision = Column(String, nullable=False)
    reviewer = Column(String, nullable=False)
    note = Column(String, nullable=True)
    next_review_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    vendor = relationship("Vendor", back_populates="review_decisions")


class JobRun(Base):
    __tablename__ = "job_runs"

    id = Column(BigInteger, primary_key=True)
    job_type = Column(String, nullable=False)
    status = Column(String, nullable=False)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    items_total = Column(BigInteger, default=0, nullable=False)
    items_succeeded = Column(BigInteger, default=0, nullable=False)
    items_failed = Column(BigInteger, default=0, nullable=False)
    error = Column(String, nullable=True)
