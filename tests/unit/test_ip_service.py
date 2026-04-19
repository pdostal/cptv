from __future__ import annotations

import ipaddress

import pytest

from cptv.services.ip import classify, parse_forwarded_for


class TestParseForwardedFor:
    def test_none_when_header_missing(self):
        assert parse_forwarded_for(None) is None

    def test_empty_string(self):
        assert parse_forwarded_for("") is None

    def test_single_ip(self):
        assert parse_forwarded_for("203.0.113.42") == "203.0.113.42"

    def test_takes_leftmost_of_list(self):
        assert parse_forwarded_for("203.0.113.42, 10.0.0.1, 192.168.1.1") == "203.0.113.42"

    def test_strips_whitespace(self):
        assert parse_forwarded_for("  203.0.113.42  ") == "203.0.113.42"

    def test_ipv6(self):
        assert parse_forwarded_for("2001:db8::1") == "2001:db8::1"


class TestClassify:
    def test_public_ipv4(self):
        result = classify(ipaddress.ip_address("8.8.8.8"))
        assert result.protocol == "IPv4"
        assert result.is_private is False
        assert result.is_cgnat is False

    def test_private_ipv4(self):
        result = classify(ipaddress.ip_address("192.168.1.1"))
        assert result.protocol == "IPv4"
        assert result.is_private is True
        assert result.is_cgnat is False

    def test_cgnat_range(self):
        result = classify(ipaddress.ip_address("100.64.0.1"))
        assert result.is_cgnat is True

    def test_cgnat_upper_bound(self):
        result = classify(ipaddress.ip_address("100.127.255.254"))
        assert result.is_cgnat is True

    def test_outside_cgnat(self):
        result = classify(ipaddress.ip_address("100.63.255.255"))
        assert result.is_cgnat is False

    def test_public_ipv6(self):
        result = classify(ipaddress.ip_address("2001:db8::1"))
        assert result.protocol == "IPv6"
        assert result.is_cgnat is False


def test_text_representation():
    result = classify(ipaddress.ip_address("203.0.113.42"))
    assert result.text == "203.0.113.42"


def test_invalid_ip_raises():
    with pytest.raises(ValueError):
        ipaddress.ip_address("not an ip")
