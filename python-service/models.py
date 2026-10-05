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
    archived_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    snapshots = relationship(
        "ComplianceSnapshot", back_populates="vendor", order_by="ComplianceSnapshot.checked_at"
    )
    financial_records = relationship("FinancialRecord", back_populates="vendor")
    filing_evidence_pages = relationship("FilingEvidencePage", back_populates="vendor")
    company_events = relationship("CompanyEvent", back_populates="vendor")
    risk_events = relationship("RiskEvent", back_populates="vendor")
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
    financial_document_hash = Column(String, nullable=True, index=True)

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


class RiskEvent(Base):
    """Normalized source event. Events are evidence, not score decisions."""
    __tablename__ = "risk_events"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id"), nullable=False, index=True)
    source = Column(String, nullable=False, index=True)
    source_event_id = Column(String, nullable=True)
    event_type = Column(String, nullable=False, index=True)
    category = Column(String, nullable=False, index=True)
    occurred_at = Column(DateTime, nullable=True, index=True)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    title = Column(String, nullable=False)
    description = Column(String, nullable=True)
    raw_payload = Column(JSON, nullable=True)
    normalized_payload = Column(JSON, nullable=False, default=dict)
    source_url = Column(String, nullable=True)
    source_timestamp = Column(DateTime, nullable=True)
    confidence = Column(String, nullable=False, default="MEDIUM")
    source_reliability = Column(String, nullable=False)
    severity = Column(String, nullable=False, default="INFO")
    fingerprint = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    vendor = relationship("Vendor", back_populates="risk_events")
    evidence_items = relationship("EventEvidence", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("vendor_id", "source", "fingerprint", name="uq_risk_event_fingerprint"),
    )


class EventEvidence(Base):
    __tablename__ = "event_evidence"

    id = Column(BigInteger, primary_key=True)
    event_id = Column(BigInteger, ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String, nullable=False)
    source_url = Column(String, nullable=True)
    source_record_id = Column(String, nullable=True)
    retrieved_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    occurred_at = Column(DateTime, nullable=True)
    source_version = Column(String, nullable=True)
    raw_reference = Column(String, nullable=True)
    content_hash = Column(String, nullable=False, index=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    event = relationship("RiskEvent", back_populates="evidence_items")


class SourceSyncState(Base):
    __tablename__ = "source_sync_states"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    source = Column(String, nullable=False)
    status = Column(String, nullable=False, default="unknown")
    last_successful_sync = Column(DateTime, nullable=True)
    last_attempted_sync = Column(DateTime, nullable=True)
    source_timestamp = Column(DateTime, nullable=True)
    last_error = Column(String, nullable=True)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("vendor_id", "source", name="uq_source_sync_vendor_source"),
    )


class RiskDimensionSnapshot(Base):
    __tablename__ = "risk_dimension_snapshots"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    dimension = Column(String, nullable=False)
    state = Column(String, nullable=False)
    score = Column(BigInteger, nullable=True)
    confidence = Column(String, nullable=False)
    summary = Column(String, nullable=False)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("snapshot_id", "dimension", name="uq_risk_dimension_snapshot"),)


class ScoreChangeRecord(Base):
    __tablename__ = "score_change_records"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    previous_snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id"), nullable=True)
    previous_score = Column(BigInteger, nullable=True)
    current_score = Column(BigInteger, nullable=False)
    score_delta = Column(BigInteger, nullable=True)
    trend = Column(String, nullable=False)
    explanation = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RiskRule(Base):
    __tablename__ = "risk_rules"

    id = Column(BigInteger, primary_key=True)
    rule_key = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    event_type = Column(String, nullable=True)
    severity = Column(String, nullable=False)
    conditions = Column(JSON, nullable=False, default=dict)
    base_delta = Column(BigInteger, nullable=True)
    affects_score = Column(Boolean, nullable=False, default=False)
    affects_alert = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)
    version = Column(String, nullable=False, default="1.0.0")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SourceReliabilityConfig(Base):
    __tablename__ = "source_reliability_configs"

    id = Column(BigInteger, primary_key=True)
    source = Column(String, nullable=False, unique=True, index=True)
    reliability = Column(String, nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    description = Column(String, nullable=False)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class EventReviewDecision(Base):
    __tablename__ = "event_review_decisions"

    id = Column(BigInteger, primary_key=True)
    event_id = Column(BigInteger, ForeignKey("risk_events.id", ondelete="CASCADE"), nullable=False, index=True)
    decision = Column(String, nullable=False)
    reviewer = Column(String, nullable=False)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AuditReportSnapshot(Base):
    __tablename__ = "audit_report_snapshots"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id", ondelete="SET NULL"), nullable=True)
    report_version = Column(String, nullable=False, default="1.0.0")
    generated_by = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


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
    acknowledged_by = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)
    resolution_note = Column(String, nullable=True)
    sla_due_at = Column(DateTime, nullable=False)
    escalated_at = Column(DateTime, nullable=True)
    notification_state = Column(String, nullable=False, default="pending")
    notification_count = Column(BigInteger, nullable=False, default=0)
    last_notified_at = Column(DateTime, nullable=True)
    notification_last_error = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    vendor = relationship("Vendor", back_populates="alerts")
    snapshot = relationship("ComplianceSnapshot")


class AlertAction(Base):
    __tablename__ = "alert_actions"

    id = Column(BigInteger, primary_key=True)
    alert_id = Column(BigInteger, ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False, index=True)
    action = Column(String, nullable=False)
    actor = Column(String, nullable=True)
    note = Column(String, nullable=True)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FinancialEvidenceReview(Base):
    __tablename__ = "financial_evidence_reviews"

    id = Column(BigInteger, primary_key=True)
    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True)
    snapshot_id = Column(BigInteger, ForeignKey("compliance_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, nullable=False, default="pending")
    reviewer = Column(String, nullable=True)
    note = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("vendor_id", "snapshot_id", name="uq_financial_evidence_review_snapshot"),
    )


class VendorRefreshLock(Base):
    __tablename__ = "vendor_refresh_locks"

    vendor_id = Column(BigInteger, ForeignKey("vendors.id", ondelete="CASCADE"), primary_key=True)
    owner_token = Column(String, nullable=False)
    acquired_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)


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
