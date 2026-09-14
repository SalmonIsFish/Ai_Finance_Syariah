"""Read-only Tiingo EOD adapter with an explicit fixture fallback."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import BACKEND_DIR, load_settings


CACHE_DIR = BACKEND_DIR / "market_data_cache"


class TiingoDataError(RuntimeError):
    """Market-data request failure with safe diagnostic fields."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.status_code = status_code
        self.retry_after = retry_after


def _fixture_prices(symbol: str) -> list[dict]:
    from datetime import date, timedelta
    today = date.today()
    bars = []
    # Make a 205-bar uptrend
    for i in range(205, -1, -1):
        price = 100.0 + (205 - i) * 0.1
        bars.append({"symbol": symbol, "date": (today - timedelta(days=i)).isoformat(), "open": price, "high": price+1, "low": price-1, "close": price, "volume": 100000})
    return bars


def _cache_path(symbol: str) -> Path:
    safe_symbol = "".join(character for character in symbol.upper() if character.isalnum() or character in {"-", "."})
    return CACHE_DIR / f"{safe_symbol}.json"


def _normalize_bars(symbol: str, payload: list[dict]) -> list[dict]:
    return [
        {"symbol": symbol, "date": row["date"][:10], "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"], "volume": row["volume"]}
        for row in payload
    ]


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
    metadata = read_cache_metadata(symbol)
    bars = metadata.get("bars")
    if not isinstance(bars, list):
        return []
    return bars


def read_cache_metadata(symbol: str) -> dict:
    path = _cache_path(symbol)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_error(exc: Exception) -> TiingoDataError:
    if isinstance(exc, HTTPError):
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        reason = getattr(exc, "reason", None) or exc.msg or "HTTP request failed"
        return TiingoDataError(
            f"http_{exc.code}",
            f"Tiingo HTTP {exc.code}: {reason}",
            status_code=exc.code,
            retry_after=retry_after,
        )
    if isinstance(exc, URLError):
        reason = getattr(exc, "reason", None) or str(exc)
        return TiingoDataError("url_error", f"Tiingo network error: {reason}")
    if isinstance(exc, TimeoutError):
        return TiingoDataError("timeout", "Tiingo request timed out")
    return TiingoDataError(type(exc).__name__, "Tiingo request failed")


def fetch_eod_prices(
    symbol: str,
    start_date: str,
    end_date: str,
    *,
    allow_fallback: bool = True,
    allow_stale_cache: bool = False,
) -> tuple[list[dict], str]:
    """Return normalized bars and their source (tiingo or fixture)."""
    normalized_symbol = symbol.strip().upper()
    settings = load_settings()
    if not settings.tiingo_api_token:
        if allow_stale_cache:
            cached_bars = _read_cache(normalized_symbol)
            if cached_bars:
                return cached_bars, "tiingo_cache_no_token"
        if allow_fallback:
            return _fixture_prices(normalized_symbol), "fixture"
        raise TiingoDataError("missing_token", "TIINGO_API_TOKEN is not configured")

    query = urlencode({"startDate": start_date, "endDate": end_date})
    url = f"https://api.tiingo.com/tiingo/daily/{normalized_symbol}/prices?{query}"
    request = Request(url, headers={"Content-Type": "application/json", "Authorization": f"Token {settings.tiingo_api_token}"})
    try:
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        if allow_stale_cache:
            cached_bars = _read_cache(normalized_symbol)
            if cached_bars:
                return cached_bars, "tiingo_cache_after_error"
        if allow_fallback:
            return _fixture_prices(normalized_symbol), "fixture_after_tiingo_error"
        raise _safe_error(exc) from exc

    bars = _normalize_bars(normalized_symbol, payload)
    _write_cache(normalized_symbol, start_date, end_date, bars)
    return bars, "tiingo"
