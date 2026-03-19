from datetime import datetime, timezone
from typing import Optional


def utc_now(ts: Optional[datetime] = None) -> datetime:
    """Return the current UTC time as a naive datetime (no tzinfo)."""
    tzinfo = None
    if ts:
        tzinfo = ts.astimezone(timezone.utc).tzinfo
    return datetime.now(timezone.utc).replace(tzinfo=tzinfo)
