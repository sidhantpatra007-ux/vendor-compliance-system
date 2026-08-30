from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr


class VendorIntakeRequest(BaseModel):
    legal_company_name: str
    trading_name: Optional[str] = None
    country: str = "United Kingdom"
    registration_number: str
    tax_id: Optional[str] = None

    address_street: str
    address_city: str
    address_postcode: str

    contact_name: str
    contact_email: EmailStr
    contact_phone: str

    bank_account_holder: str
    iban: str
    swift_bic: str

    vendor_category: str


class ValidationResult(BaseModel):
    status: str  # "auto_approved" or "needs_review"
    flags: List[str]
    vendor_id: int


class EventOut(BaseModel):
    event_type: str
    severity: str
    description: str
    created_at: datetime

    class Config:
        from_attributes = True


class VendorHistoryResponse(BaseModel):
    vendor_id: int
    legal_company_name: str
    onboarding_status: str
    events: List[EventOut]