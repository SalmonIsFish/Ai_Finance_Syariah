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


@pytest.mark.parametrize("adapter", ["alpaca", "alpaca_mcp", "moomoo"])
@pytest.mark.parametrize("price", NON_FINITE, ids=NON_FINITE_IDS)
def test_neither_adapter_submits_a_non_finite_price(price, adapter, monkeypatch):
    """`price <= 0` misses NaN, and the order body then carried limit_price "nan".

    The refusal must come from the adapter's own price check, before any broker contact.
    An earlier version of this test asserted only `broker_submission is False`, which was
    satisfied for the wrong reasons: with paper keys in .env the Alpaca half really POSTed
    limit_price "nan" to paper-api and passed because Alpaca refused it, and the Moomoo half
    passed because no OpenD was running -- then hung the moment one was. So every broker
    seam is replaced by a recorder, and the refusal is asserted by name.

    The Moomoo seam is recorded rather than made to raise: the adapter wraps
    load_moomoo_sdk() in `except Exception`, which would turn a raising stub into
    SDK_UNAVAILABLE and let a broken guard pass again.
    """
    import alpaca_paper_adapter
    import moomoo_paper_adapter

    contacted = []

    def record(name):
        def seam(*args, **kwargs):
            contacted.append(name)
            raise RuntimeError(f"{name} reached")

        return seam

    monkeypatch.setattr(alpaca_paper_adapter, "alpaca_request", record("alpaca_request"))
    monkeypatch.setattr(
        alpaca_paper_adapter, "load_alpaca_mcp_client", record("load_alpaca_mcp_client")
    )
    monkeypatch.setattr(moomoo_paper_adapter, "load_moomoo_sdk", record("load_moomoo_sdk"))

    approval = {
        "id": 1,
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 1,
        "price": price,
        "shariah_market": "US",
        "payload": "{}",
    }
    module = moomoo_paper_adapter if adapter == "moomoo" else alpaca_paper_adapter
    result = module.submit_paper_order(approval, {}, adapter)

    assert contacted == [], (adapter, contacted, result)
    assert result["status"] == "INVALID_PRICE", result
    assert result["broker_submission"] is False, result


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


# --- the P3 sizing engine ------------------------------------------------------------
#
# pure_risk sizes by min(cash, position, total, sector, per_trade_loss). `min` keeps its
# running best unless a later value compares smaller, so a NaN anywhere after the first
# argument is silently dropped -- and `max(0.0, +inf)` passes an infinite cap straight
# through. Before the guard, loss_per_unit=nan sized 10x over the per-trade loss cap and
# equity=inf sized to all available cash with every percentage cap gone. The inputs that
# did fail closed did so by accident of argument order.
#
# Imported from `backend.pure_risk`, the module object p3_decision_engine uses, so the
# exception class caught there is the one raised here.

SIZING_FIELDS = [
    "price",
    "position",
    "cash",
    "equity",
    "total_exposure",
    "sector_exposure",
    "max_position_pct",
    "max_total_exposure_pct",
    "max_sector_exposure_pct",
    "max_loss_per_trade_pct",
    "loss_per_unit",
]


def _sizing(side="BUY", **overrides):
    """Baseline: 100k book, price 10, caps 5/25/20, 0.5% loss cap -> 50 shares."""
    from backend.pure_risk import RiskPolicy, RiskState, calculate_target_quantity

    v = {
        "price": 10.0,
        "position": 0.0,
        "cash": 100_000.0,
        "equity": 100_000.0,
        "total_exposure": 0.0,
        "sector_exposure": 0.0,
        "max_position_pct": 5.0,
        "max_total_exposure_pct": 25.0,
        "max_sector_exposure_pct": 20.0,
        "max_loss_per_trade_pct": 0.5,
        "loss_per_unit": 10.0,
    }
    unknown = set(overrides) - set(v)
    assert not unknown, f"_sizing does not wire {unknown}"
    v.update(overrides)

    state = RiskState(
        cash=v["cash"],
        equity=v["equity"],
        positions={"AAPL": v["position"]},
        sector_exposure={"Tech": v["sector_exposure"]},
        total_exposure=v["total_exposure"],
    )
    policy = RiskPolicy(
        max_position_pct=v["max_position_pct"],
        max_total_exposure_pct=v["max_total_exposure_pct"],
        max_sector_exposure_pct=v["max_sector_exposure_pct"],
        max_loss_per_trade_pct=v["max_loss_per_trade_pct"],
    )
    return calculate_target_quantity(
        state, "AAPL", v["price"], "Tech", policy, side, loss_per_unit=v["loss_per_unit"]
    )


