"""Verify the quant agent reads prices through the configured market-data provider.

The quant signal is the first thing /paper/preview consults, so if it is pinned to
one provider it can block every order while the configured provider is healthy.
Nothing here touches the network: market_data.fetch_eod_prices is the seam, exactly
as alpaca_request is elsewhere.
"""

import os

import agents.quant_agent as quant_agent
import market_data


def trending_bars(symbol: str, count: int = 260) -> list[dict]:
    """A monotonically rising series, so SMA50 > SMA200 and the last close breaks out."""
    bars = []
    for index in range(count):
        close = 100.0 + index
        bars.append(
            {
                "symbol": symbol,
                "date": f"2026-01-{(index % 28) + 1:02d}",
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000,
            }
        )
    return bars


def test_quant_agent_uses_the_provider_switch() -> None:
    """A tiingo outage must not blind the quant agent when the provider is alpaca."""
    calls = []

    def fake_fetch(symbol, start_date, end_date, **kwargs):
        calls.append(symbol)
        return trending_bars(symbol), "alpaca"

    original = market_data.fetch_eod_prices
    market_data.fetch_eod_prices = fake_fetch
    try:
        result = quant_agent.evaluate_quant("CVX")
    finally:
        market_data.fetch_eod_prices = original

    assert calls == ["CVX"], f"quant agent bypassed the provider switch: {calls}"
    assert result["price_source"] == "alpaca", result["price_source"]
    assert result["signal"] == "BUY", result
    assert result["status"] == "PASS", result


def test_alpaca_sources_are_reported_live_not_unknown() -> None:
    """data_freshness predated the alpaca provider; alpaca bars are live, not unknown."""
    for source in ["alpaca", "alpaca_iex", "tiingo"]:
        freshness = quant_agent.data_freshness("CVX", source)
        assert freshness["data_freshness"] == "live", (source, freshness)

    for source in ["fixture", "fixture_after_tiingo_error", "fixture_after_alpaca_error"]:
        freshness = quant_agent.data_freshness("CVX", source)
        assert freshness["data_freshness"] == "fixture", (source, freshness)

    for source in [
        "tiingo_cache_after_error",
        "alpaca_cache_after_error",
        "alpaca_cache_no_credentials",
    ]:
        freshness = quant_agent.data_freshness("CVX", source)
        assert freshness["data_freshness"] == "cached", (source, freshness)


def test_provider_switch_honours_the_configured_provider() -> None:
    """market_data.fetch_eod_prices is the switch; confirm it actually switches."""
    saved = os.environ.get("MARKET_DATA_PROVIDER")
    seen = {}

    import alpaca_market_data
    import tiingo_prices

    original_alpaca = alpaca_market_data.fetch_eod_prices
    original_tiingo = tiingo_prices.fetch_eod_prices
    original_switch_alpaca = market_data.fetch_alpaca_eod_prices
    original_switch_tiingo = market_data.fetch_tiingo_eod_prices

    market_data.fetch_alpaca_eod_prices = lambda *a, **k: (
        seen.setdefault("provider", "alpaca"),
        ([], "alpaca"),
    )[1]
    market_data.fetch_tiingo_eod_prices = lambda *a, **k: (
        seen.setdefault("provider", "tiingo"),
        ([], "tiingo"),
    )[1]
    try:
        os.environ["MARKET_DATA_PROVIDER"] = "alpaca"
        market_data.fetch_eod_prices("CVX", "2026-01-01", "2026-08-19")
        assert seen["provider"] == "alpaca", seen

        seen.clear()
        os.environ["MARKET_DATA_PROVIDER"] = "tiingo"
        market_data.fetch_eod_prices("CVX", "2026-01-01", "2026-08-19")
        assert seen["provider"] == "tiingo", seen
    finally:
        market_data.fetch_alpaca_eod_prices = original_switch_alpaca
        market_data.fetch_tiingo_eod_prices = original_switch_tiingo
        alpaca_market_data.fetch_eod_prices = original_alpaca
        tiingo_prices.fetch_eod_prices = original_tiingo
        if saved is None:
            os.environ.pop("MARKET_DATA_PROVIDER", None)
        else:
            os.environ["MARKET_DATA_PROVIDER"] = saved


