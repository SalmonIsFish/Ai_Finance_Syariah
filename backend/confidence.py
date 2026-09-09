"""Quant attractiveness scorer — investment quality only, NOT compliance.

This module answers "how attractive is this trade?" It does NOT answer
"is this trade permissible?" Compliance is decided solely by the gate chain.

A high quant score NEVER compensates for: Shariah REJECT, Shariah UNKNOWN,
risk failure, or account failure. Compliance must never be a weighted
component of attractiveness.
"""

from __future__ import annotations


def score_attractiveness(
    *,
    strategy_result: dict,
    bars: list[dict],
    risk_headroom: float | None = None,
) -> dict:
    """Score trade attractiveness from 0.0 to 1.0.

    Components (all independent of Shariah compliance):
      technical  — signal strength from the quant strategy
      volume     — liquidity proxy from recent trading volume
      risk_room  — how much room remains within risk limits

    Returns a dict with total score and per-component breakdown.
    """
    tech = _technical_score(strategy_result)
    vol = _volume_score(bars)
    risk = risk_headroom if risk_headroom is not None else 0.5

    total = round(0.50 * tech + 0.30 * vol + 0.20 * risk, 4)

    return {
        "attractiveness": total,
        "components": {
            "technical": round(tech, 4),
            "volume": round(vol, 4),
            "risk_headroom": round(risk, 4),
        },
        "weights": {"technical": 0.50, "volume": 0.30, "risk_headroom": 0.20},
    }


def _technical_score(strategy: dict) -> float:
    if strategy.get("signal") != "BUY":
        return 0.0

    score = 0.5

    trend_gap = strategy.get("trend_gap_pct")
    if trend_gap is not None and trend_gap > 0:
        score += min(trend_gap / 10.0, 0.25)

    breakout_gap = strategy.get("breakout_gap_pct")
    if breakout_gap is not None and breakout_gap > 0:
        score += min(breakout_gap / 5.0, 0.25)

    return min(score, 1.0)


def _volume_score(bars: list[dict]) -> float:
    if not bars or len(bars) < 20:
        return 0.0

    recent = bars[-5:]
    avg_20 = sum(b["volume"] for b in bars[-20:]) / 20.0

    if avg_20 <= 0:
        return 0.0

    recent_avg = sum(b["volume"] for b in recent) / len(recent)
    ratio = recent_avg / avg_20

    if ratio >= 1.5:
        return 1.0
    if ratio >= 1.0:
        return 0.5 + 0.5 * ((ratio - 1.0) / 0.5)
    return max(ratio * 0.5, 0.1)
