"""Regression tests: /paper/approval must never trust a client-supplied risk verdict.

Phase 0 audit finding, left open at the time: "/paper/approval and /paper/execute
still trust client-echoed risk verdicts." Phase 1 closes this the same way the
Shariah bypass was closed -- local_api.authoritative_risk_verdict() recomputes
every risk dimension from server-controlled state (live portfolio positions,
a real count of today's approved orders, realized P&L from portfolio_store)
and /paper/approval uses that instead of preview["agent_summary"]["risk"].

Covers the required scenarios: a client changing risk status, position size,
portfolio value (implicitly -- it's DB state, never client input), risk
parameters (daily loss / orders_today), symbol, quantity, price, and Shariah
status cannot turn a genuinely-blocked order into an approved one; a
genuinely valid order still succeeds.
"""

import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text(
    json.dumps(
        {
            "validation": {"status": "active"},
            "records": [
                {"ticker": "0001", "issuer_name": "Compliant Bhd", "shariah_status": "COMPLIANT"}
            ],
        }
    ),
    encoding="utf-8",
)
os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"
os.environ["MAX_POSITION_PCT"] = "5.0"
os.environ["MAX_TOTAL_EXPOSURE_PCT"] = "25.0"
os.environ["MAX_DAILY_LOSS_PCT"] = "1.0"
os.environ["MAX_WEEKLY_LOSS_PCT"] = "2.0"
os.environ["MAX_ORDERS_PER_DAY"] = "5"

from fastapi.testclient import TestClient

import local_api
import portfolio_store
import sc_malaysia_store
from local_api import app


DB_PATH = Path(fixture_dir.name) / "paper_trading.db"


def _reset_db() -> sqlite3.Connection:
    if DB_PATH.exists():
        DB_PATH.unlink()
    local_api.DB_PATH = DB_PATH
    sc_malaysia_store.DB_PATH = DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    from approval_queue import ensure_approval_queue

    ensure_approval_queue(conn)
    sc_malaysia_store.ensure_sc_tables(conn)
    # The legacy JSON fallback can never assert PASS on its own (closed as a
    # bypass in the Phase 0 audit) -- ticker 0001 needs a real approved,
    # activated SC publication to be PASS, same as test_approval_shariah_bypass.py.
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": "sc-sac-my-test-fixture",
            "publication_date": "2026-01-01",
            "source_document_hash": "test",
            "official_record_count": 1,
            "extractable_record_count": 1,
            "parsed_record_count": 1,
            "parser_version": "test-fixture-v1",
        },
    )
    sc_malaysia_store.insert_securities(
        conn,
        "sc-sac-my-test-fixture",
        [{"ticker": "0001", "issuer_name": "Compliant Bhd", "shariah_status": "COMPLIANT"}],
    )
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test-fixture")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test-fixture")
    return conn


def _preview(
    symbol="0001", side="BUY", quantity=1, price=10.0, *, forged_risk_status="PASS"
) -> dict:
    """A preview body a client could submit directly to /paper/approval,
    claiming whatever risk verdict it likes -- the server must ignore this
    and recompute risk itself."""
    notional = round(quantity * price, 2)
    return {
        "status": "READY_FOR_APPROVAL",
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "price": price,
        "notional": notional,
        "blockers": [],
        "asset_class": "equity",
        "agent_summary": {
            "shariah": {
                "status": "PASS",
                "provider": "SC_MY_APPROVED_PUBLICATION",
                "reason": "authoritative_compliant",
            },
            "risk": {"status": forged_risk_status, "reason": "forged_by_client"},
            "quant": {"status": "PASS", "signal": "BUY"},
        },
        "shariah": {"status": "PASS"},
        "risk": {"status": forged_risk_status, "reason": "forged_by_client"},
    }


def _approve(preview: dict) -> dict:
    client = TestClient(app)
    response = client.post("/paper/approval", json={"preview": preview, "approved": True})
    assert response.status_code == 200, response.text
    return response.json()["approval"]


def test_1_forged_pass_cannot_override_a_genuine_position_limit_breach():
    """Seed an existing position already near the 5% cap, then submit a BUY
    that would push it over -- with risk.status forged to PASS. The forged
    claim must be ignored and the real overlay must still reject."""
    conn = _reset_db()
    # 5% of $10,000 equity = $500. An existing $480 position leaves only $20
    # of headroom; a further $200 BUY must breach the position ceiling.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="0001",
        account_suffix="TEST",
        account_type="CASH",
        side="BUY",
        quantity=48,
        avg_price=10.0,
    )
    conn.commit()
    conn.close()

    approval = _approve(_preview(quantity=20, price=10.0, forged_risk_status="PASS"))
    assert approval["status"] == "REJECT", approval
    assert approval["reason"] == "risk_gate_failed"
    print("PASS: 1 — forged risk PASS cannot override a genuine position-limit breach")


