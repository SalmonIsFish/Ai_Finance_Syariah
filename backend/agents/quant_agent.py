"""Local quant agent for rule-based signal evaluation."""

from datetime import date, datetime, timezone, timedelta

# Route through the provider switch rather than a single vendor: pinning the quant
# agent to tiingo let a tiingo outage blank the signal while the configured provider
# (MARKET_DATA_PROVIDER, alpaca by default) was healthy. The on-disk cache is shared
# by both providers, so cache metadata still comes from tiingo_prices.
import market_data
from config import load_settings
from tiingo_prices import read_cache_metadata


MIN_BARS = 200

# Which strategies the quant agent runs, in priority order. Both keep the same
# SMA50 > SMA200 trend filter -- S002 is a second entry trigger inside that
# regime, not a lower bar for entering it. Set QUANT_STRATEGIES=S001 to restore
# the single-strategy behaviour exactly.
DEFAULT_STRATEGIES = ["S001", "S002"]


def configured_strategies() -> list[str]:
    """QUANT_STRATEGIES, via config.load_settings() like every other setting."""
    try:
        return list(load_settings().quant_strategies) or list(DEFAULT_STRATEGIES)
    except Exception:
        # A malformed unrelated setting must not blank the quant agent.
        return list(DEFAULT_STRATEGIES)


def _indicators(bars: list[dict]) -> dict | None:
    """The numbers both strategies read. None when there is not enough history.

    Computed once and shared so the two strategies can never disagree about what
    the trend is -- only about what to do about it.
    """
    if len(bars) < MIN_BARS:
        return None
    closes = [float(bar["close"]) for bar in bars]
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200
    breakout_level = max(closes[-56:-1])
    latest_close = closes[-1]
    return {
        "closes": closes,
        "sma50": sma50,
        "sma200": sma200,
        "breakout_level": breakout_level,
        "latest_close": latest_close,
        "previous_close": closes[-2],
        "trend_ok": sma50 > sma200,
        "breakout_ok": latest_close >= breakout_level,
        "breakout_gap_pct": (
            round(((latest_close / breakout_level) - 1) * 100, 4) if breakout_level else None
        ),
        "trend_gap_pct": round(((sma50 / sma200) - 1) * 100, 4) if sma200 else None,
    }


def _insufficient_history(bars: list[dict], strategy_id: str) -> dict:
    return {
        "strategy_id": strategy_id,
        "signal": "NO_SIGNAL",
        "reason": "insufficient_history",
        "required_bars": MIN_BARS,
        "received_bars": len(bars),
        "blockers": ["insufficient_history"],
        "narrative": (
            f"Needs {MIN_BARS} daily bars to judge the trend; only {len(bars)} are available."
        ),
    }


def _shared_fields(core: dict) -> dict:
    """Fields every consumer already reads off `strategy`, so both strategies
    present the same shape (opportunity_scanner.py reads trend_ok, breakout_ok
    and breakout_level directly)."""
    return {
        "sma50": core["sma50"],
        "sma200": core["sma200"],
        "trend_ok": core["trend_ok"],
        "breakout_ok": core["breakout_ok"],
        "breakout_level": core["breakout_level"],
        "breakout_gap_pct": core["breakout_gap_pct"],
        "trend_gap_pct": core["trend_gap_pct"],
    }


