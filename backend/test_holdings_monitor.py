"""Tests for the Shariah monitor: reported holdings, CSV import, colours, sales, alerts.

No test reaches the network: every run swaps the `evaluate` seam for canned
evaluate_shariah-shaped verdicts.
"""

import sqlite3

import pytest

import holdings_compliance
import holdings_monitor
import portfolio_store
from holdings_monitor import HoldingError


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    holdings_monitor.ensure_monitor_tables(conn)
    return conn


def _pass(debt=10.0, report_date="2025-12-31"):
    return {
        "status": "PASS",
        "details": {
            "status": "COMPLIANT",
            "screen": "financial_ratios",
            "report_date": report_date,
            "ratios": {
                "debt_ratio_pct": debt,
                "cash_ratio_pct": 5.0,
                "limit_pct": 33.0,
                "debt_concepts_used": ["LongTermDebtNoncurrent"],
            },
        },
    }


REJECT = {
    "status": "REJECT",
    "details": {
        "status": "NON_COMPLIANT",
        "screen": "financial_ratios",
        "report_date": "2025-12-31",
    },
}
MIXED = {
    "status": "REJECT",
    "details": {
        "status": "NON_COMPLIANT",
        "screen": "business_activity",
        "exclusion_category": "hospitality_mixed_activity",
    },
}


def _evaluate(verdicts):
    def evaluate(symbol):
        return {"market": "US", "provider": "STUB", "symbol": symbol, **verdicts[symbol]}

    return evaluate


def _run(conn, verdicts, price=None):
    return holdings_monitor.run_monitor(
        conn,
        evaluate=_evaluate(verdicts),
        price_lookup=(lambda symbol: price) if price is not None else None,
    )


def _light(result, symbol):
    return next(h for h in result["holdings"] if h["symbol"] == symbol)["traffic_light"]["light"]


# --- entering holdings -------------------------------------------------------


def test_manual_holding_is_validated():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol=" us.aapl ", quantity="10", average_cost="$150.5")
    [row] = holdings_monitor.list_holdings(conn)
    assert (row["symbol"], row["quantity"], row["cost_basis"]) == ("AAPL", 10.0, 1505.0)
    for bad in (
        {"symbol": "AAPL", "quantity": 0, "average_cost": 1},
        {"symbol": "AAPL", "quantity": 1, "average_cost": -1},
        {"symbol": "AAPL", "quantity": "nan", "average_cost": 1},
        {"symbol": "HK.00700", "quantity": 1, "average_cost": 1},
        {"symbol": "not a ticker!", "quantity": 1, "average_cost": 1},
    ):
        with pytest.raises(HoldingError):
            holdings_monitor.upsert_holding(conn, **bad)


def test_csv_matches_common_broker_column_names():
    text = '﻿Code,Name,Qty,Diluted Cost,Market Value\nUS.AAPL,Apple,10,150.00,1800\nMSFT,Microsoft,"1,000",300,0\n'
    parsed = holdings_monitor.parse_holdings_csv(text)
    assert parsed["status"] == "OK"
    assert parsed["rows"] == [
        {"symbol": "AAPL", "quantity": 10.0, "average_cost": 150.0},
        {"symbol": "MSFT", "quantity": 1000.0, "average_cost": 300.0},
    ]


def test_csv_falls_back_to_cost_basis_and_skips_closed_rows():
    text = "Ticker,Shares,Total Cost\nNVDA,4,400\nTSLA,0,0\n"
    parsed = holdings_monitor.parse_holdings_csv(text)
    assert parsed["rows"] == [{"symbol": "NVDA", "quantity": 4.0, "average_cost": 100.0}]


def test_csv_is_all_or_nothing():
    for text, fragment in (
        ("Symbol,Quantity\nAAPL,1\n", "average_cost"),
        ("Symbol,Quantity,Avg Price\nAAPL,1,10\nHK.00700,1,10\n", "not supported"),
        ("Symbol,Quantity,Avg Price\nAAPL,1,10\nAAPL,2,10\n", "more than once"),
        ("Symbol,Quantity,Avg Price\nAAPL,abc,10\n", "not a number"),
    ):
        parsed = holdings_monitor.parse_holdings_csv(text)
        assert parsed["status"] == "REJECTED"
        assert parsed["rows"] == []
        assert fragment in " ".join(parsed["errors"])


