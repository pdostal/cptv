from __future__ import annotations

import asyncio
import ipaddress
import json
from unittest.mock import AsyncMock, patch

import pytest

from cptv.services.traceroute import (
    Hop,
    MplsLabel,
    TracerouteBusyError,
    TracerouteError,
    TracerouteRateLimitedError,
    TracerouteResult,
    _enrich_hop,
    _nat_warning,
    cache_key,
    format_json,
    format_text,
    reset_concurrency_cap,
    run_mtr,
    run_mtr_cached,
    stream_mtr_cached,
)

# Sample mtr JSON output matching real mtr --json format.
SAMPLE_MTR_JSON = {
    "report": {
        "mtr": {
            "src": "server",
            "dst": "203.0.113.42",
            "tos": 0,
            "psize": "64",
            "bitpattern": "0x00",
        },
        "hubs": [
            {
                "count": 5,
                "host": "10.0.0.1",
                "Loss%": 0.0,
                "Snt": 5,
                "Last": 1.2,
                "Avg": 1.3,
                "Best": 1.0,
                "Wrst": 1.8,
                "StDev": 0.3,
            },
            {
                "count": 5,
                "host": "???",
                "Loss%": 100.0,
                "Snt": 5,
                "Last": 0.0,
                "Avg": 0.0,
                "Best": 0.0,
                "Wrst": 0.0,
                "StDev": 0.0,
            },
            {
                "count": 5,
                "host": "203.0.113.42",
                "Loss%": 0.0,
                "Snt": 5,
                "Last": 5.1,
                "Avg": 5.0,
                "Best": 4.8,
                "Wrst": 5.3,
                "StDev": 0.2,
                "Mpls": [{"label": 12345, "tc": 0, "s": 1, "ttl": 255}],
            },
        ],
    }
}


class TestEnrichHop:
    @patch("cptv.services.traceroute._reverse_dns", return_value="router.example.com")
    @patch("cptv.services.traceroute.asn_service.lookup", return_value=None)
    def test_basic_hop(self, mock_asn, mock_rdns):
        hub = {"host": "10.0.0.1", "Loss%": 0.0, "Avg": 1.3, "Best": 1.0, "Wrst": 1.8}
        hop = _enrich_hop(1, hub)
        assert hop.hop == 1
        assert hop.ip == "10.0.0.1"
        assert hop.rdns == "router.example.com"
        assert hop.loss_pct == 0.0
        assert hop.avg_ms == 1.3

    def test_non_responding_hop(self):
        hub = {"host": "???", "Loss%": 100.0}
        hop = _enrich_hop(2, hub)
        assert hop.ip is None
        assert hop.loss_pct == 100.0

    @patch("cptv.services.traceroute._reverse_dns", return_value=None)
    @patch("cptv.services.traceroute.asn_service.lookup", return_value=None)
    def test_mpls_labels(self, mock_asn, mock_rdns):
        hub = {
            "host": "203.0.113.42",
            "Loss%": 0.0,
            "Avg": 5.0,
            "Best": 4.8,
            "Wrst": 5.3,
            "Mpls": [{"label": 12345, "tc": 0, "s": 1, "ttl": 255}],
        }
        hop = _enrich_hop(3, hub)
        assert len(hop.mpls) == 1
        assert hop.mpls[0].label == 12345


class TestNatWarning:
    def test_private_ip_warns(self):
        addr = ipaddress.ip_address("192.168.1.1")
        assert _nat_warning(addr) is not None
        assert "private" in _nat_warning(addr).lower()

    def test_cgnat_warns(self):
        addr = ipaddress.ip_address("100.64.0.1")
        assert _nat_warning(addr) is not None
        assert "CGNAT" in _nat_warning(addr)

    def test_public_ip_no_warning(self):
        addr = ipaddress.ip_address("8.8.8.8")
        assert _nat_warning(addr) is None