def _evaluate_s002_signal(bars: list[dict]) -> dict:
    """S002: buy a dip inside an uptrend that is already turning back up.

    Same trend filter as S001, then three further conditions -- so S002 accepts a
    strict subset of "the trend is up", never more. It exists because S001 only
    ever buys new highs, which on a calm tape means the system has no opinion at
    all for weeks at a time.

    Conditions: SMA50 > SMA200 (identical to S001); price off the 55-day high
    (otherwise it is S001's breakout, not a pullback); price still at or above
    SMA50 (a dip through support is a breakdown); and today closing above
    yesterday (the dip has turned).
    """
    core = _indicators(bars)
    if core is None:
        return _insufficient_history(bars, "S002")

    latest = core["latest_close"]
    above_support = latest >= core["sma50"]
    is_pullback = not core["breakout_ok"]
    turning_up = latest > core["previous_close"]
    support_gap_pct = round(((latest / core["sma50"]) - 1) * 100, 4) if core["sma50"] else None

    blockers = []
    if not core["trend_ok"]:
        blockers.append("trend_not_confirmed")
    if not is_pullback:
        blockers.append("not_a_pullback")
    if not above_support:
        blockers.append("below_trend_support")
    if not turning_up:
        blockers.append("no_upturn_yet")

    signal = "BUY" if not blockers else "NO_SIGNAL"
    if signal == "BUY":
        narrative = (
            f"Pullback in an uptrend: {abs(core['breakout_gap_pct']):.2f}% off the 55-day high, "
            f"holding {support_gap_pct:+.2f}% above SMA50, and closing up on the day."
        )
    else:
        narrative = (
            "Pullback setup not present: "
            + ", ".join(
                {
                    "trend_not_confirmed": f"SMA50 {core['trend_gap_pct']:+.2f}% vs SMA200",
                    "not_a_pullback": "price is at the 55-day high, not off it",
                    "below_trend_support": f"price {support_gap_pct:+.2f}% vs SMA50 support",
                    "no_upturn_yet": "still closing lower day on day",
                }[name]
                for name in blockers
            )
            + "."
        )

    return {
        "strategy_id": "S002",
        "signal": signal,
        "reason": "pullback_in_uptrend_confirmed"
        if signal == "BUY"
        else "strategy_conditions_not_met",
        **_shared_fields(core),
        "support_gap_pct": support_gap_pct,
        "blockers": blockers,
        "narrative": narrative,
    }


STRATEGY_EVALUATORS = {}


def evaluate_strategies(bars: list[dict], names: list[str]) -> list[dict]:
    """Run each named strategy over the same bars, skipping unknown names."""
    selected = [name for name in names if name in STRATEGY_EVALUATORS]
    if not selected:
        selected = ["S001"]
    return [STRATEGY_EVALUATORS[name](bars) for name in selected]


def select_signal(results: list[dict]) -> dict:
    """First BUY in configured order, else the first result.

    Priority order matters for reporting, not for permission: a genuine breakout
    is reported as S001's breakout rather than as whatever else also happened to
    be true.
    """
    for result in results:
        if result.get("signal") == "BUY":
            return result
    return results[0]


