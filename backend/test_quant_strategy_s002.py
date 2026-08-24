"""Verify S002 (pullback in an uptrend) and how it combines with S001.

S001 buys strength: an uptrend making a new 55-day high. It is deliberately
strict, and CLAUDE.md records it finding nothing on 19 of 21 large caps on
2026-08-20. S002 is the complement -- the same uptrend, bought on a dip that is
still holding the 50-day average and has started to turn back up.

The safety property under test is that S002 is NARROWER than "trend only", not
looser than S001: it keeps S001's exact trend filter and adds three more
conditions on top. A second strategy must give the system a second way to say
yes, never a lower bar for saying it.
"""

import os

from agents.quant_agent import (
    _evaluate_s001_signal,
    _evaluate_s002_signal,
    evaluate_strategies,
    select_signal,
)


def uptrend(count: int = 220, start: float = 100.0, step: float = 0.5) -> list[float]:
    return [start + index * step for index in range(count)]


def bars(closes: list[float]) -> list[dict]:
    return [{"close": value} for value in closes]


def pullback_series() -> list[float]:
    """A clean uptrend that dips off the highs but holds above SMA50, turning up."""
    closes = uptrend()
    sma50 = sum(closes[-50:]) / 50
    # Two down days off the high, then an up day, all comfortably above SMA50.
    closes[-3] = sma50 * 1.10
    closes[-2] = sma50 * 1.06
    closes[-1] = sma50 * 1.08
    return closes


def check_s002_fires_on_a_pullback_s001_refuses() -> None:
    series = pullback_series()
    s001 = _evaluate_s001_signal(bars(series))
    s002 = _evaluate_s002_signal(bars(series))

    # S001 refuses: this is not a new high.
    assert s001["signal"] == "NO_SIGNAL", s001
    assert "below_breakout_level" in s001["blockers"], s001

    # S002 takes it: same uptrend, bought on the dip.
    assert s002["signal"] == "BUY", s002
    assert s002["strategy_id"] == "S002", s002
    assert s002["trend_ok"] is True, s002
    assert s002["blockers"] == [], s002
    assert "pullback" in s002["narrative"].lower(), s002["narrative"]


def check_s002_keeps_s001s_trend_filter() -> None:
    """The one property that makes a second strategy safe rather than looser."""
    downtrend = [200.0 - index * 0.5 for index in range(220)]
    s001 = _evaluate_s001_signal(bars(downtrend))
    s002 = _evaluate_s002_signal(bars(downtrend))
    assert s001["trend_ok"] is False, s001
    assert s002["signal"] == "NO_SIGNAL", s002
    assert "trend_not_confirmed" in s002["blockers"], s002

    # Every series S002 accepts, S001's trend filter also accepts.
    for series in [pullback_series(), uptrend(), downtrend]:
        a = _evaluate_s001_signal(bars(series))
        b = _evaluate_s002_signal(bars(series))
        if b["signal"] == "BUY":
            assert a["trend_ok"] is True, (a, b)


def check_s002_refuses_a_broken_down_pullback() -> None:
    """Below SMA50 is not a pullback, it is a breakdown."""
    closes = uptrend()
    sma50 = sum(closes[-50:]) / 50
    closes[-2] = sma50 * 0.90
    closes[-1] = sma50 * 0.92  # turning up, but under support
    result = _evaluate_s002_signal(bars(closes))
    assert result["signal"] == "NO_SIGNAL", result
    assert "below_trend_support" in result["blockers"], result


def check_s002_requires_the_turn() -> None:
    """Still falling is not a pullback worth taking."""
    closes = uptrend()
    sma50 = sum(closes[-50:]) / 50
    closes[-2] = sma50 * 1.10
    closes[-1] = sma50 * 1.06  # still going down
    result = _evaluate_s002_signal(bars(closes))
    assert result["signal"] == "NO_SIGNAL", result
    assert "no_upturn_yet" in result["blockers"], result


def check_s002_refuses_at_the_highs() -> None:
    """At a new high it is S001's trade, not a pullback."""
    result = _evaluate_s002_signal(bars(uptrend()))
    assert result["signal"] == "NO_SIGNAL", result
    assert "not_a_pullback" in result["blockers"], result