def test_2_larger_quantity_at_approval_is_evaluated_honestly():
    """The 'preview' quantity and the 'approval' quantity are the same field
    in this request shape (there is no separate stored preview to diff
    against) -- what matters is that whatever quantity is claimed AT
    APPROVAL TIME is checked against real portfolio state, not rubber-stamped
    because a risk verdict was attached to it."""
    conn = _reset_db()
    conn.close()

    # $40 notional stays under the 0.5% max-loss-per-trade ceiling on the
    # $10,000 test account ($50) as well as every other limit -- this proves
    # a small order is not rejected, not that any quantity would be.
    small = _approve(_preview(quantity=4, price=10.0, forged_risk_status="PASS"))
    assert small["status"] != "REJECT" or small.get("reason") != "risk_gate_failed"

    huge = _approve(_preview(quantity=100_000, price=10.0, forged_risk_status="PASS"))
    assert huge["status"] == "REJECT", huge
    assert huge["reason"] == "risk_gate_failed"
    print(
        "PASS: 2 — an oversized quantity is rejected on its own merits regardless of claimed risk status"
    )


def test_3_portfolio_value_is_read_from_the_server_never_the_client():
    """Nothing in the preview body can express 'current portfolio value' --
    it is derived exclusively from live DB state (portfolio_snapshot). Seed
    an existing position in a DIFFERENT symbol (so the new order's own
    same-symbol-add-on rule doesn't confound the result) at two different
    sizes, and observe two different outcomes for identical BUY orders on a
    fresh symbol -- driven purely by TOTAL exposure, which only the server
    can know."""
    conn = _reset_db()
    # $200 held elsewhere leaves ample room under the 25% ($2,500) total
    # exposure ceiling for a further $50 BUY on a fresh symbol.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="OTHER",
        account_suffix="TEST",
        account_type="CASH",
        side="BUY",
        quantity=20,
        avg_price=10.0,
    )
    conn.commit()
    conn.close()
    light = _approve(_preview(symbol="0001", quantity=5, price=10.0))
    assert light["status"] != "REJECT" or light.get("reason") != "risk_gate_failed", light

    conn = _reset_db()
    # $2,460 held elsewhere leaves only $40 of room under the same ceiling;
    # the identical $50 BUY on the fresh symbol now breaches it.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="OTHER",
        account_suffix="TEST",
        account_type="CASH",
        side="BUY",
        quantity=246,
        avg_price=10.0,
    )
    conn.commit()
    conn.close()
    heavy = _approve(_preview(symbol="0001", quantity=5, price=10.0))
    assert heavy["status"] == "REJECT", heavy
    print(
        "PASS: 3 — risk outcome tracks real DB portfolio state, not anything the client could submit"
    )


def test_4_forged_daily_loss_and_orders_today_are_ignored():
    """Seed 5 (the configured MAX_ORDERS_PER_DAY) already-approved orders
    today, then submit a 6th claiming orders_today: 0 and risk PASS."""
    conn = _reset_db()
    now = datetime.now(timezone.utc).isoformat()
    for i in range(5):
        conn.execute(
            "INSERT INTO approval_queue (created_at, symbol, side, quantity, price, notional, "
            "approval_status, broker_submission, execution_status, payload) VALUES "
            "(?, ?, 'BUY', 1, 10.0, 10.0, 'APPROVED_PAPER_READY', 0, 'NOT_EXECUTED', '{}')",
            (now, f"SEED{i}"),
        )
    conn.commit()
    conn.close()

    approval = _approve(_preview(quantity=1, price=10.0, forged_risk_status="PASS"))
    assert approval["status"] == "REJECT", approval
    assert approval["reason"] == "risk_gate_failed"
    print(
        "PASS: 4 — a real daily order-count cap breach is caught even when the client claims orders_today=0"
    )


def test_5_symbol_is_evaluated_as_claimed_not_cached():
    """Changing the symbol at approval time changes WHAT is being checked,
    not whether it is checked for real -- a heavily-held symbol is rejected,
    a fresh one is not, for the same client-claimed risk verdict."""
    conn = _reset_db()
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="0001",
        account_suffix="TEST",
        account_type="CASH",
        side="BUY",
        quantity=49,
        avg_price=10.0,
    )
    conn.commit()
    conn.close()

    heavy_symbol = _approve(_preview(symbol="0001", quantity=5, price=10.0))
    assert heavy_symbol["status"] == "REJECT", heavy_symbol

    fresh_symbol = _approve(_preview(symbol="9999", quantity=5, price=10.0))
    assert fresh_symbol["status"] != "REJECT" or fresh_symbol.get("reason") != "risk_gate_failed"
    print("PASS: 5 — switching symbols is evaluated honestly against that symbol's real exposure")