def _evaluate_s001_signal(bars: list[dict]) -> dict:
    """S001: trade only an uptrend that is breaking out. Explains itself either way.

    The signal rule is unchanged and deliberately strict -- CLAUDE.md records
    that on 2026-08-20 it produced no BUY on 19 of 21 liquid large caps. That is
    the filter doing its job, but `strategy_conditions_not_met` gave a reader no
    way to tell a correct refusal from a broken agent. So every return now names
    which condition failed and by how much.

    Nothing here widens what may be approved: `signal` is still BUY only when
    both trend and breakout hold, and test_quant_strategy.py restates that rule
    independently so narration can never drift into permission.
    """
    if len(bars) < MIN_BARS:
        return _insufficient_history(bars, "S001")

    closes = [float(bar["close"]) for bar in bars]
    sma50 = sum(closes[-50:]) / 50
    sma200 = sum(closes[-200:]) / 200
    breakout_level = max(closes[-56:-1])
    latest_close = closes[-1]
    trend_ok = sma50 > sma200
    breakout = latest_close >= breakout_level
    breakout_gap_pct = (
        round(((latest_close / breakout_level) - 1) * 100, 4) if breakout_level else None
    )
    # How far the trend filter is from flipping, in the same units as the
    # breakout gap, so the two numbers can be read side by side.
    trend_gap_pct = round(((sma50 / sma200) - 1) * 100, 4) if sma200 else None

    blockers = []
    if not trend_ok:
        blockers.append("trend_not_confirmed")
    if not breakout:
        blockers.append("below_breakout_level")

    if trend_ok:
        trend_phrase = f"Trend confirmed (SMA50 {trend_gap_pct:+.2f}% vs SMA200)"
    else:
        trend_phrase = f"Trend not confirmed (SMA50 {trend_gap_pct:+.2f}% vs SMA200)"
    if breakout:
        breakout_phrase = f"breakout confirmed at or above the 55-day high of {breakout_level:.2f}"
    else:
        breakout_phrase = (
            f"{abs(breakout_gap_pct):.2f}% below the 55-day high of {breakout_level:.2f}"
        )
    narrative = f"{trend_phrase}; {breakout_phrase}."

    return {
        "strategy_id": "S001",
        "signal": "BUY" if (trend_ok and breakout) else "NO_SIGNAL",
        "reason": "trend_and_breakout_confirmed"
        if (trend_ok and breakout)
        else "strategy_conditions_not_met",
        "sma50": sma50,
        "sma200": sma200,
        "trend_ok": trend_ok,
        "breakout_ok": breakout,
        "breakout_level": breakout_level,
        "breakout_gap_pct": breakout_gap_pct,
        "trend_gap_pct": trend_gap_pct,
        "blockers": blockers,
        "narrative": narrative,
    }


def evaluate_quant(
    symbol: str, *, allow_fallback: bool = True, allow_stale_cache: bool = False
) -> dict:
    end_date = date.today()
    start_date = end_date - timedelta(days=320)
    bars, source = market_data.fetch_eod_prices(
        symbol,
        start_date.isoformat(),
        end_date.isoformat(),
        allow_fallback=allow_fallback,
        allow_stale_cache=allow_stale_cache,
    )
    freshness = data_freshness(symbol, source)
    results = evaluate_strategies(bars, configured_strategies())
    strategy = select_signal(results)
    close = float(bars[-1]["close"]) if bars else None
    return {
        "agent": "quant",
        "status": "PASS" if strategy.get("signal") == "BUY" else "NO_SIGNAL",
        "symbol": symbol,
        "signal": strategy.get("signal"),
        "reason": strategy.get("reason"),
        "price": close,
        "bars": len(bars),
        "price_source": source,
        **freshness,
        # `strategy` stays the single dict every existing consumer reads
        # (opportunity_scanner.py pulls trend_ok/breakout_ok/breakout_level off
        # it); `strategies` is additive, carrying what each one said.
        "strategy": strategy,
        "signal_source": strategy.get("strategy_id"),
        "strategies": results,
    }


LIVE_SOURCES = {"tiingo", "alpaca", "alpaca_iex"}


def data_freshness(symbol: str, source: str) -> dict:
    if source in LIVE_SOURCES:
        return {"data_freshness": "live", "cache_cached_at": None, "cache_age_hours": None}
    if "_cache" not in source:
        return {
            "data_freshness": "fixture" if source.startswith("fixture") else "unknown",
            "cache_cached_at": None,
            "cache_age_hours": None,
        }
    metadata = read_cache_metadata(symbol)
    cached_at = metadata.get("cached_at")
    age_hours = None
    if cached_at:
        try:
            cached_dt = datetime.fromisoformat(cached_at)
            if cached_dt.tzinfo is None:
                cached_dt = cached_dt.replace(tzinfo=timezone.utc)
            age_hours = round((datetime.now(timezone.utc) - cached_dt).total_seconds() / 3600, 2)
        except ValueError:
            age_hours = None
    return {"data_freshness": "cached", "cache_cached_at": cached_at, "cache_age_hours": age_hours}


STRATEGY_EVALUATORS.update({"S001": _evaluate_s001_signal, "S002": _evaluate_s002_signal})