def check_selection_reports_which_strategy_fired() -> None:
    series = pullback_series()
    results = evaluate_strategies(bars(series), ["S001", "S002"])
    assert [r["strategy_id"] for r in results] == ["S001", "S002"], results

    chosen = select_signal(results)
    assert chosen["signal"] == "BUY", chosen
    assert chosen["strategy_id"] == "S002", chosen

    # With only S001 configured, the same series is refused -- the second
    # strategy is opt-in, and turning it off restores the old behaviour exactly.
    only_s001 = select_signal(evaluate_strategies(bars(series), ["S001"]))
    assert only_s001["signal"] == "NO_SIGNAL", only_s001
    assert only_s001["strategy_id"] == "S001", only_s001


def check_the_two_strategies_are_mutually_exclusive() -> None:
    """They can never both fire, which is what keeps "two strategies" honest.

    S001 requires a new 55-day high; S002 requires price to be off it. So the
    pair cannot double-count one setup as two confirmations, and adding S002
    cannot turn a marginal S001 refusal into a BUY -- it can only cover a
    disjoint case. Selection order is therefore about labelling, not permission.
    """
    closes = uptrend()
    sma50 = sum(closes[-50:]) / 50
    level = max(closes[-56:-1])
    series = [
        uptrend(),  # breakout
        pullback_series(),  # pullback
        [200.0 - i * 0.5 for i in range(220)],  # downtrend
        uptrend()[:-1] + [level * 0.999],  # just under the high
        uptrend()[:-1] + [level],  # exactly at the high
        uptrend()[:-1] + [sma50 * 0.95],  # broken support
    ]
    for closes in series:
        a = _evaluate_s001_signal(bars(closes))
        b = _evaluate_s002_signal(bars(closes))
        assert not (a["signal"] == "BUY" and b["signal"] == "BUY"), (a, b)

    # A breakout is still reported as S001's breakout.
    chosen = select_signal(evaluate_strategies(bars(uptrend()), ["S001", "S002"]))
    assert chosen["signal"] == "BUY" and chosen["strategy_id"] == "S001", chosen


def check_no_buy_reports_the_first_configured_strategy() -> None:
    """When nothing fires the caller still gets a full, legible result."""
    downtrend = [200.0 - index * 0.5 for index in range(220)]
    chosen = select_signal(evaluate_strategies(bars(downtrend), ["S001", "S002"]))
    assert chosen["signal"] == "NO_SIGNAL", chosen
    assert chosen["strategy_id"] == "S001", chosen
    assert chosen["blockers"], chosen
    assert chosen["narrative"], chosen


def check_unknown_strategy_is_ignored_not_fatal() -> None:
    results = evaluate_strategies(bars(uptrend()), ["S001", "NOPE", "S002"])
    assert [r["strategy_id"] for r in results] == ["S001", "S002"], results
    # And an empty/garbage configuration falls back to S001 rather than
    # silently evaluating nothing and reporting no signal for the wrong reason.
    results = evaluate_strategies(bars(uptrend()), ["NOPE"])
    assert [r["strategy_id"] for r in results] == ["S001"], results


def check_configured_strategies_come_from_settings() -> None:
    from agents.quant_agent import configured_strategies

    saved = os.environ.get("QUANT_STRATEGIES")
    try:
        os.environ["QUANT_STRATEGIES"] = "S001"
        assert configured_strategies() == ["S001"], configured_strategies()
        os.environ["QUANT_STRATEGIES"] = "s002, s001"
        assert configured_strategies() == ["S002", "S001"], configured_strategies()
        os.environ["QUANT_STRATEGIES"] = ""
        assert configured_strategies() == ["S001", "S002"], configured_strategies()
    finally:
        if saved is None:
            os.environ.pop("QUANT_STRATEGIES", None)
        else:
            os.environ["QUANT_STRATEGIES"] = saved


def main() -> None:
    check_s002_fires_on_a_pullback_s001_refuses()
    check_s002_keeps_s001s_trend_filter()
    check_s002_refuses_a_broken_down_pullback()
    check_s002_requires_the_turn()
    check_s002_refuses_at_the_highs()
    check_selection_reports_which_strategy_fired()
    check_the_two_strategies_are_mutually_exclusive()
    check_no_buy_reports_the_first_configured_strategy()
    check_unknown_strategy_is_ignored_not_fatal()
    check_configured_strategies_come_from_settings()
    print("PASS: S002 adds a second way to say yes, never a lower bar for saying it.")


if __name__ == "__main__":
    main()
