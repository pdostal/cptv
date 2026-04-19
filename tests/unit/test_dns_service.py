from __future__ import annotations

from cptv.services.dns import classify_resolver


def test_none_resolver():
    info = classify_resolver(None)
    assert info.resolver_ip is None
    assert info.resolver_name is None
    assert info.is_known_public is False


def test_known_cloudflare_v4():
    info = classify_resolver("1.1.1.1")
    assert info.resolver_ip == "1.1.1.1"
    assert info.resolver_name == "Cloudflare"
    assert info.is_known_public is True


def test_known_google_v4():
    info = classify_resolver("8.8.8.8")
    assert info.resolver_name == "Google"
    assert info.is_known_public is True


def test_known_quad9_v6():
    info = classify_resolver("2620:fe::fe")
    assert info.resolver_name == "Quad9"
    assert info.is_known_public is True


def test_unknown_resolver():
    info = classify_resolver("203.0.113.5")
    assert info.resolver_ip == "203.0.113.5"
    assert info.resolver_name is None
    assert info.is_known_public is False