def test_malaysian_symbols_route_to_yahoo_whatever_the_provider() -> None:
    """Bursa prices exist only on Yahoo, so MY must not follow the global switch.

    Alpaca and Tiingo carry no Bursa data at all: a Malaysian symbol sent to
    either returns nothing, falls through to fixture bars, and is then refused as
    synthetic_market_data. Routing per market is what makes the two markets
    usable at the same time -- before this, reaching Bursa meant pulling every US
    symbol off Alpaca too.
    """
    saved = os.environ.get("MARKET_DATA_PROVIDER")
    try:
        for configured in ("alpaca", "tiingo"):
            os.environ["MARKET_DATA_PROVIDER"] = configured
            assert market_data.provider_for("5225") == "yahoo", configured
            assert market_data.provider_for("0026") == "yahoo", configured
            assert market_data.provider_for("AAPL") == configured
    finally:
        if saved is None:
            os.environ.pop("MARKET_DATA_PROVIDER", None)
        else:
            os.environ["MARKET_DATA_PROVIDER"] = saved
    print("PASS: Malaysian symbols route to Yahoo regardless of MARKET_DATA_PROVIDER.")


def test_yahoo_is_live_data_not_unknown() -> None:
    """The bug this guards cost Malaysia its whole execution path.

    "yahoo" was missing from LIVE_SOURCES, so real Bursa bars were classified
    `unknown` and agent_coordinator's synthetic-data blocker refused them --
    measured on 5225: 217 real bars at 7.62, freshness "unknown", order blocked.
    Real data labelled synthetic is a worse failure than missing data, because
    the reason given is untrue.
    """
    assert quant_agent.data_freshness("5225", "yahoo")["data_freshness"] == "live"

    # A fixture must still be a fixture. The blocker exists for exactly this.
    assert quant_agent.data_freshness("5225", "fixture")["data_freshness"] == "fixture"
    assert (
        quant_agent.data_freshness("5225", "fixture_after_yahoo_error")["data_freshness"]
        == "fixture"
    )
    print("PASS: Yahoo bars are live data; fixtures are still fixtures.")


def test_cache_age_is_read_from_the_provider_that_wrote_it() -> None:
    """Each provider owns its own cache file, so the lookup must follow the source.

    Yahoo caches under `yahoo_{SYMBOL}.json` while tiingo/alpaca share
    `{SYMBOL}.json`. Asking tiingo about a Yahoo-cached symbol silently returned
    {}, so a stale Bursa price reported `cache_age_hours: None` and was
    indistinguishable from a fresh one.
    """
    import yahoo_finance

    saved_yahoo = yahoo_finance.read_cache_metadata
    saved_tiingo = quant_agent.read_cache_metadata
    try:
        yahoo_finance.read_cache_metadata = lambda symbol: {
            "cached_at": "2026-09-20T00:00:00+00:00"
        }
        quant_agent.read_cache_metadata = lambda symbol: {}

        yahoo_cached = quant_agent.data_freshness("5225", "yahoo_cache")
        assert yahoo_cached["data_freshness"] == "cached", yahoo_cached
        assert yahoo_cached["cache_cached_at"] is not None, (
            "a cached Bursa price reported no age, so staleness was invisible"
        )
        assert yahoo_cached["cache_age_hours"] is not None
    finally:
        yahoo_finance.read_cache_metadata = saved_yahoo
        quant_agent.read_cache_metadata = saved_tiingo
    print("PASS: cache age comes from the provider that wrote the cache.")


def test_malaysian_lookback_is_wider_than_us() -> None:
    """Bursa closes for more holidays; 320 days left only 18 bars of headroom.

    Asserted through the seam rather than by reading a constant, so it is the
    window actually requested that is checked.
    """
    windows = {}

    def fake_fetch(symbol, start_date, end_date, **kwargs):
        windows[symbol] = (start_date, end_date)
        return trending_bars(symbol), "alpaca"

    saved = market_data.fetch_eod_prices
    try:
        market_data.fetch_eod_prices = fake_fetch
        quant_agent.evaluate_quant("5225")
        quant_agent.evaluate_quant("AAPL")
    finally:
        market_data.fetch_eod_prices = saved

    my_span = windows["5225"]
    us_span = windows["AAPL"]
    assert my_span[0] < us_span[0], f"MY window must reach further back: {my_span} vs {us_span}"
    print("PASS: Malaysian symbols request a wider history window than US symbols.")


def main() -> None:
    test_quant_agent_uses_the_provider_switch()
    test_alpaca_sources_are_reported_live_not_unknown()
    test_provider_switch_honours_the_configured_provider()
    test_malaysian_symbols_route_to_yahoo_whatever_the_provider()
    test_yahoo_is_live_data_not_unknown()
    test_cache_age_is_read_from_the_provider_that_wrote_it()
    test_malaysian_lookback_is_wider_than_us()
    print("PASS: the quant agent reads through the configured market-data provider.")


if __name__ == "__main__":
    main()
