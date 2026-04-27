from __future__ import annotations

from cptv.services.timing import TcpInfo, parse_tcp_info_headers


def test_parse_full_headers():
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "24000",
            "X-Tcp-Rttvar-Us": "15800",
            "X-Tcp-Mss-Server": "1448",
        }
    )
    assert info == TcpInfo(rtt_ms=24.0, rttvar_ms=15.8, mss_bytes=1448)


def test_parse_legacy_mss_header():
    # X-Tcp-Mss is the legacy alias from the original draft; still accepted.
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "24000",
            "X-Tcp-Rttvar-Us": "15800",
            "X-Tcp-Mss": "1428",
        }
    )
    assert info == TcpInfo(rtt_ms=24.0, rttvar_ms=15.8, mss_bytes=1428)


def test_parse_prefers_server_header_when_both_present():
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "24000",
            "X-Tcp-Rttvar-Us": "15800",
            "X-Tcp-Mss": "1428",
            "X-Tcp-Mss-Server": "1448",
        }
    )
    assert info is not None
    assert info.mss_bytes == 1448


def test_parse_us_to_ms_rounding():
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "24876",
            "X-Tcp-Rttvar-Us": "0",
            "X-Tcp-Mss": "1428",
        }
    )
    assert info is not None
    assert info.rtt_ms == 24.9
    assert info.rttvar_ms == 0.0
    assert info.mss_bytes == 1428


def test_parse_rtt_only_without_mss_returns_partial_info():
    """RTT/RTTvar work on stock nginx without Lua; MSS may be absent."""
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "24000",
            "X-Tcp-Rttvar-Us": "15800",
        }
    )
    assert info == TcpInfo(rtt_ms=24.0, rttvar_ms=15.8, mss_bytes=None)


def test_parse_missing_rtt_returns_none():
    # RTT and RTTvar are required (they work on any nginx); without
    # them the whole TCP block is hidden in the UI.
    assert parse_tcp_info_headers({"X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"}) is None


def test_parse_missing_rttvar_returns_none():
    assert parse_tcp_info_headers({"X-Tcp-Rtt-Us": "100", "X-Tcp-Mss": "1448"}) is None


def test_parse_blank_rtt_returns_none():
    assert (
        parse_tcp_info_headers({"X-Tcp-Rtt-Us": "", "X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"})
        is None
    )


def test_parse_negative_rtt_returns_none():
    assert (
        parse_tcp_info_headers(
            {"X-Tcp-Rtt-Us": "-1", "X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"}
        )
        is None
    )


def test_parse_non_numeric_rtt_returns_none():
    assert (
        parse_tcp_info_headers(
            {"X-Tcp-Rtt-Us": "abc", "X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"}
        )
        is None
    )


def test_parse_zero_mss_treated_as_missing():
    # MSS of zero is meaningless; RTT/RTTvar still surface, MSS row hides.
    info = parse_tcp_info_headers({"X-Tcp-Rtt-Us": "100", "X-Tcp-Rttvar-Us": "0", "X-Tcp-Mss": "0"})
    assert info is not None
    assert info.mss_bytes is None
    assert info.rtt_ms == 0.1


def test_parse_absurd_mss_treated_as_missing():
    # >60_000_000 bytes is treated as bogus; only MSS hides — RTT stays.
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "100",
            "X-Tcp-Rttvar-Us": "200",
            "X-Tcp-Mss": "999999999",
        }
    )
    assert info is not None
    assert info.mss_bytes is None


def test_parse_absurd_rtt_returns_none():
    assert (
        parse_tcp_info_headers(
            {
                "X-Tcp-Rtt-Us": "999999999",
                "X-Tcp-Rttvar-Us": "100",
                "X-Tcp-Mss": "1448",
            }
        )
        is None
    )


def test_parse_empty_headers():
    assert parse_tcp_info_headers({}) is None
