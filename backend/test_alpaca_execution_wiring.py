"""Verify paper_execution routes to the Alpaca adapter without contacting Alpaca or OpenD."""

import json
import os
import sqlite3

import alpaca_paper_adapter
import paper_execution
from approval_queue import get_approval
from paper_execution import execute_paper_order
from test_alpaca_paper_adapter import FakeRest
from test_paper_execution_gates import add_approval, make_connection, set_execution_env


READY_ALPACA = {
    "status": "paper_account_ready",
    "paper_account_ready": True,
    "environment": "PAPER",
    "account_type": "MARGIN",
    "account_status": "ACTIVE",
    "account_suffix": "1740",
    "broker_submission": False,
}


NOT_READY_ALPACA = {
    "status": "credentials_missing",
    "paper_account_ready": False,
    "reason": "alpaca_credentials_missing",
    "broker_submission": False,
}


def poison(*args, **kwargs):
    raise AssertionError("the Alpaca broker path must not run for a non-Alpaca adapter")


def check_alpaca_adapter_submits(connection: sqlite3.Connection) -> None:
    set_execution_env(adapter="alpaca")
    paper_execution.check_alpaca_status = lambda: READY_ALPACA
    alpaca_paper_adapter.alpaca_request = FakeRest()

    queue_id = add_approval(connection)
    result = execute_paper_order(connection, queue_id)
    assert result["status"] == "BROKER_SUBMITTED", result
    assert result["broker_submission"] is True
    assert result["moomoo"]["environment"] == "PAPER"

    stored = json.loads(get_approval(connection, queue_id)["payload"])["broker_submission"]
    assert stored["adapter"] == "alpaca", stored
    assert stored["broker_order_id"] == "ALPACA-ORDER-1"
    assert stored["environment"] == "PAPER"
    assert stored["account_suffix"] == "1740"


def check_broker_not_ready_blocks(connection: sqlite3.Connection) -> None:
    set_execution_env(adapter="alpaca")
    paper_execution.check_alpaca_status = lambda: NOT_READY_ALPACA
    alpaca_paper_adapter.alpaca_request = poison

    queue_id = add_approval(connection)
    result = execute_paper_order(connection, queue_id)
    assert result["status"] == "BROKER_NOT_READY", result
    assert result["broker_submission"] is False


def check_fake_adapter_keeps_moomoo_path(connection: sqlite3.Connection) -> None:
    set_execution_env(adapter="fake")
    paper_execution.check_alpaca_status = poison
    alpaca_paper_adapter.alpaca_request = poison
    paper_execution.check_moomoo_status = lambda market="US": {
        "status": "paper_account_ready",
        "paper_account_ready": True,
        "environment": "SIMULATE",
        "account_type": "CASH",
        "account_status": "ACTIVE",
        "account_suffix": "1234",
        "broker_submission": False,
    }

    queue_id = add_approval(connection)
    result = execute_paper_order(connection, queue_id)
    assert result["status"] == "BROKER_SUBMITTED", result
    stored = json.loads(get_approval(connection, queue_id)["payload"])["broker_submission"]
    assert stored["adapter"] == "fake", stored
    assert stored["environment"] == "SIMULATE"


def check_reconcile_routes_by_stored_adapter() -> None:
    set_execution_env(adapter="fake")

    def approval_with(broker_submission: dict) -> dict:
        return {
            "id": 7,
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 1,
            "price": 333.74,
            "shariah_market": "US",
            "broker_submission": True,
            "payload": json.dumps({"broker_submission": broker_submission}),
        }

    alpaca_paper_adapter.alpaca_request = FakeRest(
        order_lookup={
            "id": "ALPACA-ORDER-9",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "1",
            "filled_qty": "1",
            "filled_avg_price": "333.70",
            "status": "filled",
            "updated_at": "2026-08-18T14:00:00Z",
        }
    )
    result = paper_execution.reconcile_for_approval(
        approval_with({"adapter": "alpaca", "broker_order_id": "ALPACA-ORDER-9", "broker_code": "AAPL"})
    )
    assert result["status"] == "BROKER_FILLED", result
    assert result["adapter"] == "alpaca"
    assert result["dealt_qty"] == 1.0

    alpaca_paper_adapter.alpaca_request = poison
    result = paper_execution.reconcile_for_approval(
        approval_with({"adapter": "fake", "broker_order_id": "FAKE-PAPER-7", "broker_code": "AAPL"})
    )
    assert result["adapter"] == "fake", result
    assert result["broker_order_id"] == "FAKE-PAPER-7"


def main() -> None:
    saved_env = {
        key: os.environ.get(key)
        for key in [
            "TRADING_MODE",
            "PAPER_EXECUTION_ENABLED",
            "PAPER_EXECUTION_ADAPTER",
            "ALPACA_API_KEY_ID",
            "ALPACA_SECRET_KEY",
        ]
    }
    original_alpaca_status = paper_execution.check_alpaca_status
    original_moomoo_status = paper_execution.check_moomoo_status
    original_request = alpaca_paper_adapter.alpaca_request

    os.environ["ALPACA_API_KEY_ID"] = "TEST-KEY-ID"
    os.environ["ALPACA_SECRET_KEY"] = "TEST-SECRET"
    connection = make_connection()
    try:
        check_alpaca_adapter_submits(connection)
        check_broker_not_ready_blocks(connection)
        check_fake_adapter_keeps_moomoo_path(connection)
        check_reconcile_routes_by_stored_adapter()
    finally:
        connection.close()
        paper_execution.check_alpaca_status = original_alpaca_status
        paper_execution.check_moomoo_status = original_moomoo_status
        alpaca_paper_adapter.alpaca_request = original_request
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("PASS: paper execution routes to the Alpaca adapter and reconciles by the stored adapter.")


if __name__ == "__main__":
    main()