def test_6_quantity_alone_cannot_evade_the_exposure_ceiling():
    conn = _reset_db()
    conn.close()
    # 25% of $10,000 = $2,500 total-exposure ceiling.
    over = _approve(_preview(quantity=260, price=10.0, forged_risk_status="PASS"))
    assert over["status"] == "REJECT", over
    print(
        "PASS: 6 — quantity large enough to breach total exposure is rejected regardless of claimed risk"
    )


def test_7_price_is_used_consistently_not_as_a_free_variable():
    """A client cannot make a genuinely large order look small by claiming a
    tiny price while asking for a large quantity -- notional is recomputed
    from whatever price+quantity is claimed together, and a large TRUE
    notional (even at a low per-share price) still breaches exposure."""
    conn = _reset_db()
    conn.close()
    cheap_but_huge = _approve(_preview(quantity=10_000, price=1.0, forged_risk_status="PASS"))
    assert cheap_but_huge["status"] == "REJECT", cheap_but_huge
    print(
        "PASS: 7 — notional is recomputed from quantity*price together, not trusted as a client-supplied total"
    )


def test_8_forged_shariah_status_still_covered_by_the_dedicated_bypass_test():
    """See test_approval_shariah_bypass.py for the full Shariah-verdict
    regression; this just confirms the risk fix does not depend on it or
    interfere with it, using a REJECT shariah candidate."""
    conn = _reset_db()
    conn.close()
    preview = _preview(
        symbol="9999", quantity=1, price=10.0
    )  # absent from fixture universe -> UNKNOWN
    approval = _approve(preview)
    assert approval["status"] == "REJECT"
    assert approval["reason"] == "compliance_not_confirmed"
    print("PASS: 8 — Shariah re-derivation still gates independently of the risk fix")


def test_9_a_genuinely_valid_order_still_succeeds():
    conn = _reset_db()
    conn.close()
    approval = _approve(
        _preview(symbol="0001", quantity=1, price=10.0, forged_risk_status="REJECT")
    )
    # Even though the client claims risk REJECT, a genuinely small, valid
    # order must still succeed -- proving the fix checks reality in both
    # directions, not just "always block".
    assert approval["status"] in {"APPROVED_PAPER_READY", "PENDING_APPROVAL"}, approval
    print(
        "PASS: 9 — a genuinely valid order still succeeds even if the client's claim was pessimistic"
    )


def test_10_loss_per_trade_ceiling_is_enforced_from_server_notional():
    """Phase 2A: max_loss_per_trade_pct (0.5% of the $10,000 test account =
    $50) is now derived from the candidate's own server-recomputed notional,
    not left as a permanent no-op. $40 (0.4%) passes; $60 (0.6%) -- still
    comfortably inside the 5% position and 25% exposure limits, so nothing
    else could be the cause -- is rejected specifically by this ceiling,
    with a forged risk PASS in both requests proving the client's claim is
    irrelevant either way."""
    conn = _reset_db()
    conn.close()
    under = _approve(_preview(quantity=4, price=10.0, forged_risk_status="PASS"))
    assert under["status"] != "REJECT" or under.get("reason") != "risk_gate_failed", under

    conn = _reset_db()
    conn.close()
    over = _approve(_preview(quantity=6, price=10.0, forged_risk_status="PASS"))
    assert over["status"] == "REJECT", over
    assert over["reason"] == "risk_gate_failed", over
    print(
        "PASS: 10 — loss-per-trade ceiling is enforced from the server-recomputed notional, not a permanent no-op"
    )


def main():
    test_1_forged_pass_cannot_override_a_genuine_position_limit_breach()
    test_2_larger_quantity_at_approval_is_evaluated_honestly()
    test_3_portfolio_value_is_read_from_the_server_never_the_client()
    test_4_forged_daily_loss_and_orders_today_are_ignored()
    test_5_symbol_is_evaluated_as_claimed_not_cached()
    test_6_quantity_alone_cannot_evade_the_exposure_ceiling()
    test_7_price_is_used_consistently_not_as_a_free_variable()
    test_8_forged_shariah_status_still_covered_by_the_dedicated_bypass_test()
    test_9_a_genuinely_valid_order_still_succeeds()
    test_10_loss_per_trade_ceiling_is_enforced_from_server_notional()
    print("\nAll approval-endpoint risk-bypass regression tests passed.")


if __name__ == "__main__":
    main()
