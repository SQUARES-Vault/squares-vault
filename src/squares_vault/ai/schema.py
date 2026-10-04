"""JSON schemas for AI extraction."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field

class ExtractedRecord(BaseModel):
    """The strict JSON schema the AI must return."""
    category: str = Field(description="One of: vehicle, warranty & purchases, people & occasions, bills & subscriptions, insurance, ids & government docs, health, home, custom")
    subtype: Optional[str] = Field(None, description="E.g., RC, Insurance, Invoice, Life, Passport")
    title: str = Field(description="A short, clear title for the record")
    entity_name: Optional[str] = Field(None, description="The name of the vehicle, person, product, or property")
    key_date: Optional[str] = Field(None, description="The most important date on this document (YYYY-MM-DD)")
    key_date_type: Optional[str] = Field(None, description="One of: expiry, due, event, renewal, end")
    amount_minor: Optional[int] = Field(None, description="Amount in minor units (e.g. paise for INR) if applicable")
    currency: Optional[str] = Field("INR", description="Currency code, e.g. INR")
    
    # Category extras
    vehicle_number: Optional[str] = Field(None, description="Registration number if it's a vehicle document")
    provider: Optional[str] = Field(None, description="Name of the company/provider")
    document_number: Optional[str] = Field(None, description="Full unmasked document number, policy number, or ID number")
    purchase_date: Optional[str] = Field(None, description="Date of purchase (YYYY-MM-DD)")
    warranty_months: Optional[int] = Field(None, description="Number of months of warranty")
    
    notes: Optional[str] = Field(None, description="Any other key details or context")
