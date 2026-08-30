"""Time helpers, kept in one place so tests can inject a fixed clock."""

from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Render a datetime as a ``Z``-suffixed ISO-8601 string."""
    return (
        moment.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def parse_iso(value: str) -> datetime:
    """
    Parse an ISO-8601 timestamp into a timezone-aware UTC datetime.

    Raises:
        ValueError: If the value is not a parseable timestamp.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Not a timestamp: {value!r}")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def try_parse_iso(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, returning None instead of raising."""
    try:
        return parse_iso(value)
    except (ValueError, TypeError):
        return None
