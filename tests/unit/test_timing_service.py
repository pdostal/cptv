from __future__ import annotations

from cptv.services.timing import TcpInfo, parse_tcp_info_headers


def test_parse_full_headers():
    info = parse_tcp_info_headers(
        {
            "X-Tcp-Rtt-Us": "24000",
            "X-Tcp-Rttvar-Us": "15800",
            "X-Tcp-Mss": "1448",
        }
    )
    assert info == TcpInfo(rtt_ms=24.0, rttvar_ms=15.8, mss_bytes=1448)


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


def test_parse_missing_header_returns_none():
    # Any single missing header yields None — the UI then omits the rows.
    base = {
        "X-Tcp-Rtt-Us": "100",
        "X-Tcp-Rttvar-Us": "200",
        "X-Tcp-Mss": "1448",
    }
    for missing in base:
        partial = {k: v for k, v in base.items() if k != missing}
        assert parse_tcp_info_headers(partial) is None, missing


def test_parse_blank_value_returns_none():
    assert (
        parse_tcp_info_headers({"X-Tcp-Rtt-Us": "", "X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"})
        is None
    )


def test_parse_negative_value_returns_none():
    assert (
        parse_tcp_info_headers(
            {"X-Tcp-Rtt-Us": "-1", "X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"}
        )
        is None
    )


def test_parse_non_numeric_returns_none():
    assert (
        parse_tcp_info_headers(
            {"X-Tcp-Rtt-Us": "abc", "X-Tcp-Rttvar-Us": "100", "X-Tcp-Mss": "1448"}
        )
        is None
    )


def test_parse_zero_mss_returns_none():
    # MSS of zero is meaningless and should not show "0b" on the page.
    assert (
        parse_tcp_info_headers({"X-Tcp-Rtt-Us": "100", "X-Tcp-Rttvar-Us": "0", "X-Tcp-Mss": "0"})
        is None
    )


def test_parse_absurd_value_returns_none():
    # >60_000_000 us / bytes is treated as bogus; clamped at None so we
    # don't render "60000.0ms" or a 60 MB MSS.
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