class TestRunMtr:
    @pytest.mark.asyncio
    @patch("cptv.services.traceroute._reverse_dns", return_value=None)
    @patch("cptv.services.traceroute.asn_service.lookup", return_value=None)
    async def test_successful_run(self, mock_asn, mock_rdns):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(SAMPLE_MTR_JSON).encode(), b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await run_mtr(ipaddress.ip_address("203.0.113.42"))

        assert result.target == "203.0.113.42"
        assert len(result.hops) == 3
        assert result.hops[0].ip == "10.0.0.1"
        assert result.hops[1].ip is None  # ???
        assert result.hops[2].ip == "203.0.113.42"
        assert result.cached is False

    @pytest.mark.asyncio
    async def test_mtr_not_found(self):
        with (
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=FileNotFoundError,
            ),
            pytest.raises(TracerouteError, match="not found"),
        ):
            await run_mtr(ipaddress.ip_address("8.8.8.8"))

    @pytest.mark.asyncio
    async def test_mtr_nonzero_exit(self, caplog):
        """mtr's raw stderr is logged but not surfaced to the client."""
        import logging

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"permission denied"))

        caplog.set_level(logging.WARNING, logger="cptv.services.traceroute")
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(TracerouteError, match="^traceroute failed$"),
        ):
            await run_mtr(ipaddress.ip_address("8.8.8.8"))
        assert any("permission denied" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_mtr_unreachable_translates_cleanly(self):
        """Network is unreachable becomes TracerouteUnreachableError."""
        from cptv.services.traceroute import TracerouteUnreachableError

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"mtr: udp socket connect failed: Network is unreachable")
        )

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(TracerouteUnreachableError, match="no IPv6 route"),
        ):
            await run_mtr(ipaddress.ip_address("2001:db8::1"))

    @pytest.mark.asyncio
    async def test_mtr_passes_family_flag(self):
        """The -4 / -6 flag is appended so mtr commits to one family."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(SAMPLE_MTR_JSON).encode(), b""))

        captured: list[str] = []

        async def capture(*args, **_kwargs):
            captured.extend(args)
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", side_effect=capture),
            patch("cptv.services.traceroute._reverse_dns", return_value=None),
            patch("cptv.services.traceroute.asn_service.lookup", return_value=None),
        ):
            await run_mtr(ipaddress.ip_address("203.0.113.42"))
        assert "-4" in captured
        assert "-6" not in captured

        captured.clear()
        with (
            patch("asyncio.create_subprocess_exec", side_effect=capture),
            patch("cptv.services.traceroute._reverse_dns", return_value=None),
            patch("cptv.services.traceroute.asn_service.lookup", return_value=None),
        ):
            await run_mtr(ipaddress.ip_address("2001:db8::1"))
        assert "-6" in captured
        assert "-4" not in captured

    @pytest.mark.asyncio
    async def test_mtr_invalid_json(self):
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"not json", b""))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(TracerouteError, match="invalid JSON"),
        ):
            await run_mtr(ipaddress.ip_address("8.8.8.8"))


class TestFormatText:
    def test_basic_output(self):
        result = TracerouteResult(
            target="203.0.113.42",
            ran_at="2024-01-01T00:00:00+00:00",
            hops=[
                Hop(hop=1, ip="10.0.0.1", rdns="gw.example.com", loss_pct=0.0, avg_ms=1.3),
                Hop(hop=2, ip=None, loss_pct=100.0),
                Hop(
                    hop=3,
                    ip="203.0.113.42",
                    asn=1234,
                    asn_name="Example ISP",
                    loss_pct=0.0,
                    avg_ms=5.0,
                    mpls=[MplsLabel(label=12345, tc=0, s=1, ttl=255)],
                ),
            ],
        )
        text = format_text(result)
        assert "203.0.113.42" in text
        assert "* * *" in text
        assert "AS1234" in text
        assert "MPLS:12345" in text

    def test_nat_warning_in_text(self):
        result = TracerouteResult(
            target="192.168.1.1",
            ran_at="2024-01-01T00:00:00+00:00",
            hops=[],
            nat_warning="Your IP is in a private range.",
        )
        text = format_text(result)
        assert "WARNING" in text


class TestFormatJson:
    def test_serializable(self):
        result = TracerouteResult(
            target="8.8.8.8",
            ran_at="2024-01-01T00:00:00+00:00",
            hops=[Hop(hop=1, ip="10.0.0.1", loss_pct=0.0, avg_ms=1.0)],
        )
        data = format_json(result)
        # Should be JSON-serializable
        json.dumps(data)
        assert data["target"] == "8.8.8.8"
        assert len(data["hops"]) == 1


class TestCacheKey:
    def test_ipv4_uses_full_address(self):
        addr = ipaddress.ip_address("203.0.113.42")
        key = cache_key(addr)
        assert key == "cptv:mtr:203.0.113.42"

    def test_ipv6_uses_slash64(self):
        addr = ipaddress.ip_address("2001:db8::1")
        key = cache_key(addr)
        assert key == "cptv:mtr:2001:db8::"

    def test_ipv6_different_hosts_same_prefix(self):
        addr1 = ipaddress.ip_address("2001:db8::1")
        addr2 = ipaddress.ip_address("2001:db8::ffff")
        assert cache_key(addr1) == cache_key(addr2)

    def test_ipv6_different_prefixes(self):
        addr1 = ipaddress.ip_address("2001:db8:1::1")
        addr2 = ipaddress.ip_address("2001:db8:2::1")
        assert cache_key(addr1) != cache_key(addr2)


class TestRunMtrCached:
    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.get_valkey", new_callable=AsyncMock, return_value=None)
    @patch("cptv.services.traceroute.run_mtr", new_callable=AsyncMock)
    async def test_fallback_without_redis(self, mock_run, mock_valkey):
        mock_run.return_value = TracerouteResult(
            target="8.8.8.8",
            ran_at="2024-01-01T00:00:00+00:00",
            hops=[],
        )
        result, meta = await run_mtr_cached(ipaddress.ip_address("8.8.8.8"))
        assert result.target == "8.8.8.8"
        assert meta.cached is False
        mock_run.assert_called_once()

    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.run_mtr", new_callable=AsyncMock)
    async def test_returns_cached_result(self, mock_run):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "target": "8.8.8.8",
                    "ran_at": "2024-01-01T00:00:00+00:00",
                    "hops": [],
                    "cached": False,
                    "nat_warning": None,
                }
            )
        )
        fake_redis.ttl = AsyncMock(return_value=3000)

        with patch(
            "cptv.services.traceroute.get_valkey",
            new_callable=AsyncMock,
            return_value=fake_redis,
        ):
            result, meta = await run_mtr_cached(ipaddress.ip_address("8.8.8.8"))

        assert result.cached is True
        assert meta.cached is True
        assert meta.cache_age == 600
        assert meta.refreshes_in == 3000
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.run_mtr", new_callable=AsyncMock)
    async def test_rate_limited_when_locked(self, mock_run):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.exists = AsyncMock(return_value=True)

        with (
            patch(
                "cptv.services.traceroute.get_valkey",
                new_callable=AsyncMock,
                return_value=fake_redis,
            ),
            pytest.raises(TracerouteRateLimitedError),
        ):
            await run_mtr_cached(ipaddress.ip_address("8.8.8.8"))

        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.run_mtr", new_callable=AsyncMock)
    async def test_fresh_run_stores_in_cache(self, mock_run):
        mock_run.return_value = TracerouteResult(
            target="8.8.8.8",
            ran_at="2024-01-01T00:00:00+00:00",
            hops=[],
        )
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.exists = AsyncMock(return_value=False)
        fake_redis.set = AsyncMock()
        fake_redis.delete = AsyncMock()

        with patch(
            "cptv.services.traceroute.get_valkey",
            new_callable=AsyncMock,
            return_value=fake_redis,
        ):
            result, meta = await run_mtr_cached(ipaddress.ip_address("8.8.8.8"))

        assert meta.cached is False
        mock_run.assert_called_once()
        # Should have set lock, cache, and deleted lock
        assert fake_redis.set.call_count == 2  # lock + cache
        fake_redis.delete.assert_called_once()  # lock cleanup


class TestConcurrencyCap:
    @pytest.fixture(autouse=True)
    def _reset_settings(self, monkeypatch):
        from cptv import config

        config.get_settings.cache_clear()
        reset_concurrency_cap()
        yield
        config.get_settings.cache_clear()
        reset_concurrency_cap()

    @pytest.mark.asyncio
    async def test_busy_when_cap_saturated(self, monkeypatch):
        from cptv import config

        config.get_settings.cache_clear()
        monkeypatch.setenv("CPTV_TRACEROUTE_MAX_CONCURRENCY", "1")
        monkeypatch.setenv("CPTV_TRACEROUTE_CONCURRENCY_WAIT_SECONDS", "0.05")
        reset_concurrency_cap()

        # Take the only slot manually so the next caller times out.
        from cptv.services import traceroute as traceroute_mod

        sem = traceroute_mod._get_semaphore()
        await sem.acquire()
        try:
            with pytest.raises(TracerouteBusyError):
                await run_mtr(ipaddress.ip_address("8.8.8.8"))
        finally:
            sem.release()

    @pytest.mark.asyncio
    async def test_releases_slot_on_success(self, monkeypatch):
        from cptv import config

        config.get_settings.cache_clear()
        monkeypatch.setenv("CPTV_TRACEROUTE_MAX_CONCURRENCY", "1")
        reset_concurrency_cap()

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(json.dumps(SAMPLE_MTR_JSON).encode(), b""))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("cptv.services.traceroute._reverse_dns", return_value=None),
            patch("cptv.services.traceroute.asn_service.lookup", return_value=None),
        ):
            await run_mtr(ipaddress.ip_address("8.8.8.8"))
            # Slot must be free again — second call also succeeds.
            await run_mtr(ipaddress.ip_address("1.1.1.1"))

    @pytest.mark.asyncio
    async def test_releases_slot_on_error(self, monkeypatch):
        from cptv import config

        config.get_settings.cache_clear()
        monkeypatch.setenv("CPTV_TRACEROUTE_MAX_CONCURRENCY", "1")
        reset_concurrency_cap()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError,
        ):
            with pytest.raises(TracerouteError):
                await run_mtr(ipaddress.ip_address("8.8.8.8"))

        # Slot must be released — semaphore should not block a fresh acquire.
        from cptv.services import traceroute as traceroute_mod

        sem = traceroute_mod._get_semaphore()
        async with asyncio.timeout(0.1):
            await sem.acquire()
        sem.release()


class TestStreamMtrLive:
    @pytest.mark.asyncio
    @patch("cptv.services.traceroute._reverse_dns", return_value=None)
    @patch("cptv.services.traceroute.asn_service.lookup", return_value=None)
    async def test_parses_raw_lines_into_hop_events(self, _asn, _rdns):
        """Feed synthetic mtr --raw lines through the live streamer."""
        from cptv.services import traceroute as t

        raw = [
            b"h 0 10.0.0.1\n",
            b"x 0 1\n",
            b"p 0 1234 1\n",
            b"h 1 8.8.8.8\n",
            b"x 1 2\n",
            b"p 1 5678 2\n",
            b"m 1 1234 0 1 64\n",
        ]

        async def fake_lines(_target):
            for line in raw:
                yield line

        with patch.object(t, "_stream_mtr_raw_lines", side_effect=fake_lines):
            events = []
            async for ev in t.stream_mtr_live(ipaddress.ip_address("8.8.8.8")):
                events.append(ev)

        # Each h, p, m event for a hop with an IP should yield a hop event.
        assert all(e.event == "hop" for e in events)
        # Last emission for hop 1 should include MPLS labels and avg_ms.
        last_hop_1 = [e for e in events if e.data["hop"] == 2][-1]
        assert last_hop_1["ip"] if False else last_hop_1.data["ip"] == "8.8.8.8"
        # 5678 us → 5.678 ms, rounded to 2 decimals.
        assert last_hop_1.data["avg_ms"] == 5.68
        assert len(last_hop_1.data["mpls"]) == 1


def _live_event(hop_data):
    from cptv.services.traceroute import StreamEvent

    return StreamEvent(event="hop", data=hop_data)


async def _fake_live_stream(events):
    for e in events:
        yield e


class TestStreamMtrCached:
    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.get_valkey", new_callable=AsyncMock, return_value=None)
    @patch("cptv.services.traceroute.asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_no_cache_emits_started_hops_done(self, mock_sleep, mock_valkey):
        sample_events = [
            _live_event(
                {
                    "hop": 1,
                    "ip": "10.0.0.1",
                    "rdns": None,
                    "asn": None,
                    "asn_name": None,
                    "loss_pct": 0.0,
                    "avg_ms": 1.0,
                    "best_ms": 1.0,
                    "worst_ms": 1.0,
                    "mpls": [],
                }
            ),
            _live_event(
                {
                    "hop": 2,
                    "ip": "8.8.8.8",
                    "rdns": None,
                    "asn": None,
                    "asn_name": None,
                    "loss_pct": 0.0,
                    "avg_ms": 5.0,
                    "best_ms": 5.0,
                    "worst_ms": 5.0,
                    "mpls": [],
                }
            ),
        ]

        with patch(
            "cptv.services.traceroute.stream_mtr_live",
            side_effect=lambda addr: _fake_live_stream(sample_events),
        ):
            events = []
            async for ev in stream_mtr_cached(ipaddress.ip_address("8.8.8.8")):
                events.append(ev)

        kinds = [e.event for e in events]
        assert kinds == ["started", "hop", "hop", "done"]
        assert events[0].data["cached"] is False
        assert events[1].data["ip"] == "10.0.0.1"
        assert events[-1].data["cached"] is False

    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.run_mtr", new_callable=AsyncMock)
    @patch("cptv.services.traceroute.asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_cache_hit_replays_hops(self, mock_sleep, mock_run):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(
            return_value=json.dumps(
                {
                    "target": "8.8.8.8",
                    "ran_at": "2024-01-01T00:00:00+00:00",
                    "hops": [
                        {
                            "hop": 1,
                            "ip": "10.0.0.1",
                            "rdns": None,
                            "asn": None,
                            "asn_name": None,
                            "loss_pct": 0.0,
                            "avg_ms": 1.0,
                            "best_ms": None,
                            "worst_ms": None,
                            "mpls": [],
                        },
                    ],
                    "cached": False,
                    "nat_warning": None,
                }
            )
        )
        fake_redis.ttl = AsyncMock(return_value=3000)

        with patch(
            "cptv.services.traceroute.get_valkey",
            new_callable=AsyncMock,
            return_value=fake_redis,
        ):
            events = []
            async for ev in stream_mtr_cached(ipaddress.ip_address("8.8.8.8")):
                events.append(ev)

        assert events[0].event == "started"
        assert events[0].data["cached"] is True
        assert events[0].data["cache_age"] == 600
        assert events[1].event == "hop"
        assert events[-1].event == "done"
        assert events[-1].data["cached"] is True
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.run_mtr", new_callable=AsyncMock)
    async def test_stream_rate_limited_emits_error(self, mock_run):
        fake_redis = AsyncMock()
        fake_redis.get = AsyncMock(return_value=None)
        fake_redis.exists = AsyncMock(return_value=True)

        with patch(
            "cptv.services.traceroute.get_valkey",
            new_callable=AsyncMock,
            return_value=fake_redis,
        ):
            events = []
            async for ev in stream_mtr_cached(ipaddress.ip_address("8.8.8.8")):
                events.append(ev)

        assert events == [
            type(events[0])(
                event="error",
                data={"error": "Traceroute already in progress for this IP."},
            )
        ]
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    @patch("cptv.services.traceroute.asyncio.sleep", new_callable=AsyncMock)
    async def test_stream_error_emits_error_event(self, mock_sleep):
        async def boom(_addr):
            raise TracerouteError("mtr exploded")
            yield  # pragma: no cover

        with (
            patch(
                "cptv.services.traceroute.get_valkey",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch("cptv.services.traceroute.stream_mtr_live", side_effect=boom),
        ):
            events = []
            async for ev in stream_mtr_cached(ipaddress.ip_address("8.8.8.8")):
                events.append(ev)

        assert events[0].event == "started"
        assert events[-1].event == "error"
        assert "mtr exploded" in events[-1].data["error"]
