"""Tests for the Yahoo Finance adapter.

Verifies:
  - Bar shape matches the standard contract (symbol, date, open, high, low, close, volume)
  - .KL suffix handling for Bursa tickers
  - Fixture fallback when yfinance fetch fails
  - Cache write and read
  - Provider routing through market_data.py
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import yahoo_finance


def test_bursa_ticker_suffix():
    assert yahoo_finance._bursa_ticker("1155") == "1155.KL"
    assert yahoo_finance._bursa_ticker("1155.KL") == "1155.KL"
    assert yahoo_finance._bursa_ticker("  1295  ") == "1295.KL"
    assert yahoo_finance._bursa_ticker("AAPL") == "AAPL"
    print("PASS: bursa_ticker_suffix — .KL added for numeric tickers only")


def test_bar_shape():
    required_keys = {"symbol", "date", "open", "high", "low", "close", "volume"}
    bars = yahoo_finance._fixture_prices("1155")
    assert len(bars) == 2
    for bar in bars:
        assert set(bar.keys()) == required_keys, f"Bad bar keys: {set(bar.keys())}"
        assert isinstance(bar["open"], float)
        assert isinstance(bar["volume"], int)
    print("PASS: bar_shape — fixture bars have correct shape")


def test_fixture_fallback():
    def _fail(*args, **kwargs):
        raise RuntimeError("network unavailable")

    with patch.object(yahoo_finance, "_fetch_yfinance", _fail):
        bars, source = yahoo_finance.fetch_eod_prices("1155", "2026-01-01", "2026-06-01")
    assert source == "fixture"
    assert len(bars) >= 1
    assert bars[0]["symbol"] == "1155"
    print("PASS: fixture_fallback — returns fixture when yfinance fails")


def test_cache_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        original_cache = yahoo_finance.CACHE_DIR
        yahoo_finance.CACHE_DIR = Path(tmp)
        try:
            test_bars = [
                {
                    "symbol": "1155",
                    "date": "2026-09-01",
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.8,
                    "close": 10.3,
                    "volume": 1000000,
                },
            ]
            yahoo_finance._write_cache("1155", "2026-09-01", "2026-09-01", test_bars)
            cached = yahoo_finance._read_cache("1155")
            assert len(cached) == 1
            assert cached[0]["close"] == 10.3
            assert cached[0]["symbol"] == "1155"
        finally:
            yahoo_finance.CACHE_DIR = original_cache
    print("PASS: cache_round_trip — write and read cache correctly")


def test_stale_cache_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        original_cache = yahoo_finance.CACHE_DIR
        yahoo_finance.CACHE_DIR = Path(tmp)
        try:
            test_bars = [
                {
                    "symbol": "1155",
                    "date": "2026-08-01",
                    "open": 9.5,
                    "high": 9.8,
                    "low": 9.3,
                    "close": 9.6,
                    "volume": 800000,
                },
            ]
            yahoo_finance._write_cache("1155", "2026-08-01", "2026-08-01", test_bars)

            def _fail(*args, **kwargs):
                raise RuntimeError("network down")

            with patch.object(yahoo_finance, "_fetch_yfinance", _fail):
                bars, source = yahoo_finance.fetch_eod_prices(
                    "1155",
                    "2026-09-01",
                    "2026-09-05",
                    allow_stale_cache=True,
                    allow_fallback=False,
                )
            assert source == "yahoo_cache"
            assert bars[0]["close"] == 9.6
        finally:
            yahoo_finance.CACHE_DIR = original_cache
    print("PASS: stale_cache_fallback — uses cached bars when fetch fails")


def test_error_without_fallback():
    def _fail(*args, **kwargs):
        raise RuntimeError("network down")

    with patch.object(yahoo_finance, "_fetch_yfinance", _fail):
        try:
            yahoo_finance.fetch_eod_prices(
                "1155",
                "2026-01-01",
                "2026-06-01",
                allow_fallback=False,
                allow_stale_cache=False,
            )
            assert False, "Should have raised"
        except yahoo_finance.YahooDataError as exc:
            assert exc.error_code == "fetch_failed"
    print("PASS: error_without_fallback — raises YahooDataError when all paths fail")


def test_provider_routing():
    saved = os.environ.get("MARKET_DATA_PROVIDER")
    os.environ["MARKET_DATA_PROVIDER"] = "yahoo"
    try:
        import market_data

        def mock_fetch(symbol, start, end, **kw):
            return [
                {
                    "symbol": symbol,
                    "date": "2026-09-01",
                    "open": 1,
                    "high": 2,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 100,
                }
            ], "yahoo_mock"

        with patch.object(yahoo_finance, "fetch_eod_prices", mock_fetch):
            from importlib import reload

            reload(market_data)
            bars, source = market_data.fetch_eod_prices("1155", "2026-09-01", "2026-09-01")
        assert source == "yahoo_mock"
    finally:
        if saved is not None:
            os.environ["MARKET_DATA_PROVIDER"] = saved
        else:
            os.environ.pop("MARKET_DATA_PROVIDER", None)
    print("PASS: provider_routing — market_data routes to yahoo when configured")


def test_live_fetch():
    """Smoke test: fetch real data for Maybank if network is available."""
    try:
        bars, source = yahoo_finance.fetch_eod_prices(
            "1155",
            "2026-08-01",
            "2026-09-01",
            allow_fallback=False,
            allow_stale_cache=False,
        )
    except yahoo_finance.YahooDataError:
        print("SKIP: live_fetch — network unavailable")
        return

    assert source == "yahoo"
    assert len(bars) >= 1
    required_keys = {"symbol", "date", "open", "high", "low", "close", "volume"}
    for bar in bars:
        assert set(bar.keys()) == required_keys
        assert bar["close"] > 0
    print(
        f"PASS: live_fetch — got {len(bars)} bars for 1155.KL (Maybank), latest close {bars[-1]['close']}"
    )


def main():
    test_bursa_ticker_suffix()
    test_bar_shape()
    test_fixture_fallback()
    test_cache_round_trip()
    test_stale_cache_fallback()
    test_error_without_fallback()
    test_provider_routing()
    test_live_fetch()
    print("\nAll Yahoo Finance tests passed.")


if __name__ == "__main__":
    main()
