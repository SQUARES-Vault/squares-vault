"""Post-extraction validation rules."""

from __future__ import annotations

import re
from datetime import datetime
from dateutil.relativedelta import relativedelta
from squares_vault.ai.schema import ExtractedRecord


def normalize_vehicle_number(v_num: str) -> str:
    """Normalize and validate an Indian vehicle number."""
    if not v_num:
        return ""
    
    # Remove spaces and hyphens, upper case
    clean = re.sub(r'[\s\-]', '', v_num).upper()
    
    # Standard: ^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$
    standard_pattern = r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}$"
    # BH Series: ^\d{2}BH\d{4}[A-Z]{1,2}$
    bh_pattern = r"^\d{2}BH\d{4}[A-Z]{1,2}$"
    
    if re.match(standard_pattern, clean) or re.match(bh_pattern, clean):
        return clean
        
    # If it fails, we still return it but the UI flags it as low confidence.
    # For now, just return it. The frontend handles confidence flags based on regex.
    return clean


def validate_and_enrich(record: ExtractedRecord) -> ExtractedRecord:
    """Apply validation rules and compute fields."""
    
    if record.vehicle_number:
        record.vehicle_number = normalize_vehicle_number(record.vehicle_number)
        
    # Compute warranty end if we have purchase date and months
    if record.category.lower() == "warranty & purchases" and record.purchase_date and record.warranty_months:
        try:
            p_date = datetime.strptime(record.purchase_date, "%Y-%m-%d").date()
            # Warranty end = purchase date + warranty months
            end_date = p_date + relativedelta(months=record.warranty_months)
            
            # If the AI didn't extract a key_date or we want to overwrite it
            record.key_date = end_date.isoformat()
            record.key_date_type = "expiry"
        except ValueError:
            pass
            
    # Dates: verify they are within today +- 30 years
    if record.key_date:
        try:
            d = datetime.strptime(record.key_date, "%Y-%m-%d").date()
            year = d.year
            current_year = datetime.now().year
            if abs(year - current_year) > 30:
                record.key_date = None  # Drop invalid dates
        except ValueError:
            record.key_date = None
            
    return record
