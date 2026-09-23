"""Verify option_strategy picks a defensible strike and refuses to pick a bad one.

The chain is injected rather than fetched, so nothing here touches Alpaca; the one
test that exercises the fetch path swaps option_strategy._load_chain, which is the
single seam every chain read goes through.

The assertions target the *choice and its evidence* — which strike, why that one, and
what the rejected contracts were rejected for — because a selection that lands on the
right strike for the wrong reason is not a rule anyone can defend in a demo.
"""

from datetime import date

import option_strategy
from option_strategy import (
    rank_candidates,
    select_cash_secured_put,
    select_covered_call,
)
from shariah_candidate import build_shariah_candidate
from approval_workflow import approve_candidate
from option_permissibility import (
    OPTION_DETERMINATION,
    OPTION_POLICY_PERMITTED,
    REASON_NOT_PERMITTED,
)

# Test-only seam; see option_permissibility.py.
PERMITTED_DETERMINATION = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)


TODAY = date(2026, 8, 19)
SPOT = 100.0

SHARIAH_PASS = {"agent": "shariah", "status": "PASS", "provider": "SEC_EDGAR", "reason": "COMPLIANT"}


def contract(strike, *, option_type="CALL", expiration="2026-08-24", bid=1.00, ask=1.04, **overrides):
    """A chain row in the exact shape alpaca_market_data.fetch_option_chain returns."""
    mid = None
    if bid is not None and ask is not None:
        mid = round((bid + ask) / 2, 4)
    row = {
        "symbol": f"TEST{str(expiration).replace('-', '')[2:]}{option_type[0]}{int(strike * 1000):08d}",
        "underlying": "TEST",
        "option_type": option_type,
        "strike": strike,
        "expiration": expiration,
        "style": "american",
        "status": "active",
        "tradable": True,
        "multiplier": 100,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": mid,
    }
    row.update(overrides)
    return row


# A spread of strikes around a spot of 100, all 5 DTE.
CALL_CHAIN = [contract(strike) for strike in (98.0, 100.0, 102.0, 103.0, 104.0, 105.0, 107.0, 112.0)]
PUT_CHAIN = [contract(strike, option_type="PUT") for strike in (88.0, 93.0, 95.0, 96.0, 98.0, 100.0, 102.0)]


def main() -> None:
    covered_call_picks_the_target_strike()
    cash_secured_put_picks_and_sizes()
    band_and_window_are_enforced()
    quality_filters_reject_unsellable_contracts()
    ranking_is_deterministic()
    sizing_walks_the_ranking()
    refuses_rather_than_guesses()
    chain_failure_is_not_a_selection()
    feeds_the_gate_chain()
    print("PASS: option_strategy selects a defensible strike, sizes it from real collateral, and fails closed.")


def covered_call_picks_the_target_strike() -> None:
    result = select_covered_call("test", shares_held=250, spot=SPOT, chain=CALL_CHAIN, as_of=TODAY)
    assert result["status"] == "SELECTED", result

    # 104 is 4.0% OTM, exactly the target. 103 and 105 are equally close in absolute
    # terms only if the target moved; with a 4.0 target, 104 wins outright.
    assert result["option_contract"]["strike"] == 104.0, result
    assert result["otm_pct"] == 4.0, result
    assert result["dte"] == 5, result

    # Sized from owned shares, not from appetite: 250 shares covers two contracts and
    # the 50 shares left over cover nothing.
    assert result["contracts"] == 2, result
    assert result["shares_committed"] == 200, result
    assert result["estimated_credit"] == 204.0, result

    # The contract dict is exactly what the preview endpoint and the OCC builder want.
    assert result["option_contract"]["strategy"] == "COVERED_CALL", result
    assert result["option_contract"]["option_type"] == "CALL", result
    assert result["option_contract"]["underlying"] == "TEST", result
    assert result["side"] == "SELL", result

    # The rationale has to carry the numbers a human would be asked about.
    rationale = result["rationale"]
    for fragment in ("covered call", "4.0% OTM", "104.00", "5 days", "200 of 250 owned shares"):
        assert fragment in rationale, (fragment, rationale)


def cash_secured_put_picks_and_sizes() -> None:
    result = select_cash_secured_put("TEST", cash_available=20_000.0, spot=SPOT, chain=PUT_CHAIN, as_of=TODAY)
    assert result["status"] == "SELECTED", result

    # A put is OTM *below* spot, so the 4% target is strike 96.
    assert result["option_contract"]["strike"] == 96.0, result
    assert result["otm_pct"] == 4.0, result
    assert result["option_contract"]["option_type"] == "PUT", result

    # 20,000 secures two contracts at 96 (19,200), not three (28,800).
    assert result["contracts"] == 2, result
    assert result["cash_required"] == 19_200.0, result
    assert "19200.00 of 20000.00 settled cash" in result["rationale"], result["rationale"]


