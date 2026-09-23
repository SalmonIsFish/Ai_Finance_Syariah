"""No gate may pass a value it could not compute.

Python makes the opposite the default. Every comparison against `NaN` is `False`, so a
threshold check written the obvious way inverts under missing data:

    nan <= 0      -> False    a price-sanity gate does not fire
    nan >= 33.0   -> False    a Shariah ratio screens COMPLIANT
    nan > limit   -> False    a position limit passes

This is not hypothetical. On 2026-09-24 Yahoo returned no price for a Bursa symbol, a
position worth 5.40% of equity was APPROVED against a 5% cap, and a sweep for the same
shape found twelve more sites -- two of them on the Shariah verdict and the price gate.

**This file is the invariant, not a list of those twelve bugs.** One rule, parameterised
across every gate entry point, in the spirit of `test_single_screening_path.py`: a static
convention only catches what someone remembered, while this catches a new gate the moment
it is written without a guard.

Three variants recur, and each has its own section below:

* `is None` guards that miss `NaN`;
* `float(x or 0)`, where `NaN` is truthy so the default never applies;
* `max(0.0, nan)`, which returns `0.0` -- flattening a fail-closed signal into a passing
  value *before* the gate sees it, which is worse than propagating it.
"""

import math

import pytest
from numeric_guards import finite_or, first_non_finite, is_finite_number

NON_FINITE = [float("nan"), float("inf"), float("-inf")]
NON_FINITE_IDS = ["nan", "inf", "-inf"]


# --- the shared guard itself ---------------------------------------------------------


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_the_guard_rejects_every_non_finite_value(value):
    assert is_finite_number(value) is False
    assert finite_or(value, "fallback") == "fallback"


@pytest.mark.parametrize("value", [0, 0.0, -1, 1.5, 1e300])
def test_the_guard_accepts_real_numbers_including_zero(value):
    """A legitimate 0.0 must not be mistaken for missing -- that is the `or 0` bug."""
    assert is_finite_number(value) is True
    assert finite_or(value, "fallback") == value


@pytest.mark.parametrize("value", [None, True, False, "1.0", "nan", [], {}])
def test_the_guard_rejects_non_numbers(value):
    """bool is excluded deliberately: True silently behaving as 1 in a price is its own bug.
    Strings are rejected rather than coerced -- a gate is not where "nan" becomes a number.
    """
    assert is_finite_number(value) is False


def test_first_non_finite_names_the_field():
    assert first_non_finite(a=1.0, b=float("nan"), c=2.0) == "b"
    assert first_non_finite(a=1.0, b=2.0) is None


# --- the price-sanity gate -----------------------------------------------------------


