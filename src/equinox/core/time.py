from datetime import datetime, timezone
from typing import Optional


def utc_now(ts: Optional[datetime] = None) -> datetime:
    """Return *ts* (converted to naive UTC) if given, else the current naive UTC time.

    All returned datetimes are **naive** (no ``tzinfo``) and represent UTC.
    If *ts* carries timezone info it is first converted to UTC, then stripped.
    """
    if ts is not None:
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        return ts.replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None)
