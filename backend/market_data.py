"""Market-data verification helpers for the quant agent.

PROVIDER ROUTING IS PER MARKET, NOT GLOBAL

`MARKET_DATA_PROVIDER` selects the provider for US equities. Malaysian symbols
ignore it and always use Yahoo, because Alpaca and Tiingo carry no Bursa data at
all -- a Bursa symbol sent to either returns nothing and falls through to
synthetic fixture bars, which `agent_coordinator` then correctly refuses as
`synthetic_market_data`.

Before this was routed per market the switch was global, which made the two
markets mutually exclusive: setting it to `yahoo` to reach Bursa would also have
pulled every US symbol off Alpaca, losing the IEX retry, the shared cache, and
the options and news paths that stay on Alpaca regardless.

The market is decided by `detect_market` -- the same function the Shariah gate
routes on and `p3_decision_engine` sizes lots with -- so a symbol cannot be
screened as Malaysian and priced as American.
"""

from datetime import date, timedelta

from alpaca_market_data import fetch_eod_prices as fetch_alpaca_eod_prices
from agents.shariah_agent import detect_market
from config import load_settings
from tiingo_prices import fetch_eod_prices as fetch_tiingo_eod_prices
from yahoo_finance import fetch_eod_prices as fetch_yahoo_eod_prices


def provider_for(symbol: str) -> str:
    """Which provider will price this symbol. Exposed so callers can report it."""
    if detect_market(symbol) == "MY":
        return "yahoo"
    return load_settings().market_data_provider


def fetch_eod_prices(symbol: str, start_date: str, end_date: str, **kwargs):
    """Route to the provider that actually covers this symbol's market."""
    provider = provider_for(symbol)
    if provider == "yahoo":
        return fetch_yahoo_eod_prices(symbol, start_date, end_date, **kwargs)
    if provider == "alpaca":
        return fetch_alpaca_eod_prices(symbol, start_date, end_date, **kwargs)
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