def test_the_sizing_fixture_reaches_the_real_arithmetic():
    """Not vacuous: the baseline binds on the loss cap, and on the position cap without it."""
    assert _sizing() == 50.0
    assert _sizing(max_loss_per_trade_pct=None) == 500.0


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
@pytest.mark.parametrize("field", SIZING_FIELDS)
def test_a_non_finite_sizing_input_is_refused_and_named(field, value):
    """Refused rather than sized -- and the refusal names the field, which also proves the
    override reached the value the guard reads instead of being dropped by the fixture."""
    from backend.pure_risk import NonFiniteRiskInputError

    with pytest.raises(NonFiniteRiskInputError) as excinfo:
        _sizing(**{field: value})
    assert excinfo.value.field == field


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
def test_a_sell_never_returns_a_non_finite_quantity(value):
    """SELL returns the held quantity as-is, so a NaN holding became a NaN order size."""
    from backend.pure_risk import NonFiniteRiskInputError

    with pytest.raises(NonFiniteRiskInputError) as excinfo:
        _sizing(side="SELL", position=value)
    assert excinfo.value.field == "position"


# --- the P3 book: prices in, cash out ------------------------------------------------
#
# p3_decision_engine reads a close for the candidate and for every held position, and
# execute_order writes current_cash back. Measured on 2026-09-29 with the sizing guard
# above in place: every non-finite close was already refused before any write. What was
# left open was the book itself -- a SELL on a book whose stored cash was already inf
# executed and wrote inf back, because nothing checked the book's cash on the SELL side
# and nothing checked the write -- and refusals that named a field ("equity") instead of
# the ticker whose price could not be used.
#
# NaN cannot be stored at all: current_cash is REAL NOT NULL and SQLite stores NaN as
# NULL, so that write raises. +/-inf is stored as-is, which is why the cases below use it.


def _p3_book(monkeypatch, *, cash=100_000.0, hold=None):
    """An in-memory P3 book, and a mutable {ticker: close} the engine prices from."""
    import sqlite3

    from backend import p3_decision_engine, p3_portfolio_engine

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    p3_portfolio_engine.ensure_p3_tables(conn)
    portfolio_id = p3_portfolio_engine.create_portfolio(conn, "Test", cash)["id"]
    if hold:
        conn.execute(
            """
            INSERT INTO p3_portfolio_positions (portfolio_id, ticker, quantity, average_cost,
                market_value, unrealized_pnl, realized_pnl, sector, updated_at)
            VALUES (?, ?, ?, 100.0, 0.0, 0.0, 0.0, 'Tech', '2024-01-01T00:00:00Z')
            """,
            (portfolio_id, hold[0], hold[1]),
        )
        conn.commit()

    closes = {"AAPL": 150.0, "MSFT": 200.0}

    def fetch(ticker, start_date, end_date, allow_fallback, allow_stale_cache):
        return [{"close": closes[ticker]}], "test"

    monkeypatch.setattr(
        p3_decision_engine,
        "screen_ticker",
        lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}},
    )
    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", fetch)
    monkeypatch.setattr(
        p3_decision_engine,
        "risk_limits",
        lambda: {
            "limits": {
                "max_loss_per_trade_pct": 0.5,
                "max_total_exposure_pct": 100.0,
                "max_position_pct": 50.0,
                "max_sector_exposure_pct": 100.0,
            }
        },
    )
    return p3_decision_engine, conn, portfolio_id, closes


def _cash_and_fills(conn, portfolio_id):
    cash = conn.execute(
        "SELECT current_cash FROM p3_portfolios WHERE id = ?", (portfolio_id,)
    ).fetchone()[0]
    fills = conn.execute("SELECT COUNT(*) FROM p3_portfolio_fills").fetchone()[0]
    return cash, fills


