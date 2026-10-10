import io
from datetime import datetime

def generate_ics(title: str, date_str: str, description: str = "") -> io.BytesIO:
    """Generate an iCalendar (.ics) file for an all-day event."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dt_str = dt.strftime("%Y%m%d")
    
    # Next day for all-day event end
    from datetime import timedelta
    dt_end = dt + timedelta(days=1)
    dt_end_str = dt_end.strftime("%Y%m%d")
    
    dt_stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    
    uid = f"squares-vault-{dt_str}-{hash(title)}"
    
    content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//SQUARES Vault//Bot//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:{uid}
DTSTAMP:{dt_stamp}
DTSTART;VALUE=DATE:{dt_str}
DTEND;VALUE=DATE:{dt_end_str}
SUMMARY:SQUARES Vault: {title}
DESCRIPTION:{description}
SEQUENCE:0
STATUS:CONFIRMED
TRANSP:TRANSPARENT
END:VEVENT
END:VCALENDAR"""
    
    bio = io.BytesIO(content.encode("utf-8"))
    bio.name = f"{title}.ics"
    return bio
