from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    """RFC 3339 / ISO 8601 timestamp with trailing Z."""
    return now().replace(microsecond=0).isoformat().replace("+00:00", "Z")
