from __future__ import annotations

import ipaddress

from cptv.services import geoip


def test_lookup_none_address():
    assert geoip.lookup(None) is None


def test_lookup_private_skipped():
    # Private addresses never hit the reader.
    assert geoip.lookup(ipaddress.ip_address("192.168.1.1")) is None
    assert geoip.lookup(ipaddress.ip_address("10.0.0.1")) is None


def test_lookup_public_without_db_returns_none():
    # No GeoLite2 DB on disk in tests → graceful fallback.
    assert geoip.lookup(ipaddress.ip_address("8.8.8.8")) is None
