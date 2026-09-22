"""Verify agent_coordinator composes the option-structure gate without
disturbing the existing equity-only candidate path."""

from agent_coordinator import evaluate_candidate


SHARIAH_PASS = {"agent": "shariah", "status": "PASS", "reason": "symbol_compliant"}
SHARIAH_REJECT = {"agent": "shariah", "status": "REJECT", "reason": "non_compliant_business"}
QUANT_BUY = {"agent": "quant", "signal": "BUY", "price": 100.0}

# The real shape agents/quant_agent.py returned for CVX on 2026-08-20: a trend it
# likes, but no breakout, so no entry signal. 19 of 21 liquid large caps looked
# like this that day.
QUANT_NO_SIGNAL = {
    "agent": "quant",
    "status": "NO_SIGNAL",
    "signal": "NO_SIGNAL",
    "reason": "strategy_conditions_not_met",
    "price": 205.78,
    "strategy": {
        "signal": "NO_SIGNAL",
        "reason": "strategy_conditions_not_met",
        "trend_ok": True,
        "breakout_ok": False,
        "breakout_level": 207.68,
        "breakout_gap_pct": -0.91,
    },
}


def option_candidate(*, side, quant, shariah=SHARIAH_PASS, structure=None):
    """A Level 1 option candidate; only the fields under test vary."""
    return evaluate_candidate(
        symbol="CVX",
        side=side,
        quantity=1,
        price=0.13,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=shariah,
        quant_override=quant,
        asset_class="option",
        option_structure=structure,
    )


def check_synthetic_market_data_blocks() -> None:
    """Fabricated prices must never clear a gate, for any asset class.

    alpaca_market_data falls back to fixture_prices() when Alpaca errors and
    labels the result "fixture_after_alpaca_error"; quant_agent turns that into
    data_freshness "fixture". Nothing read that label, so a market-data outage
    could produce a BUY computed entirely from test data that passed every gate
    while truthfully reporting where it came from.

    Deliberately NOT exempt for options, unlike the BUY-signal rule. A covered
    call written against a fabricated price is as wrong as an equity entry.
    """
    base = dict(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        price=100.0,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
    )

    live = evaluate_candidate(**base, quant_override={**QUANT_BUY, "data_freshness": "live"})
    assert "synthetic_market_data" not in live["blockers"], live["blockers"]
    assert live["decision"] == "READY_FOR_APPROVAL", live

    # Real Alpaca data inside its TTL. Permitted -- the age is reported, and
    # refusing every cached bar would make the system unusable whenever the
    # market is closed.
    cached = evaluate_candidate(**base, quant_override={**QUANT_BUY, "data_freshness": "cached"})
    assert "synthetic_market_data" not in cached["blockers"], cached["blockers"]

    fixture = evaluate_candidate(**base, quant_override={**QUANT_BUY, "data_freshness": "fixture"})
    assert "synthetic_market_data" in fixture["blockers"], fixture["blockers"]
    assert fixture["decision"] == "BLOCKED", fixture

    # Provenance that cannot be established is not provenance.
    unknown = evaluate_candidate(**base, quant_override={**QUANT_BUY, "data_freshness": "unknown"})
    assert "synthetic_market_data" in unknown["blockers"], unknown["blockers"]

    # The option path gets no exemption.
    option_fixture = option_candidate(
        side="SELL",
        quant={**QUANT_BUY, "data_freshness": "fixture"},
        structure={"structure": "covered_call", "shares_held": 100, "contracts": 1},
    )
    assert "synthetic_market_data" in option_fixture["blockers"], option_fixture["blockers"]

    print("PASS: synthetic market data blocks every asset class; live and cached do not.")