def test_csv_import_replaces_only_that_broker():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="AAPL", quantity=1, average_cost=1, broker="ibkr")
    holdings_monitor.upsert_holding(conn, symbol="OLD", quantity=1, average_cost=1, broker="moomoo")
    result = holdings_monitor.import_holdings_csv(
        conn, "Symbol,Quantity,Avg Cost\nMSFT,2,300\n", broker="moomoo"
    )
    assert result["imported"] == 1
    held = {(h["symbol"], h["broker"]) for h in holdings_monitor.list_holdings(conn)}
    assert held == {("AAPL", "ibkr"), ("MSFT", "moomoo")}
    # A rejected file changes nothing.
    holdings_monitor.import_holdings_csv(conn, "Symbol,Quantity\nX,1\n", broker="moomoo")
    assert ("MSFT", "moomoo") in {
        (h["symbol"], h["broker"]) for h in holdings_monitor.list_holdings(conn)
    }


# --- colours, clocks, alerts -------------------------------------------------


def test_run_colours_holdings_and_attaches_advice():
    conn = _conn()
    for symbol in ("GOOD", "NEAR", "BAD", "HOTEL"):
        holdings_monitor.upsert_holding(conn, symbol=symbol, quantity=10, average_cost=10)
    result = _run(conn, {"GOOD": _pass(), "NEAR": _pass(debt=31), "BAD": REJECT, "HOTEL": MIXED})
    lights = {h["symbol"]: h["traffic_light"]["light"] for h in result["holdings"]}
    assert lights == {"GOOD": "GREEN", "NEAR": "YELLOW", "BAD": "RED", "HOTEL": "YELLOW"}
    assert result["counts"] == {"GREEN": 1, "YELLOW": 2, "RED": 1}
    advice = {h["symbol"]: h["advice"] for h in result["holdings"]}
    assert "Do not buy more or sell" in advice["NEAR"]


def test_disposal_clock_runs_for_red_only():
    conn = _conn()
    for symbol in ("BAD", "HOTEL"):
        holdings_monitor.upsert_holding(conn, symbol=symbol, quantity=10, average_cost=10)
    result = _run(conn, {"BAD": REJECT, "HOTEL": MIXED}, price=15.0)
    by_symbol = {h["symbol"]: h for h in result["holdings"]}
    assert by_symbol["BAD"]["disposal_deadline"] is not None
    assert by_symbol["HOTEL"]["disposal_deadline"] is None
    # Purification estimate covers the RED holding only: 10 * (15 - 10).
    assert result["purification"]["total_purification_due"] == 50.0


def test_monitor_and_paper_clocks_never_clear_each_other():
    conn = _conn()
    conn.execute(
        "INSERT INTO paper_positions (symbol, account_suffix, account_type, quantity, "
        "average_cost, cost_basis, realized_pnl, updated_at) "
        "VALUES ('PAPERBAD', 'TEST', 'CASH', 1, 1, 1, 0, '2026-10-01T00:00:00+00:00')"
    )
    conn.commit()
    paper = holdings_compliance.screen_holdings(conn, evaluate=_evaluate({"PAPERBAD": REJECT}))
    paper = holdings_compliance.apply_disposal_clock(conn, paper)
    first = paper["flagged"][0]["first_flagged_at"]

    holdings_monitor.upsert_holding(conn, symbol="BAD", quantity=1, average_cost=1)
    _run(conn, {"BAD": REJECT})
    open_rows = conn.execute(
        "SELECT symbol FROM holdings_compliance_flags WHERE cleared_at IS NULL"
    ).fetchall()
    assert {row[0] for row in open_rows} == {"PAPERBAD", "BAD"}

    again = holdings_compliance.apply_disposal_clock(
        conn, holdings_compliance.screen_holdings(conn, evaluate=_evaluate({"PAPERBAD": REJECT}))
    )
    assert again["flagged"][0]["first_flagged_at"] == first
    still_open = conn.execute(
        "SELECT symbol FROM holdings_compliance_flags WHERE cleared_at IS NULL"
    ).fetchall()
    assert {row[0] for row in still_open} == {"PAPERBAD", "BAD"}


def test_alerts_fire_on_change_only():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="AAA", quantity=1, average_cost=1)
    holdings_monitor.upsert_holding(conn, symbol="BBB", quantity=1, average_cost=1)
    first = _run(conn, {"AAA": _pass(), "BBB": _pass(debt=31)})
    # A first sighting alerts only if it is not GREEN.
    assert [(t["symbol"], t["from"], t["to"]) for t in first["transitions"]] == [
        ("BBB", None, "YELLOW")
    ]
    assert _run(conn, {"AAA": _pass(), "BBB": _pass(debt=31)})["transitions"] == []
    third = _run(conn, {"AAA": REJECT, "BBB": _pass(debt=31)})
    assert [(t["symbol"], t["from"], t["to"]) for t in third["transitions"]] == [
        ("AAA", "GREEN", "RED")
    ]
    assert "AAA: GREEN -> RED" in third["transitions"][0]["message"]
    assert len(holdings_monitor.list_alerts(conn)) == 2