def band_and_window_are_enforced() -> None:
    # Strikes at and inside the money are never selected, however rich the premium.
    ranked, rejected = rank_candidates(
        CALL_CHAIN, strategy="COVERED_CALL", spot=SPOT, today=TODAY, policy=option_strategy.DEFAULT_POLICY
    )
    strikes = [row["strike"] for row in ranked]
    assert 100.0 not in strikes and 98.0 not in strikes, strikes
    assert 112.0 not in strikes, "12% OTM is outside the 2-7% band"
    assert rejected["outside_otm_band"] == 3, rejected  # 98 (ITM), 100 (ATM), 112 (12% OTM)

    # Everything outside 1-7 DTE goes, including a same-day expiry: the order has to
    # survive the human confirmation step before it is worth anything.
    dated = [
        contract(104.0, expiration="2026-08-19"),  # 0 DTE
        contract(104.0, expiration="2026-08-20"),  # 1 DTE
        contract(104.0, expiration="2026-09-18"),  # 30 DTE
    ]
    ranked, rejected = rank_candidates(
        dated, strategy="COVERED_CALL", spot=SPOT, today=TODAY, policy=option_strategy.DEFAULT_POLICY
    )
    assert [row["dte"] for row in ranked] == [1], ranked
    assert rejected["outside_dte_window"] == 2, rejected

    # ...but the floor is policy, not law, and can be opened up deliberately.
    ranked, _ = rank_candidates(
        dated,
        strategy="COVERED_CALL",
        spot=SPOT,
        today=TODAY,
        policy=option_strategy.resolve_policy({"min_dte": 0}),
    )
    assert sorted(row["dte"] for row in ranked) == [0, 1], ranked


def quality_filters_reject_unsellable_contracts() -> None:
    chain = [
        contract(104.0, bid=None, ask=1.10),                      # cannot sell into no bid
        contract(104.0, bid=0.0, ask=1.10),                       # same, quoted as zero
        contract(104.0, bid=0.01, ask=0.02),                      # premium under the floor
        contract(104.0, bid=1.00, ask=1.60),                      # 46% spread
        contract(104.0, bid=1.20, ask=1.00),                      # crossed quote
        contract(104.0, tradable=False),
        contract(104.0, status="inactive"),
        contract(104.0, multiplier=137),                          # adjusted contract
        contract(104.0, option_type="PUT"),
        contract(104.0, expiration="not-a-date"),
        {**contract(104.0), "strike": 0.0},
        "not even a dict",
    ]
    ranked, rejected = rank_candidates(
        chain, strategy="COVERED_CALL", spot=SPOT, today=TODAY, policy=option_strategy.DEFAULT_POLICY
    )
    assert ranked == [], ranked
    for reason in (
        "no_live_bid",
        "premium_below_floor",
        "spread_too_wide",
        "crossed_quote",
        "not_tradable",
        "non_standard_multiplier",
        "wrong_option_type",
        "unparseable_expiration",
        "invalid_strike",
        "malformed_row",
    ):
        assert rejected.get(reason), (reason, rejected)
    assert rejected["no_live_bid"] == 2, rejected

    # A non-standard multiplier is the dangerous one: 100 shares does not cover a
    # contract deliverable on 137, so it must never be sized as if it did.
    result = select_covered_call("TEST", shares_held=100, spot=SPOT, chain=[contract(104.0, multiplier=137)], as_of=TODAY)
    assert result["status"] == "NO_CANDIDATE", result
    assert "multiplier" in result["reason"], result