@pytest.mark.parametrize("price", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_non_finite_price_is_blocked(price):
    """The reproduction that started this: READY_FOR_APPROVAL with no blockers at all.

    This is the ONLY price guard on the option path -- the equity portfolio overlay is
    skipped for asset_class == "option" by design (CLAUDE.md limitation 4).
    """
    from agent_coordinator import evaluate_candidate

    result = evaluate_candidate(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        price=price,
        position_pct=0.0,
        total_exposure_pct=0.0,
        loss_per_trade_pct=0.0,
        daily_loss_pct=0.0,
        orders_today=0,
        shariah_override={"agent": "shariah", "status": "PASS", "reason": "test"},
        quant_override={
            "agent": "quant",
            "signal": "BUY",
            "price": price,
            "data_freshness": "live",
        },
        record_evidence=False,
    )
    assert result["decision"] == "BLOCKED", result
    assert "valid_price_required" in result["blockers"], result
    # NaN is truthy, so notional used to become NaN rather than None.
    assert result["notional"] is None, result


# --- the Shariah verdict -------------------------------------------------------------


def _gaap(total_assets, cash=100.0):
    """A balance sheet in the shape compute_ratios actually takes.

    It takes the `gaap` mapping directly, NOT a {"us-gaap": ...} wrapper. A first draft of
    this file wrapped it, so every lookup missed, every call returned
    "total_assets_not_reported_in_any_filing", and the test passed for a finite value too
    -- a vacuous test that would never have caught the bug it was written for. Caught by
    the deliberate-break check, which is the entire reason for running one.
    """
    row = {"form": "10-K", "end": "2025-12-31", "filed": "2026-02-01"}
    return {
        "Assets": {"units": {"USD": [dict(row, val=total_assets)]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [dict(row, val=cash)]}},
    }


def test_the_balance_sheet_fixture_actually_screens():
    """Guards the guard: a finite balance sheet must produce real ratios.

    Without this, every assertion below could pass because the fixture is malformed
    rather than because the code is correct.
    """
    from sec_edgar_screen import compute_ratios

    result = compute_ratios(_gaap(1000.0))
    assert isinstance(result, dict), result
    assert result["cash_ratio_pct"] == 10.0, result


@pytest.mark.parametrize("total_assets", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_non_finite_balance_sheet_never_screens_compliant(total_assets):
    """The worst of the twelve: `nan <= 0` is False, so both ratios compared False
    against the 33% limit and the screen returned COMPLIANT -- with a reason reading
    "debt nan% and cash nan% of total assets, both under 33%"."""
    from sec_edgar_screen import compute_ratios

    result = compute_ratios(_gaap(total_assets))
    # A string is this module's own "cannot screen" signal, which the caller turns into
    # UNKNOWN. A dict here would mean the ratios reached the comparison.
    assert isinstance(result, str), result


@pytest.mark.parametrize("cash", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_non_finite_cash_line_never_screens_compliant(cash):
    """The numerator matters as much as the denominator."""
    from sec_edgar_screen import compute_ratios

    result = compute_ratios(_gaap(1000.0, cash=cash))
    assert isinstance(result, str), result


# --- the sector concentration limit --------------------------------------------------


def _sector(**overrides):
    from sector_concentration import check_sector_concentration

    kwargs = {
        "symbol": "XOM",
        "added_exposure": 100.0,
        "positions": [],
        "sectors": {},
        "account_equity": 10_000.0,
        "max_sector_pct": 10.0,
    }
    kwargs.update(overrides)
    return check_sector_concentration(**kwargs)


@pytest.mark.parametrize("added", NON_FINITE, ids=NON_FINITE_IDS)
def test_an_unvaluable_order_does_not_pass_the_sector_limit(added):
    """`max(0.0, nan)` returned 0.0, so the order contributed NOTHING to its sector."""
    result = _sector(added_exposure=added)
    assert result["status"] == "REJECT", result


@pytest.mark.parametrize(
    "equity", NON_FINITE + [0.0, -1.0, None], ids=NON_FINITE_IDS + ["zero", "negative", "none"]
)
def test_unusable_equity_fails_closed(equity):
    """Used to PASS. `_period_loss_pct` returns inf for the same condition to fail CLOSED."""
    result = _sector(account_equity=equity)
    assert result["status"] == "REJECT", result


@pytest.mark.parametrize("cap", [float("nan"), "ten percent", object()])
def test_a_malformed_sector_limit_does_not_disable_the_gate(cap):
    result = _sector(max_sector_pct=cap)
    assert result["status"] == "REJECT", result


def test_every_sector_rejection_carries_a_message():
    """local_api reads sector_result["message"] directly, so a REJECT without one raises."""
    for result in (
        _sector(added_exposure=float("nan")),
        _sector(account_equity=0.0),
        _sector(max_sector_pct="ten"),
    ):
        assert result["status"] == "REJECT"
        assert result.get("message"), result


# --- the risk engine -----------------------------------------------------------------


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
@pytest.mark.parametrize(
    "field",
    ["position_pct", "total_exposure_pct", "loss_per_trade_pct", "daily_loss_pct"],
)
def test_a_non_finite_risk_input_is_rejected(field, value):
    from risk_checks import check_order

    kwargs = {
        "position_pct": 1.0,
        "total_exposure_pct": 1.0,
        "loss_per_trade_pct": 0.1,
        "daily_loss_pct": 0.1,
        "orders_today": 0,
    }
    kwargs[field] = value
    assert check_order(**kwargs)["status"] == "REJECT", (field, value)


# --- the option structure gate -------------------------------------------------------


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_non_finite_collateral_never_clears_the_structure_gate(value):
    """Blocked twice over today -- by the option determination and by the arithmetic."""
    from option_permissibility import OPTION_DETERMINATION, OPTION_POLICY_PERMITTED
    from option_structure_gate import check_structure

    permitted = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)
    result = check_structure(
        structure="cash_secured_put",
        cash_collateral=value,
        strike=100.0,
        contracts=1,
        determination=permitted,
    )
    assert result["status"] == "REJECT", result


# --- the broker adapters, the last guard before submission ---------------------------


@pytest.mark.parametrize("price", NON_FINITE, ids=NON_FINITE_IDS)
def test_neither_adapter_submits_a_non_finite_price(price):
    """`price <= 0` misses NaN, and the order body then carried limit_price "nan".

    Alpaca would 422 it, so the refusal came from the broker rather than from the gate
    chain -- the inverse of this architecture.
    """
    import alpaca_paper_adapter
    import moomoo_paper_adapter

    approval = {
        "id": 1,
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 1,
        "price": price,
        "shariah_market": "US",
        "payload": "{}",
    }
    alpaca = alpaca_paper_adapter.submit_paper_order(approval, {}, "alpaca")
    assert alpaca["broker_submission"] is False, alpaca

    moomoo = moomoo_paper_adapter.submit_paper_order(approval, {}, "moomoo")
    assert moomoo["broker_submission"] is False, moomoo


# --- the exposure valuation ----------------------------------------------------------


@pytest.mark.parametrize("market_value", NON_FINITE, ids=NON_FINITE_IDS)
def test_an_unvaluable_position_falls_back_to_cost_basis(market_value):
    """The original bug: `is None` missed NaN, so the cost_basis fallback never ran."""
    from local_api import exposure_value

    value = exposure_value({"market_value": market_value, "cost_basis": 490.0})
    assert value == 490.0

    # And with no cost basis either, a finite zero rather than a NaN.
    orphan = exposure_value({"market_value": market_value, "cost_basis": None})
    assert math.isfinite(orphan)


# --- configuration -------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "NaN", "Infinity"])
def test_a_non_finite_risk_limit_is_refused_at_startup(raw, monkeypatch):
    """`float("inf")` parses. MAX_POSITION_PCT=inf disables the position, total-exposure,
    sector and loss limits at once, with no startup error."""
    import config

    monkeypatch.setenv("MAX_POSITION_PCT", raw)
    with pytest.raises(ValueError):
        config.load_settings()
