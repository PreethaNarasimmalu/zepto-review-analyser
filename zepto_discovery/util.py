"""Small helpers shared across pipeline stages."""

from datetime import datetime, timezone


def parse_iso_datetime(value):
    """Accepts a datetime or an ISO-8601 string; always returns tz-aware UTC."""
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