# --- sales -------------------------------------------------------------------


def test_sale_while_yellow_is_owed_if_it_resolves_red():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="NEAR", quantity=10, average_cost=10)
    _run(conn, {"NEAR": _pass(debt=31)})
    sale = holdings_monitor.record_sale(conn, symbol="NEAR", quantity=10, price=14)
    assert sale["pending"] and sale["light_at_sale"] == "YELLOW" and sale["warning"]
    assert holdings_monitor.list_holdings(conn) == []

    # Fully sold, but still screened so the pending sale can resolve.
    result = _run(conn, {"NEAR": REJECT})
    assert result["sales"]["resolved_this_run"] == 1
    assert result["sales"]["total_owed_on_sales"] == 40.0  # 10 * (14 - 10)
    assert result["holdings"] == []


def test_sale_while_yellow_owes_nothing_if_it_resolves_green():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="NEAR", quantity=10, average_cost=10)
    _run(conn, {"NEAR": _pass(debt=31)})
    holdings_monitor.record_sale(conn, symbol="NEAR", quantity=4, price=14)
    result = _run(conn, {"NEAR": _pass(debt=10, report_date="2026-12-31")})
    assert result["sales"]["pending"] == [] and result["sales"]["total_owed_on_sales"] == 0
    [held] = holdings_monitor.list_holdings(conn)
    assert held["quantity"] == 6 and held["cost_basis"] == 60


def test_sale_while_red_is_owed_at_once_and_a_loss_owes_nothing():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="BAD", quantity=10, average_cost=10)
    _run(conn, {"BAD": REJECT})
    assert (
        holdings_monitor.record_sale(conn, symbol="BAD", quantity=5, price=12)["purification_due"]
        == 10.0
    )
    assert (
        holdings_monitor.record_sale(conn, symbol="BAD", quantity=5, price=8)["purification_due"]
        == 0.0
    )


def test_cannot_sell_more_than_recorded():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="AAA", quantity=1, average_cost=1)
    with pytest.raises(HoldingError):
        holdings_monitor.record_sale(conn, symbol="AAA", quantity=2, price=1)
    with pytest.raises(HoldingError):
        holdings_monitor.record_sale(conn, symbol="ZZZ", quantity=1, price=1)


# --- reviews -----------------------------------------------------------------


def test_only_the_owner_can_escalate():
    conn = _conn()
    with pytest.raises(HoldingError):
        holdings_monitor.add_review(
            conn, symbol="AAA", kind="escalation", detail="x", source="model"
        )
    with pytest.raises(HoldingError):
        holdings_monitor.add_review(conn, symbol="AAA", kind="clear", detail="x", source="owner")


def test_owner_escalation_is_red_until_a_newer_report():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="AAA", quantity=1, average_cost=1)
    holdings_monitor.add_review(
        conn,
        symbol="AAA",
        kind="escalation",
        detail="10-K segment note shows casino revenue",
        source="owner",
        report_date="2025-12-31",
    )
    assert _light(_run(conn, {"AAA": _pass()}), "AAA") == "RED"
    # A new annual report: the screen runs fresh and the old review lapses.
    assert _light(_run(conn, {"AAA": _pass(report_date="2026-12-31")}), "AAA") == "GREEN"


def test_model_flag_adds_yellow_and_a_note_adds_nothing():
    conn = _conn()
    holdings_monitor.upsert_holding(conn, symbol="AAA", quantity=1, average_cost=1)
    holdings_monitor.add_review(
        conn,
        symbol="AAA",
        kind="note",
        detail="no non-compliant segment found",
        source="model",
        report_date="2025-12-31",
    )
    assert _light(_run(conn, {"AAA": _pass()}), "AAA") == "GREEN"
    holdings_monitor.add_review(
        conn,
        symbol="AAA",
        kind="flag",
        code="mixed_business_activity",
        detail="interest income 6% of revenue",
        quote="Interest income ... 6%",
        source="model",
        report_date="2025-12-31",
    )
    result = _run(conn, {"AAA": _pass()})
    assert _light(result, "AAA") == "YELLOW"
    reason = result["holdings"][0]["traffic_light"]["reasons"][0]
    assert reason["quote"] == "Interest income ... 6%"