def main() -> None:
    # Existing equity-only behavior must be unaffected when no option_structure
    # is supplied: no "option_structure" key appears in agent_summary at all.
    equity_only = evaluate_candidate(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        price=None,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
        quant_override=QUANT_BUY,
    )
    assert "option_structure" not in equity_only["agent_summary"]
    assert equity_only["decision"] == "READY_FOR_APPROVAL"

    # A covered call backed by enough owned shares should not block approval.
    covered_call_ready = evaluate_candidate(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        price=None,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
        quant_override=QUANT_BUY,
        option_structure={"structure": "covered_call", "shares_held": 100, "contracts": 1},
    )
    assert covered_call_ready["decision"] == "READY_FOR_APPROVAL"
    assert covered_call_ready["agent_summary"]["option_structure"]["status"] == "PASS"
    assert "option_structure_rejected" not in covered_call_ready["blockers"]

    # A naked call must block approval even if shariah/quant/risk all pass.
    naked_call_blocked = evaluate_candidate(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        price=None,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
        quant_override=QUANT_BUY,
        option_structure={"structure": "naked_call"},
    )
    assert naked_call_blocked["decision"] == "BLOCKED"
    assert "option_structure_rejected" in naked_call_blocked["blockers"]
    assert naked_call_blocked["agent_summary"]["option_structure"]["status"] == "REJECT"

    # A covered call is written by SELLING to open -- the coordinator must not
    # apply the equity-only "BUY side only" restriction to option orders.
    covered_call_sell = evaluate_candidate(
        symbol="AAPL",
        side="SELL",
        quantity=1,
        price=3.50,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
        quant_override=QUANT_BUY,
        asset_class="option",
        option_structure={"structure": "covered_call", "shares_held": 100, "contracts": 1},
    )
    assert "only_buy_side_supported" not in covered_call_sell["blockers"]
    assert covered_call_sell["decision"] == "READY_FOR_APPROVAL"

    # The restriction still applies to plain equity SELL orders -- this
    # coordinator isn't the reduce-only SELL gate (that's the portfolio risk
    # overlay in local_api.py); it just shouldn't block options on side alone.
    equity_sell_still_blocked = evaluate_candidate(
        symbol="AAPL",
        side="SELL",
        quantity=1,
        price=100.0,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
        quant_override=QUANT_BUY,
    )
    assert "only_buy_side_supported" in equity_sell_still_blocked["blockers"]

    # ------------------------------------------------------------------------
    # A sell-to-open option structure must not require a fresh bullish signal.
    #
    # This is the bug found on 2026-08-20: a CVX cash-secured put that passed
    # the Shariah screen, the structure gate and every risk limit was blocked
    # at /paper/preview for one reason only -- quant_no_buy_signal, an equity
    # breakout filter. The quant agent's job is deciding whether to open a
    # directional long. Neither Level 1 structure is one: a covered call is
    # written against stock already owned, and a cash-secured put says "willing
    # to own at this price", not "this is breaking out today".
    # ------------------------------------------------------------------------
    cash_secured_put = option_candidate(
        side="SELL",
        quant=QUANT_NO_SIGNAL,
        structure={
            "structure": "cash_secured_put",
            "cash_collateral": 20_000.0,
            "strike": 197.5,
            "contracts": 1,
        },
    )
    assert "quant_no_buy_signal" not in cash_secured_put["blockers"], cash_secured_put["blockers"]
    assert cash_secured_put["decision"] == "READY_FOR_APPROVAL", cash_secured_put

    covered_call = option_candidate(
        side="SELL",
        quant=QUANT_NO_SIGNAL,
        structure={"structure": "covered_call", "shares_held": 100, "contracts": 1},
    )
    assert "quant_no_buy_signal" not in covered_call["blockers"], covered_call["blockers"]
    assert covered_call["decision"] == "READY_FOR_APPROVAL", covered_call

    # Buying back a short leg reduces risk. Demanding a breakout before you are
    # allowed to close a position would be worse than demanding one to open it.
    buy_to_close = option_candidate(
        side="BUY",
        quant=QUANT_NO_SIGNAL,
        structure={"structure": "covered_call", "shares_held": 100, "contracts": 1},
    )
    assert "quant_no_buy_signal" not in buy_to_close["blockers"], buy_to_close["blockers"]

    # The signal is not faked to make this pass -- it is reported honestly and
    # simply is not a blocker for options. A summary that lied about the signal
    # would be worse than the bug.
    assert cash_secured_put["agent_summary"]["quant"]["signal"] == "NO_SIGNAL"

    # ---------------------------------------------------------------- guards
    # The exemption is scoped to options. A directional equity entry with no
    # signal must still be blocked; that filter is the whole point of the quant
    # agent and removing it there would be a different bug.
    equity_entry = evaluate_candidate(
        symbol="CVX",
        side="BUY",
        quantity=1,
        price=205.78,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=SHARIAH_PASS,
        quant_override=QUANT_NO_SIGNAL,
    )
    assert "quant_no_buy_signal" in equity_entry["blockers"], equity_entry["blockers"]
    assert equity_entry["decision"] == "BLOCKED"

    # Only the quant blocker was lifted. Every other reason an option order can
    # be refused must still fire on exactly the same candidate.
    naked_with_no_signal = option_candidate(
        side="SELL", quant=QUANT_NO_SIGNAL, structure={"structure": "naked_call"}
    )
    assert "option_structure_rejected" in naked_with_no_signal["blockers"]
    assert naked_with_no_signal["decision"] == "BLOCKED"

    non_compliant_with_no_signal = option_candidate(
        side="SELL",
        quant=QUANT_NO_SIGNAL,
        shariah=SHARIAH_REJECT,
        structure={"structure": "covered_call", "shares_held": 100, "contracts": 1},
    )
    assert "shariah_rejected" in non_compliant_with_no_signal["blockers"]
    assert non_compliant_with_no_signal["decision"] == "BLOCKED"

    print("PASS: agent_coordinator composes the option-structure gate additively.")
    check_synthetic_market_data_blocks()


if __name__ == "__main__":
    main()
