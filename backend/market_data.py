"""Market-data verification helpers for the quant agent."""

from datetime import date, timedelta

from alpaca_market_data import fetch_eod_prices as fetch_alpaca_eod_prices
from config import load_settings
from tiingo_prices import fetch_eod_prices as fetch_tiingo_eod_prices
from yahoo_finance import fetch_eod_prices as fetch_yahoo_eod_prices


def fetch_eod_prices(symbol: str, start_date: str, end_date: str, **kwargs):
    """Route to the configured market-data provider (MARKET_DATA_PROVIDER)."""
    provider = load_settings().market_data_provider
    if provider == "alpaca":
        return fetch_alpaca_eod_prices(symbol, start_date, end_date, **kwargs)
    if provider == "yahoo":
        return fetch_yahoo_eod_prices(symbol, start_date, end_date, **kwargs)
    return fetch_tiingo_eod_prices(symbol, start_date, end_date, **kwargs)


def summarize_history(
    symbol: str,
    *,
    days: int = 365,
    min_bars: int = 200,
    allow_fallback: bool = True,
    allow_stale_cache: bool = False,
) -> dict:
    normalized_symbol = symbol.strip().upper()
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    bars, source = fetch_eod_prices(
        normalized_symbol,
        start_date.isoformat(),
        end_date.isoformat(),
        allow_fallback=allow_fallback,
        allow_stale_cache=allow_stale_cache,
    )
    latest = bars[-1] if bars else None
    latest_close = float(latest["close"]) if latest else None
    return {
        "symbol": normalized_symbol,
        "source": source,
        "bars": len(bars),
        "min_bars": min_bars,
        "enough_history": len(bars) >= min_bars,
        "latest_date": latest.get("date") if latest else None,
        "latest_close": latest_close,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
