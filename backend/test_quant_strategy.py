"""Verify the quant strategy explains itself without changing what it decides.

CLAUDE.md records that on 2026-08-20 the S001 filter produced no BUY on 19 of 21
liquid large caps. That is the strategy working as designed -- but `reason:
"strategy_conditions_not_met"` gives a reader nothing to act on, so a correct
NO_SIGNAL is indistinguishable from a broken agent.

These tests pin the diagnostic surface (which condition failed, and by how much)
and, just as importantly, pin that adding it did not move a single signal.
"""

from agents.quant_agent import _evaluate_s001_signal


def rising_closes(count: int = 220, start: float = 100.0, step: float = 0.5) -> list[dict]:
    """A clean uptrend: SMA50 sits above SMA200 and the last bar is the high."""
    return [{"close": start + index * step} for index in range(count)]


def falling_closes(count: int = 220, start: float = 200.0, step: float = 0.5) -> list[dict]:
    return [{"close": start - index * step} for index in range(count)]


def check_insufficient_history_is_named() -> None:
    result = _evaluate_s001_signal(rising_closes(count=120))
    assert result["signal"] == "NO_SIGNAL", result
    assert result["reason"] == "insufficient_history", result
    assert result["received_bars"] == 120, result
    assert result["required_bars"] == 200, result
    # Even here the reader is told what is missing rather than left guessing.
    assert "blockers" in result, result
    assert result["blockers"] == ["insufficient_history"], result
    assert "200" in result["narrative"] and "120" in result["narrative"], result


def check_buy_still_fires_and_explains_itself() -> None:
    result = _evaluate_s001_signal(rising_closes())
    assert result["signal"] == "BUY", result
    assert result["reason"] == "trend_and_breakout_confirmed", result
    assert result["trend_ok"] is True, result
    assert result["breakout_ok"] is True, result
    assert result["blockers"] == [], result
    assert result["trend_gap_pct"] is not None and result["trend_gap_pct"] > 0, result
    narrative = result["narrative"].lower()
    assert "trend" in narrative and "breakout" in narrative, result


def check_pullback_names_the_missing_condition_and_the_distance() -> None:
    """The case CLAUDE.md says dominates: trend fine, simply not breaking out."""
    bars = rising_closes()
    breakout_level = max(float(b["close"]) for b in bars[-56:-1])
    # Sit 2% under the trigger.
    bars[-1] = {"close": breakout_level * 0.98}

    result = _evaluate_s001_signal(bars)
    assert result["signal"] == "NO_SIGNAL", result
    assert result["trend_ok"] is True, result
    assert result["breakout_ok"] is False, result
    assert result["blockers"] == ["below_breakout_level"], result
    # The number a reader actually wants: how far off is it?
    assert result["breakout_gap_pct"] is not None, result
    assert abs(result["breakout_gap_pct"] - (-2.0)) < 0.01, result
    narrative = result["narrative"]
    assert "2.00%" in narrative, narrative
    assert "below" in narrative.lower(), narrative
    # Trend is reported as satisfied, not silently lumped in with the failure.
    assert "trend" in narrative.lower(), narrative


def check_downtrend_names_the_trend_not_the_breakout() -> None:
    result = _evaluate_s001_signal(falling_closes())
    assert result["signal"] == "NO_SIGNAL", result
    assert result["trend_ok"] is False, result
    assert "trend_not_confirmed" in result["blockers"], result
    assert result["trend_gap_pct"] is not None and result["trend_gap_pct"] < 0, result
    assert "sma50" in result["narrative"].lower(), result["narrative"]


def check_both_conditions_failing_lists_both() -> None:
    bars = falling_closes()
    breakout_level = max(float(b["close"]) for b in bars[-56:-1])
    bars[-1] = {"close": breakout_level * 0.9}
    result = _evaluate_s001_signal(bars)
    assert result["signal"] == "NO_SIGNAL", result
    assert result["blockers"] == ["trend_not_confirmed", "below_breakout_level"], result


def check_legibility_did_not_change_any_decision() -> None:
    """The whole point: more explanation, identical verdicts.

    Every case above is re-checked here against the signal rule stated directly,
    so a future edit that quietly loosens the filter while adding narration
    fails rather than passing for looking helpful.
    """
    cases = []

    buy = rising_closes()
    cases.append((buy, "BUY"))

    pullback = rising_closes()
    level = max(float(b["close"]) for b in pullback[-56:-1])
    pullback[-1] = {"close": level * 0.98}
    cases.append((pullback, "NO_SIGNAL"))

    # Exactly at the trigger is a breakout: the rule is >=, not >.
    at_trigger = rising_closes()
    level = max(float(b["close"]) for b in at_trigger[-56:-1])
    at_trigger[-1] = {"close": level}
    cases.append((at_trigger, "BUY"))

    # A hair under is not.
    under = rising_closes()
    level = max(float(b["close"]) for b in under[-56:-1])
    under[-1] = {"close": level * 0.9999}
    cases.append((under, "NO_SIGNAL"))

    cases.append((falling_closes(), "NO_SIGNAL"))
    cases.append((rising_closes(count=199), "NO_SIGNAL"))

    for bars, expected in cases:
        result = _evaluate_s001_signal(bars)
        assert result["signal"] == expected, (expected, result)
        # And the independent restatement of the rule agrees with the agent.
        if result.get("trend_ok") is not None:
            rule = "BUY" if (result["trend_ok"] and result["breakout_ok"]) else "NO_SIGNAL"
            assert rule == result["signal"], result


def main() -> None:
    check_insufficient_history_is_named()
    check_buy_still_fires_and_explains_itself()
    check_pullback_names_the_missing_condition_and_the_distance()
    check_downtrend_names_the_trend_not_the_breakout()
    check_both_conditions_failing_lists_both()
    check_legibility_did_not_change_any_decision()
    print("PASS: the quant strategy explains why it said no, and decides exactly as before.")


if __name__ == "__main__":
    main()
