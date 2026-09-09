"""Yahoo Finance EOD adapter for Bursa Malaysia (.KL) tickers.

Same contract as tiingo_prices: fetch_eod_prices returns (bars, source) where bars
are [{"symbol", "date", "open", "high", "low", "close", "volume"}].

Yahoo Finance data is market data only — it NEVER determines Shariah eligibility.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from config import BACKEND_DIR


CACHE_DIR = BACKEND_DIR / "market_data_cache"


class YahooDataError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _bursa_ticker(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol.endswith(".KL"):
        return symbol
    if symbol.isdigit():
        return f"{symbol}.KL"
    return symbol


def _fixture_prices(symbol: str) -> list[dict]:
    today = date.today()
    return [
        {
            "symbol": symbol,
            "date": (today - timedelta(days=2)).isoformat(),
            "open": 10.00,
            "high": 10.20,
            "low": 9.90,
            "close": 10.10,
            "volume": 5000000,
        },
        {
            "symbol": symbol,
            "date": (today - timedelta(days=1)).isoformat(),
            "open": 10.10,
            "high": 10.30,
            "low": 10.00,
            "close": 10.20,
            "volume": 5500000,
        },
    ]


def _cache_path(symbol: str) -> Path:
    safe = "".join(c for c in symbol.upper() if c.isalnum() or c in {"-", "."})
    return CACHE_DIR / f"yahoo_{safe}.json"


def _write_cache(symbol: str, start_date: str, end_date: str, bars: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(symbol).write_text(
        json.dumps(
            {
                "symbol": symbol,
                "start_date": start_date,
                "end_date": end_date,
                "cached_at": datetime.now(timezone.utc).isoformat(),
                "bars": bars,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _read_cache(symbol: str) -> list[dict]:
    path = _cache_path(symbol)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        bars = data.get("bars")
        return bars if isinstance(bars, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _fetch_yfinance(yf_ticker: str, start_date: str, end_date: str) -> list[dict]:
    import yfinance as yf

    ticker = yf.Ticker(yf_ticker)
    df = ticker.history(start=start_date, end=end_date, auto_adjust=True)
    if df is None or df.empty:
        return []

    bars = []
    for idx, row in df.iterrows():
        dt = idx
        if hasattr(dt, "date"):
            dt = dt.date()
        bars.append(
            {
                "symbol": yf_ticker.replace(".KL", ""),
                "date": str(dt),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row["Volume"]),
            }
        )
    return bars


def fetch_eod_prices(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    allow_fallback: bool = True,
    allow_stale_cache: bool = False,
) -> tuple[list[dict], str]:
    normalized = symbol.strip().upper()
    yf_ticker = _bursa_ticker(normalized)

    try:
        bars = _fetch_yfinance(yf_ticker, start_date, end_date)
        if bars:
            _write_cache(normalized, start_date, end_date, bars)
            return bars, "yahoo"
    except Exception:
        pass

    if allow_stale_cache:
        cached = _read_cache(normalized)
        if cached:
            return cached, "yahoo_cache"

    if allow_fallback:
        return _fixture_prices(normalized), "fixture"

    raise YahooDataError("fetch_failed", f"Yahoo Finance fetch failed for {yf_ticker}")
