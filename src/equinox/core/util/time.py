from datetime import datetime, timezone


def utc_now(ts: datetime | None = None) -> datetime:
    """Return *ts* (converted to naive UTC) if given, else the current naive UTC time.

    All returned datetimes are **naive** (no ``tzinfo``) and represent UTC.
    If *ts* carries timezone info it is first converted to UTC, then stripped.
    """
    if ts is not None:
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        return ts.replace(tzinfo=None)
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_iso_z(dt: datetime | None = None) -> str:
    """Return *dt* (or now) as an ISO 8601 string with a trailing ``Z``.

    Uses ``strftime`` directly to produce the compact ``…Z`` suffix rather
    than the ``+00:00`` suffix emitted by :meth:`datetime.isoformat` on
    timezone-aware objects.

    Args:
        dt: Datetime to format.  When ``None`` the current UTC time is used.
            Naive datetimes are assumed to be UTC.

    Returns:
        String of the form ``"2026-04-14T12:00:00.000Z"``.
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