def ranking_is_deterministic() -> None:
    # Two strikes equidistant from the 4% target: 103 (3% OTM) and 105 (5% OTM).
    # Neither wins on distance, so the documented tie-break decides — later expiry
    # first, then richer premium — and it must decide the same way every time.
    pair = [
        contract(103.0, expiration="2026-08-24", bid=1.00, ask=1.04),
        contract(105.0, expiration="2026-08-26", bid=0.80, ask=0.84),
    ]
    first, _ = rank_candidates(pair, strategy="COVERED_CALL", spot=SPOT, today=TODAY, policy=option_strategy.DEFAULT_POLICY)
    second, _ = rank_candidates(
        list(reversed(pair)), strategy="COVERED_CALL", spot=SPOT, today=TODAY, policy=option_strategy.DEFAULT_POLICY
    )
    assert [row["strike"] for row in first] == [row["strike"] for row in second], (first, second)
    assert first[0]["strike"] == 105.0, "the later expiry breaks a distance tie"

    # Same expiry, same distance -> the richer premium wins.
    same_day = [
        contract(103.0, expiration="2026-08-24", bid=0.50, ask=0.52),
        contract(105.0, expiration="2026-08-24", bid=1.00, ask=1.04),
    ]
    ranked, _ = rank_candidates(
        same_day, strategy="COVERED_CALL", spot=SPOT, today=TODAY, policy=option_strategy.DEFAULT_POLICY
    )
    assert ranked[0]["strike"] == 105.0, ranked


def sizing_walks_the_ranking() -> None:
    # 10,000 in cash cannot secure the 96 strike (9,600 is fine for one, so make it
    # tighter): with 9,500 the best-ranked strike is unaffordable and the rule must
    # step down the ranking rather than give up or overspend.
    result = select_cash_secured_put("TEST", cash_available=9_500.0, spot=SPOT, chain=PUT_CHAIN, as_of=TODAY)
    assert result["status"] == "SELECTED", result
    assert result["option_contract"]["strike"] == 95.0, result
    assert result["contracts"] == 1, result
    assert result["cash_required"] == 9_500.0, result
    assert result["skipped_for_size"] == 1, result

    # max_contracts is a ceiling, never a floor.
    capped = select_covered_call(
        "TEST", shares_held=1_000, spot=SPOT, chain=CALL_CHAIN, as_of=TODAY, max_contracts=3
    )
    assert capped["contracts"] == 3, capped


def refuses_rather_than_guesses() -> None:
    # Not enough shares for even one contract: no strike is proposed at all.
    thin = select_covered_call("TEST", shares_held=99, spot=SPOT, chain=CALL_CHAIN, as_of=TODAY)
    assert thin["status"] == "INSUFFICIENT_SHARES", thin
    assert "option_contract" not in thin, thin
    assert "99 shares held" in thin["reason"], thin

    # Cash that cannot secure the cheapest eligible put: a selection is not returned
    # with a smaller size, because there is no smaller size than one contract.
    broke = select_cash_secured_put("TEST", cash_available=500.0, spot=SPOT, chain=PUT_CHAIN, as_of=TODAY)
    assert broke["status"] == "INSUFFICIENT_CASH", broke
    assert "option_contract" not in broke, broke
    assert "cheapest" in broke["reason"], broke

    assert select_cash_secured_put("TEST", cash_available=0, spot=SPOT, chain=PUT_CHAIN, as_of=TODAY)["status"] == "INSUFFICIENT_CASH"

    # No spot means no moneyness, and moneyness is the whole rule.
    for bad_spot in (None, 0, -5):
        blind = select_covered_call("TEST", shares_held=100, spot=bad_spot, chain=CALL_CHAIN, as_of=TODAY)
        assert blind["status"] == "INVALID_INPUT", blind

    # An empty chain says so, rather than reporting a spurious rejection reason.
    empty = select_covered_call("TEST", shares_held=100, spot=SPOT, chain=[], as_of=TODAY)
    assert empty["status"] == "NO_CANDIDATE", empty
    assert empty["reason"] == "the option chain was empty", empty

    # A chain of nothing but in-the-money strikes names the band as the blocker.
    itm = select_covered_call(
        "TEST", shares_held=100, spot=SPOT, chain=[contract(90.0), contract(95.0)], as_of=TODAY
    )
    assert itm["status"] == "NO_CANDIDATE", itm
    assert "OTM band" in itm["reason"] and "2-7%" in itm["reason"], itm


def chain_failure_is_not_a_selection() -> None:
    """A broker/data outage must surface as an outage, never as 'nothing qualified'."""
    original = option_strategy._load_chain
    try:
        option_strategy._load_chain = lambda root, option_type, window: (None, "option_chain_unavailable:http_403")
        down = select_covered_call("TEST", shares_held=100, spot=SPOT, as_of=TODAY)
        assert down["status"] == "CHAIN_UNAVAILABLE", down
        assert "403" in down["reason"], down
        assert "option_contract" not in down, down

        # And the fetch has to be scoped to the DTE window, not the whole chain.
        seen = {}

        def record(root, option_type, window):
            seen.update({"root": root, "option_type": option_type, "window": window})
            return CALL_CHAIN, "alpaca"

        option_strategy._load_chain = record
        fetched = select_covered_call("test", shares_held=100, spot=SPOT, as_of=TODAY)
        assert fetched["status"] == "SELECTED", fetched
        assert seen["root"] == "TEST", seen
        assert seen["option_type"] == "CALL", seen
        assert seen["window"] == (date(2026, 8, 20), date(2026, 8, 26)), seen
    finally:
        option_strategy._load_chain = original


