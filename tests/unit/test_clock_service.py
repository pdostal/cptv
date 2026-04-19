from __future__ import annotations

import re
from datetime import UTC, datetime

from cptv.services import clock


def test_now_is_utc():
    value = clock.now()
    assert value.tzinfo is UTC


def test_iso_now_ends_with_z():
    value = clock.iso_now()
    assert value.endswith("Z")


def test_iso_now_parseable():
    value = clock.iso_now()
    # drop trailing Z and parse as aware UTC
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_iso_now_no_microseconds():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", clock.iso_now())
