"""Verify the read-only execution audit contract."""

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import local_api
from approval_queue import record_approval, record_broker_reconciliation, record_broker_submission, update_execution_status


fixture_dir = tempfile.TemporaryDirectory()
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"


def ready_preview(symbol: str = "AAPL") -> dict:
    return {
        "status": "READY_FOR_APPROVAL",
        "execution": "PAPER_ONLY",
        "broker_submission": False,
        "symbol": symbol,
        "side": "BUY",
        "quantity": 1,
        "price": 110.0,
        "notional": 110.0,
        "blockers": [],
        "quote_snapshot": {
            "symbol": symbol,
            "latest_close": 110.0,
            "latest_date": "2026-07-25",
            "source": "test_price",
        },
        "agent_summary": {
            "shariah": {"status": "PASS", "market": "US", "provider": "TEST"},
            "quant": {"status": "PASS", "signal": "BUY"},
            "risk": {"status": "PASS"},
        },
    }


def record_ready(connection, symbol: str = "AAPL") -> int:
    preview = ready_preview(symbol)
    approval = {
        "status": "APPROVED_PAPER_READY",
        "broker_submission": False,
        "execution_environment": "SIMULATE",
        "candidate": {
            "symbol": symbol,
            "side": "BUY",
            "quantity": 1,
            "price": 110.0,
            "notional": 110.0,
        },
    }
    return record_approval(connection, preview=preview, approval=approval, approved_by_user=True)["id"]


def mutate_payload(connection, queue_id: int, mutator) -> None:
    row = connection.execute("SELECT payload FROM approval_queue WHERE id = ?", (queue_id,)).fetchone()
    payload = json.loads(row["payload"])
    mutator(payload)
    connection.execute("UPDATE approval_queue SET payload = ? WHERE id = ?", (json.dumps(payload, sort_keys=True), queue_id))
    connection.commit()


def seed_execution_audit_data() -> dict:
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    connection = local_api.db()
    try:
        pending_id = record_ready(connection, "AAPL")
        locked_id = record_ready(connection, "MSFT")
        update_execution_status(connection, locked_id, status="EXECUTION_LOCKED", message="PAPER_EXECUTION_ENABLED is false")

        malformed_id = record_ready(connection, "TSLA")
        mutate_payload(connection, malformed_id, lambda payload: payload["preview"].pop("quote_snapshot"))

        submitted_id = record_ready(connection, "CRM")
        record_broker_submission(
            connection,
            submitted_id,
            broker_response={
                "status": "BROKER_SUBMITTED",
                "broker_submission": True,
                "broker_order_id": "BROKER-CRM",
                "submitted_at": "2026-07-25T00:00:00+00:00",
            },
        )

        filled_id = record_ready(connection, "AMD")
        record_broker_submission(
            connection,
            filled_id,
            broker_response={
                "status": "BROKER_SUBMITTED",
                "broker_submission": True,
                "broker_order_id": "BROKER-AMD",
                "submitted_at": "2026-07-25T00:00:00+00:00",
            },
        )
        record_broker_reconciliation(
            connection,
            filled_id,
            reconciliation={
                "status": "BROKER_FILLED",
                "broker_submission": True,
                "broker_order_id": "BROKER-AMD",
                "order_status": "FILLED_ALL",
                "dealt_qty": 1.0,
                "dealt_avg_price": 110.0,
                "reconciled_at": "2026-07-25T00:05:00+00:00",
            },
        )
        local_api.add_audit_event(
            "paper_execution",
            {
                "queue_id": locked_id,
                "status": "EXECUTION_LOCKED",
                "broker_submission": False,
                "execution_message": "PAPER_EXECUTION_ENABLED is false",
            },
        )
        connection.commit()
        return {
            "pending_id": pending_id,
            "locked_id": locked_id,
            "malformed_id": malformed_id,
            "submitted_id": submitted_id,
            "filled_id": filled_id,
        }
    finally:
        connection.close()


def main() -> None:
    ids = seed_execution_audit_data()
    client = TestClient(local_api.app)
    response = client.get("/execution-audit?limit=20")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK"
    assert payload["counts"]["approval_rows"] == 5
    assert payload["counts"]["pending_execution"] == 2
    assert payload["counts"]["broker_submitted"] == 2
    assert payload["counts"]["broker_filled"] == 1
    assert payload["counts"]["missing_fill_sync"] == 1
    assert payload["counts"]["payload_audit_failures"] == 1
    assert payload["counts"]["locked_or_rejected"] == 1

    pending_ids = {row["id"] for row in payload["pending_execution"]}
    assert ids["pending_id"] in pending_ids
    assert ids["malformed_id"] in pending_ids
    assert payload["payload_audit_failures"][0]["id"] == ids["malformed_id"]
    assert "payload.preview.quote_snapshot_missing" in payload["payload_audit_failures"][0]["approval_audit_errors"]
    assert payload["locked_or_rejected"][0]["id"] == ids["locked_id"]
    assert payload["missing_fill_sync"][0]["id"] == ids["filled_id"]
    assert payload["missing_fill_sync"][0]["broker_reconciliation_status"] == "BROKER_FILLED"
    assert payload["broker_submitted"][0]["broker_submission"] is True
    assert payload["recent_execution_events"][0]["event_type"] == "paper_execution"
    print("PASS: execution audit contract summarizes queue integrity and broker safety state.")


if __name__ == "__main__":
    import auth
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    try:
        main()
    finally:
        _my_app.dependency_overrides.pop(_get_owner_actor, None)