def feeds_the_gate_chain() -> None:
    """The selection is a proposal: it must be gate-legible, and it must still be gated.

    build_shariah_candidate is the one entry point a proposal reaches the gates
    through, so what option_strategy emits has to drop into it unmodified.
    """
    call = select_covered_call("TEST", shares_held=100, spot=SPOT, chain=CALL_CHAIN, as_of=TODAY)
    candidate = build_shariah_candidate(
        symbol=call["underlying"],
        side=call["side"],
        signal=call["side"],
        quantity=call["contracts"],
        price=call["price"],
        account_type="CASH",
        option_contract=call["option_contract"],
        shares_held=call["shares_held"],
        shariah_override=SHARIAH_PASS,
    )
    assert candidate["option_structure"]["structure"] == "covered_call", candidate
    assert candidate["option_structure"]["contracts"] == call["contracts"], candidate

    # The determination in force refuses every option contract, so a perfectly selected,
    # fully covered call is refused here -- and refused as impermissible, not as badly
    # sized. Choosing is not approving, and now approving is not permitted either.
    blocked = approve_candidate(candidate, approved_by_user=True)
    assert blocked["status"] == "REJECT", blocked
    assert blocked["option_structure"]["reason"] == REASON_NOT_PERMITTED, blocked

    # Under a permissive determination the same proposal clears the chain, which is what
    # keeps this path exercised rather than rotting behind the block.
    candidate["option_structure"]["determination"] = PERMITTED_DETERMINATION
    approved = approve_candidate(candidate, approved_by_user=True)
    assert approved["status"] == "APPROVED_PAPER_READY", approved

    put = select_cash_secured_put("TEST", cash_available=20_000.0, spot=SPOT, chain=PUT_CHAIN, as_of=TODAY)
    secured = build_shariah_candidate(
        symbol=put["underlying"],
        side=put["side"],
        signal=put["side"],
        quantity=put["contracts"],
        price=put["price"],
        account_type="CASH",
        option_contract=put["option_contract"],
        cash_collateral=put["cash_available"],
        shariah_override=SHARIAH_PASS,
    )
    assert approve_candidate(secured, approved_by_user=True)["status"] == "REJECT"
    secured["option_structure"]["determination"] = PERMITTED_DETERMINATION
    assert approve_candidate(secured, approved_by_user=True)["status"] == "APPROVED_PAPER_READY"

    # Selection does not launder a non-compliant underlying: a perfectly chosen strike
    # on a rejected company is still rejected. Choosing is not approving.
    tainted = build_shariah_candidate(
        symbol=call["underlying"],
        side=call["side"],
        signal=call["side"],
        quantity=call["contracts"],
        price=call["price"],
        account_type="CASH",
        option_contract=call["option_contract"],
        shares_held=call["shares_held"],
        shariah_override={"agent": "shariah", "status": "REJECT", "provider": "SEC_EDGAR", "reason": "NON_COMPLIANT"},
    )
    assert approve_candidate(tainted, approved_by_user=True)["status"] == "REJECT"

    # Nor does it launder a margin account.
    on_margin = build_shariah_candidate(
        symbol=call["underlying"],
        side=call["side"],
        signal=call["side"],
        quantity=call["contracts"],
        price=call["price"],
        account_type="MARGIN",
        option_contract=call["option_contract"],
        shares_held=call["shares_held"],
        uses_margin=True,
        shariah_override=SHARIAH_PASS,
    )
    assert approve_candidate(on_margin, approved_by_user=True)["status"] == "REJECT"

    # The OCC symbol the adapter will build must match the one selection narrated,
    # or the rationale is describing a different contract than the one submitted.
    from alpaca_paper_adapter import build_option_occ_symbol

    rebuilt = build_option_occ_symbol(
        call["option_contract"]["underlying"],
        call["option_contract"]["expiration"],
        call["option_contract"]["option_type"],
        call["option_contract"]["strike"],
    )
    assert rebuilt == call["option_contract"]["occ_symbol"], (rebuilt, call["option_contract"])
    assert rebuilt in call["rationale"], call["rationale"]


if __name__ == "__main__":
    main()
