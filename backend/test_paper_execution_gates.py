"""Verify paper execution gates without contacting Moomoo."""

import json
import os
import sqlite3

import paper_execution
from approval_queue import get_approval, record_approval
from paper_execution import execute_paper_order, reconcile_submitted_paper_order
from portfolio_store import apply_fill_to_position, ensure_portfolio_tables


READY_MOOMOO = {
    "status": "paper_account_ready",
    "paper_account_ready": True,
    "environment": "SIMULATE",
    "account_type": "CASH",
    "account_status": "ACTIVE",
    "account_suffix": "1234",
    "broker_submission": False,
}


NOT_READY_MOOMOO = {
    "status": "paper_account_missing",
    "paper_account_ready": False,
    "reason": "active_simulate_cash_account_not_found",
    "broker_submission": False,
}


def make_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def add_approval(
    connection: sqlite3.Connection,
    *,
    shariah_status: str = "PASS",
    risk_status: str = "PASS",
    side: str = "BUY",
    quantity: int = 1,
    asset_class: str = "equity",
) -> int:
    blockers = [] if shariah_status == "PASS" and risk_status == "PASS" else ["test_blocker"]
    preview = {
        "status": "READY_FOR_APPROVAL",
        "execution": "PAPER_ONLY",
        "broker_submission": False,
        "symbol": "AAPL",
        "side": side,
        "quantity": quantity,
        "asset_class": asset_class,
        "price": 333.74,
        "notional": round(quantity * 333.74, 2),
        "blockers": blockers,
        "quote_snapshot": {
            "symbol": "AAPL",
            "latest_close": 333.74,
            "latest_date": "2026-07-24",
            "source": "test_price",
            "bars": 220,
            "min_bars": 1,
            "enough_history": True,
            "fallback_allowed": False,
            "stale_cache_allowed": True,
            "quote_snapshot_source": "test",
        },
        "agent_summary": {
            "shariah": {"status": shariah_status, "market": "US", "provider": "ZOYA"},
            "quant": {"status": "PASS", "signal": side},
            "risk": {"status": risk_status},
        },
    }
    approval = {
        "status": "APPROVED_PAPER_READY",
        "broker_submission": False,
        "execution_environment": "SIMULATE",
        "candidate": {
            "signal": side,
            "compliance": {"status": "COMPLIANT", "source": "TEST"},
            "symbol": "AAPL",
            "side": side,
            "quantity": quantity,
            "price": 333.74,
            "notional": round(quantity * 333.74, 2),
        },
    }
    return record_approval(connection, preview=preview, approval=approval, approved_by_user=True)[
        "id"
    ]


def mutate_payload(connection: sqlite3.Connection, queue_id: int, mutator) -> None:
    row = connection.execute(
        "SELECT payload FROM approval_queue WHERE id = ?", (queue_id,)
    ).fetchone()
    payload = json.loads(row["payload"])
    mutator(payload)
    connection.execute(
        "UPDATE approval_queue SET payload = ? WHERE id = ?",
        (json.dumps(payload, sort_keys=True), queue_id),
    )
    connection.commit()


def seed_position(
    connection: sqlite3.Connection, *, quantity: float, account_suffix: str = "1234"
) -> None:
    ensure_portfolio_tables(connection)
    apply_fill_to_position(
        connection,
        symbol="AAPL",
        account_suffix=account_suffix,
        account_type="CASH",
        side="BUY",
        quantity=quantity,
        avg_price=300.0,
    )
    connection.commit()


def set_execution_env(
    *, trading_mode: str = "approval", enabled: bool = True, adapter: str = "disabled"
) -> None:
    os.environ["TRADING_MODE"] = trading_mode
    os.environ["PAPER_EXECUTION_ENABLED"] = "true" if enabled else "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = adapter


