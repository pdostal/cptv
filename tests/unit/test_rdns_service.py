from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, MagicMock, patch

import dns.exception
import dns.resolver
import pytest

from cptv.services import rdns


@pytest.fixture(autouse=True)
def _no_valkey(monkeypatch):
    """Default: simulate Valkey unavailable so cache paths don't kick in
    unless the test explicitly opts in."""
    monkeypatch.setattr(rdns, "get_valkey", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_lookup_none_returns_none() -> None:
    assert await rdns.lookup(None) is None


@pytest.mark.asyncio
async def test_lookup_private_resolves_when_resolver_answers() -> None:
    """Self-hosted deployments often have a local resolver answering for
    RFC1918 PTRs (e.g. silver.local for 192.168.x.x). Don't short-circuit."""
    addr = ipaddress.ip_address("192.168.1.71")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(return_value=_fake_answer("silver.local."))
        result = await rdns.lookup(addr)
    assert result == "silver.local"


@pytest.mark.asyncio
async def test_lookup_private_returns_none_when_resolver_says_nxdomain() -> None:
    """Private IP with no local PTR zone => NXDOMAIN => None (no error)."""
    addr = ipaddress.ip_address("192.168.99.99")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(side_effect=dns.resolver.NXDOMAIN)
        assert await rdns.lookup(addr) is None


def _fake_answer(name: str) -> list:
    """dnspython's PTR rdata exposes .target whose str() yields the name.
    Wrap a real string-coercing object so str(answer[0].target) works."""

    class _Target:
        def __init__(self, value: str) -> None:
            self._value = value

        def __str__(self) -> str:  # noqa: D401
            return self._value

    rdata = MagicMock()
    rdata.target = _Target(name)
    return [rdata]


@pytest.mark.asyncio
async def test_lookup_returns_hostname_on_success() -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(return_value=_fake_answer("host.example.com."))
        result = await rdns.lookup(addr)
    assert result == "host.example.com"  # trailing dot stripped


@pytest.mark.asyncio
async def test_lookup_returns_none_on_nxdomain() -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(side_effect=dns.resolver.NXDOMAIN)
        assert await rdns.lookup(addr) is None


@pytest.mark.asyncio
async def test_lookup_returns_none_on_timeout() -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(side_effect=dns.exception.Timeout)
        assert await rdns.lookup(addr) is None


@pytest.mark.asyncio
async def test_lookup_returns_none_on_no_answer() -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(side_effect=dns.resolver.NoAnswer)
        assert await rdns.lookup(addr) is None


@pytest.mark.asyncio
async def test_lookup_handles_ipv6() -> None:
    addr = ipaddress.ip_address("2606:4700::1")
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(return_value=_fake_answer("v6.example.com."))
        result = await rdns.lookup(addr)
    assert result == "v6.example.com"


@pytest.mark.asyncio
async def test_positive_cache_hit_returns_cached_hostname(monkeypatch) -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value="cached.example.com")
    fake_redis.set = AsyncMock()

    async def _ret():
        return fake_redis

    monkeypatch.setattr(rdns, "get_valkey", _ret)
    # Resolver should NOT be called \u2014 cache hit short-circuits.
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        result = await rdns.lookup(addr)
        resolver_cls.assert_not_called()
    assert result == "cached.example.com"


@pytest.mark.asyncio
async def test_negative_cache_hit_returns_none(monkeypatch) -> None:
    """Empty string in cache means 'looked up, no PTR'."""
    addr = ipaddress.ip_address("8.8.8.8")
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value="")
    fake_redis.set = AsyncMock()

    async def _ret():
        return fake_redis

    monkeypatch.setattr(rdns, "get_valkey", _ret)
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        result = await rdns.lookup(addr)
        resolver_cls.assert_not_called()
    assert result is None


@pytest.mark.asyncio
async def test_writes_negative_cache_on_miss_then_no_ptr(monkeypatch) -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()

    async def _ret():
        return fake_redis

    monkeypatch.setattr(rdns, "get_valkey", _ret)
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(side_effect=dns.resolver.NXDOMAIN)
        result = await rdns.lookup(addr)
    assert result is None
    # Negative cache marker is the empty string.
    fake_redis.set.assert_awaited_once()
    call = fake_redis.set.await_args
    assert call.args[0] == "cptv:rdns:8.8.8.8"
    assert call.args[1] == ""


@pytest.mark.asyncio
async def test_writes_positive_cache_on_miss_then_success(monkeypatch) -> None:
    addr = ipaddress.ip_address("8.8.8.8")
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    fake_redis.set = AsyncMock()

    async def _ret():
        return fake_redis

    monkeypatch.setattr(rdns, "get_valkey", _ret)
    with patch("dns.asyncresolver.Resolver") as resolver_cls:
        instance = resolver_cls.return_value
        instance.resolve = AsyncMock(return_value=_fake_answer("host.example.com."))
        result = await rdns.lookup(addr)
    assert result == "host.example.com"
    fake_redis.set.assert_awaited_once()
    call = fake_redis.set.await_args
    assert call.args[1] == "host.example.com"