@pytest.mark.parametrize("close", NON_FINITE, ids=NON_FINITE_IDS)
@pytest.mark.parametrize(
    "unusable, hold, reason",
    [
        ("AAPL", None, "market_data_unavailable"),
        ("MSFT", ("MSFT", 100), "portfolio_valuation_unavailable"),
    ],
    ids=["candidate", "held"],
)
def test_p3_names_the_ticker_whose_close_cannot_be_used(close, unusable, hold, reason, monkeypatch):
    """Refused at the price source, under the reason a missing price already gets, and
    naming the ticker. The sizing guard would refuse it too -- as "equity", which is true
    and not something an operator can act on."""
    engine, conn, portfolio_id, closes = _p3_book(monkeypatch, hold=hold)
    closes[unusable] = close

    result = engine.authoritative_revalidation(
        conn, portfolio_id, "AAPL", "BUY", requested_price=150.0
    )
    assert result["status"] == "BLOCKED", result
    assert result["reason"] == reason, result
    assert result["details"]["ticker"] == unusable, result
    assert result["details"]["cause"] == "non_finite_close", result


@pytest.mark.parametrize("close", NON_FINITE, ids=NON_FINITE_IDS)
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_p3_execution_never_moves_cash_on_a_close_that_went_bad(close, side, monkeypatch):
    """Proposed and approved on good prices; the close goes non-finite before execution.
    Nothing may be written -- not cash, not a fill."""
    engine, conn, portfolio_id, closes = _p3_book(monkeypatch, hold=("AAPL", 10))
    order = engine.propose_order(conn, portfolio_id, "AAPL", side)
    engine.approve_order(conn, order["id"], "tester")
    before = _cash_and_fills(conn, portfolio_id)

    closes["AAPL"] = close
    with pytest.raises(ValueError):
        engine.execute_order(conn, order["id"], "tester")
    assert _cash_and_fills(conn, portfolio_id) == before


@pytest.mark.parametrize("stored", [float("inf"), float("-inf")], ids=["inf", "-inf"])
@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_p3_refuses_a_book_whose_cash_cannot_be_used(stored, side, monkeypatch):
    """Garbage already in the book. Before this guard a SELL executed against cash=inf and
    wrote inf back -- pure_risk checks cash only when sizing a BUY -- and -inf was refused
    only by accident, as "Insufficient cash"."""
    engine, conn, portfolio_id, _ = _p3_book(monkeypatch, hold=("AAPL", 10))
    conn.execute("UPDATE p3_portfolios SET current_cash = ? WHERE id = ?", (stored, portfolio_id))
    conn.commit()

    result = engine.authoritative_revalidation(
        conn, portfolio_id, "AAPL", side, requested_price=150.0
    )
    assert result["status"] == "BLOCKED", result
    assert result["reason"] == "portfolio_cash_unusable", result


@pytest.mark.parametrize("value", NON_FINITE, ids=NON_FINITE_IDS)
@pytest.mark.parametrize("field", ["price", "quantity"])
def test_execute_order_never_persists_a_non_finite_value(field, value, monkeypatch):
    """The last line: even if revalidation ever let a non-finite value through, the write
    refuses it by name. Today NaN would be stopped only because the NOT NULL column rejects
    it, and inf would reach the book -- or, for a BUY, be misreported as insufficient cash."""
    engine, conn, portfolio_id, _ = _p3_book(monkeypatch)
    order = engine.propose_order(conn, portfolio_id, "AAPL", "BUY")
    engine.approve_order(conn, order["id"], "tester")
    before = _cash_and_fills(conn, portfolio_id)

    real = engine.authoritative_revalidation

    def lets_it_through(*args, **kwargs):
        verdict = real(*args, **kwargs)
        assert verdict["status"] == "PASS", verdict
        return {**verdict, field: value}

    monkeypatch.setattr(engine, "authoritative_revalidation", lets_it_through)
    with pytest.raises(ValueError, match="non_finite_execution_value"):
        engine.execute_order(conn, order["id"], "tester")
    assert _cash_and_fills(conn, portfolio_id) == before