def main() -> None:
    original_env = {
        key: os.environ.get(key)
        for key in ["TRADING_MODE", "PAPER_EXECUTION_ENABLED", "PAPER_EXECUTION_ADAPTER"]
    }
    original_moomoo_status = paper_execution.check_moomoo_status
    original_reconcile = paper_execution.reconcile_paper_order
    try:
        connection = make_connection()
        paper_execution.check_moomoo_status = lambda market="US": READY_MOOMOO

        set_execution_env(trading_mode="advisory", enabled=True, adapter="fake")
        advisory_id = add_approval(connection)
        advisory_result = execute_paper_order(connection, advisory_id)
        assert advisory_result["status"] == "TRADING_MODE_BLOCKED"
        assert advisory_result["broker_submission"] is False

        set_execution_env(trading_mode="approval", enabled=False, adapter="fake")
        locked_id = add_approval(connection)
        locked_result = execute_paper_order(connection, locked_id)
        assert locked_result["status"] == "EXECUTION_LOCKED"
        assert locked_result["broker_submission"] is False

        set_execution_env(trading_mode="approval", enabled=True, adapter="fake")
        shariah_id = add_approval(connection, shariah_status="FAIL")
        shariah_result = execute_paper_order(connection, shariah_id)
        assert shariah_result["status"] == "SHARIAH_GATE_FAILED"
        assert shariah_result["broker_submission"] is False

        risk_id = add_approval(connection, risk_status="FAIL")
        risk_result = execute_paper_order(connection, risk_id)
        assert risk_result["status"] == "RISK_GATE_FAILED"
        assert risk_result["broker_submission"] is False

        paper_execution.check_moomoo_status = lambda market="US": NOT_READY_MOOMOO
        moomoo_id = add_approval(connection)
        moomoo_result = execute_paper_order(connection, moomoo_id)
        assert moomoo_result["status"] == "MOOMOO_NOT_READY"
        assert moomoo_result["broker_submission"] is False

        paper_execution.check_moomoo_status = lambda market="US": READY_MOOMOO
        set_execution_env(trading_mode="approval", enabled=True, adapter="disabled")
        adapter_id = add_approval(connection)
        adapter_result = execute_paper_order(connection, adapter_id)
        assert adapter_result["status"] == "ADAPTER_NOT_CONFIGURED"
        assert adapter_result["broker_submission"] is False

        set_execution_env(trading_mode="approval", enabled=True, adapter="fake")
        missing_quote_id = add_approval(connection)
        mutate_payload(
            connection, missing_quote_id, lambda payload: payload["preview"].pop("quote_snapshot")
        )
        missing_quote_result = execute_paper_order(connection, missing_quote_id)
        assert missing_quote_result["status"] == "APPROVAL_AUDIT_FAILED"
        assert (
            "payload.preview.quote_snapshot_missing"
            in missing_quote_result["approval_audit"]["errors"]
        )
        assert missing_quote_result["broker_submission"] is False

        stale_blocker_id = add_approval(connection)
        mutate_payload(
            connection,
            stale_blocker_id,
            lambda payload: payload["preview"].update({"blockers": ["portfolio_position_limit"]}),
        )
        stale_blocker_result = execute_paper_order(connection, stale_blocker_id)
        assert stale_blocker_result["status"] == "APPROVAL_AUDIT_FAILED"
        assert (
            "payload.preview.blockers_must_be_empty"
            in stale_blocker_result["approval_audit"]["errors"]
        )
        assert stale_blocker_result["broker_submission"] is False

        preview_symbol_mismatch_id = add_approval(connection)
        mutate_payload(
            connection,
            preview_symbol_mismatch_id,
            lambda payload: payload["preview"].update({"symbol": "MSFT"}),
        )
        preview_symbol_mismatch_result = execute_paper_order(connection, preview_symbol_mismatch_id)
        assert preview_symbol_mismatch_result["status"] == "APPROVAL_AUDIT_FAILED"
        assert (
            "payload.preview.symbol_mismatch"
            in preview_symbol_mismatch_result["approval_audit"]["errors"]
        )
        assert preview_symbol_mismatch_result["broker_submission"] is False

        candidate_quantity_mismatch_id = add_approval(connection)
        mutate_payload(
            connection,
            candidate_quantity_mismatch_id,
            lambda payload: payload["approval"]["candidate"].update({"quantity": 99}),
        )
        candidate_quantity_mismatch_result = execute_paper_order(
            connection, candidate_quantity_mismatch_id
        )
        assert candidate_quantity_mismatch_result["status"] == "APPROVAL_AUDIT_FAILED"
        assert (
            "payload.approval.candidate.quantity_mismatch"
            in candidate_quantity_mismatch_result["approval_audit"]["errors"]
        )
        assert candidate_quantity_mismatch_result["broker_submission"] is False

        set_execution_env(trading_mode="approval", enabled=True, adapter="fake")
        sell_without_position_id = add_approval(connection, side="SELL")
        sell_without_position_result = execute_paper_order(connection, sell_without_position_id)
        assert sell_without_position_result["status"] == "PORTFOLIO_SELL_GATE_FAILED"
        assert sell_without_position_result["broker_submission"] is False
        assert sell_without_position_result["portfolio_gate"]["available_quantity"] == 0.0

        # A sell-to-open option (covered call, cash-secured put) has no local equity
        # position by definition -- it is secured by cash or already-owned shares
        # proven by option_structure_gate/account_shariah_gate at approval time, not
        # by this equity-only ledger check. Must run before seed_position below: an
        # account that happens to already hold shares would let this pass by
        # coincidence rather than by the asset_class exemption actually working --
        # exactly how the real CVX option order masked this bug the first time.
        option_sell_without_position_id = add_approval(
            connection, side="SELL", asset_class="option"
        )
        option_sell_without_position_result = execute_paper_order(
            connection, option_sell_without_position_id
        )
        assert option_sell_without_position_result["status"] != "PORTFOLIO_SELL_GATE_FAILED"
        assert option_sell_without_position_result["broker_submission"] is True

        seed_position(connection, quantity=1.0)
        sell_exceeds_position_id = add_approval(connection, side="SELL", quantity=2)
        sell_exceeds_position_result = execute_paper_order(connection, sell_exceeds_position_id)
        assert sell_exceeds_position_result["status"] == "PORTFOLIO_SELL_GATE_FAILED"
        assert sell_exceeds_position_result["broker_submission"] is False
        assert sell_exceeds_position_result["portfolio_gate"]["available_quantity"] == 1.0

        sell_reduce_id = add_approval(connection, side="SELL")
        sell_reduce_result = execute_paper_order(connection, sell_reduce_id)
        assert sell_reduce_result["status"] == "BROKER_SUBMITTED"
        assert sell_reduce_result["broker_submission"] is True
        assert sell_reduce_result["broker_response"]["side"] == "SELL"

        set_execution_env(trading_mode="approval", enabled=True, adapter="fake")
        submitted_id = add_approval(connection)
        submitted_result = execute_paper_order(connection, submitted_id)
        assert submitted_result["status"] == "BROKER_SUBMITTED"
        assert submitted_result["broker_submission"] is True
        assert (
            submitted_result["broker_response"]["broker_order_id"] == f"FAKE-PAPER-{submitted_id}"
        )

        submitted_row = get_approval(connection, submitted_id)
        assert submitted_row["broker_submission"] is True
        assert submitted_row["execution_status"] == "BROKER_SUBMITTED"
        payload = json.loads(submitted_row["payload"])
        assert payload["broker_submission"]["adapter"] == "fake"

        paper_execution.reconcile_paper_order = lambda approval: {
            "status": "BROKER_FILLED",
            "adapter": "fake",
            "broker_submission": True,
            "broker_order_id": f"FAKE-PAPER-{submitted_id}",
            "order_status": "FILLED_ALL",
            "dealt_qty": 1.0,
            "dealt_avg_price": 333.7,
            "reconciled_at": "2026-07-24T00:00:00+00:00",
        }
        reconciliation_result = reconcile_submitted_paper_order(connection, submitted_id)
        assert reconciliation_result["status"] == "BROKER_FILLED"
        assert reconciliation_result["broker_submission"] is True

        reconciled_row = get_approval(connection, submitted_id)
        assert reconciled_row["execution_status"] == "BROKER_FILLED"
        reconciled_payload = json.loads(reconciled_row["payload"])
        assert reconciled_payload["broker_reconciliation"]["order_status"] == "FILLED_ALL"
        assert len(reconciled_payload["broker_reconciliation_history"]) == 1

        duplicate_result = execute_paper_order(connection, submitted_id)
        assert duplicate_result["status"] == "ALREADY_SUBMITTED"
        assert duplicate_result["broker_submission"] is True
    finally:
        paper_execution.check_moomoo_status = original_moomoo_status
        paper_execution.reconcile_paper_order = original_reconcile
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("PASS: paper execution gates prevent unsafe broker submission.")


if __name__ == "__main__":
    main()
