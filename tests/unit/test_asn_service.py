from __future__ import annotations

import ipaddress

from cptv.services import asn


def test_lookup_none_address():
    assert asn.lookup(None) is None


def test_lookup_private_skipped():
    assert asn.lookup(ipaddress.ip_address("192.168.1.1")) is None


def test_lookup_public_without_db_returns_none():
    assert asn.lookup(ipaddress.ip_address("8.8.8.8")) is None


def test_looking_glass_url_format():
    from cptv.services.asn import ASN

    record = ASN(number=1234, name="Example", prefix="203.0.113.0/24")
    assert record.looking_glass == "https://bgp.he.net/AS1234"
