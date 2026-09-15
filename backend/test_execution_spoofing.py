"""Regression test: /paper/execute/{queue_id} cannot be tricked into
executing merely because a client claims approved=true, shariah=PASS, or
risk=PASS in the request body.

Phase 2A objective 5/8 (execution authority-path audit + execution
spoofing tests). PaperExecutionRequest (local_api.py) has exactly one
field, confirmation_phrase -- there is no schema field for approved,
shariah, or risk at all, so FastAPI/Pydantic silently drops any such
fields a client sends; they never reach execute_paper_order(). Execution
is gated entirely by the server-stored approval_queue row (approval_status,
shariah_status, risk_status -- all populated at /paper/approval time from
authoritative_shariah_verdict/authoritative_risk_verdict, never from the
client) looked up by queue_id, plus validate_approval_payload_for_execution's
internal consistency check on the payload captured at approval time. This
proves that boundary holds even when a client actively tries to inject
those fields, and that a queue_id with no genuine server-side approval at
all is rejected outright.
"""
import pytest
import auth
@pytest.fixture(autouse=True)
def _owner_auth_fixture():
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    yield
    _my_app.dependency_overrides.pop(_get_owner_actor, None)


import json
import os
import sqlite3
import tempfile
from pathlib import Path

fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text(
    json.dumps({"validation": {"status": "active"}, "records": []}), encoding="utf-8"
)

_ENV_ORIG = {k: os.environ.get(k) for k in ["SHARIAH_UNIVERSE_PATH", "TRADING_MODE", "PAPER_EXECUTION_ENABLED", "PAPER_EXECUTION_ADAPTER", "MOOMOO_MODE", "PAPER_ACCOUNT_EQUITY"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
    os.environ["MOOMOO_MODE"] = "paper"
    os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"
    yield
    for _k in _ENV_ORIG:
        if _ENV_ORIG[_k] is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _ENV_ORIG[_k]

os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"

from fastapi.testclient import TestClient

import local_api
import portfolio_store
import sc_malaysia_store
from local_api import app

DB_PATH = Path(fixture_dir.name) / "paper_trading.db"


def _reset_db() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    local_api.DB_PATH = DB_PATH
    sc_malaysia_store.DB_PATH = DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    sc_malaysia_store.ensure_sc_tables(conn)
    conn.close()


SPOOFED_EXECUTE_BODY = {
    "confirmation_phrase": "EXECUTE PAPER",
    "approved": True,
    "shariah": {"status": "PASS", "reason": "forged_by_client"},
    "risk": {"status": "PASS", "reason": "forged_by_client"},
    "approval_status": "APPROVED_PAPER_READY",
}


def test_execution_cannot_be_forged_for_a_genuinely_blocked_approval():
    """Ticker absent from any approved publication -> UNKNOWN at /paper/approval
    -> REJECT, stored risk/shariah stay non-PASS. Attempting to execute that
    queue_id with a body claiming approved=true/shariah=PASS/risk=PASS must
    still fail -- those extra fields have no schema field to bind to."""
    _reset_db()
    client = TestClient(app)
    preview = {
        "status": "READY_FOR_APPROVAL",
        "symbol": "9999",
        "side": "BUY",
        "quantity": 1,
        "price": 10.0,
        "notional": 10.0,
        "blockers": [],
        "asset_class": "equity",
        "agent_summary": {
            "shariah": {"status": "PASS", "reason": "forged_by_client"},
            "risk": {"status": "PASS", "reason": "forged_by_client"},
            "quant": {"status": "PASS", "signal": "BUY"},
        },
    }
    approval_response = client.post("/paper/approval", json={"preview": preview, "approved": True})
    assert approval_response.status_code == 200, approval_response.text
    approval_body = approval_response.json()
    assert approval_body["approval"]["status"] == "REJECT", approval_body
    queue_id = approval_body["queue_id"]

    execute_response = client.post(f"/paper/execute/{queue_id}", json=SPOOFED_EXECUTE_BODY)
    assert execute_response.status_code == 200, execute_response.text
    execute_body = execute_response.json()
    assert execute_body["execution_status"] == "NOT_APPROVED", execute_body
    assert execute_body["broker_submission"] is False
    print(
        "PASS: forged approved/shariah/risk fields in the execute request body cannot override "
        "a genuinely REJECTed server-side approval"
    )


def test_execution_of_a_nonexistent_queue_id_fails_even_with_a_forged_body():
    """No approval row exists at all for this id -- there is no server-side
    approval to point to, forged or otherwise, so execution must report
    NOT_FOUND regardless of what the request body claims."""
    _reset_db()
    client = TestClient(app)
    execute_response = client.post("/paper/execute/999999", json=SPOOFED_EXECUTE_BODY)
    assert execute_response.status_code == 200, execute_response.text
    execute_body = execute_response.json()
    assert execute_body["status"] == "NOT_FOUND", execute_body
    assert execute_body["broker_submission"] is False
    print("PASS: executing a queue_id with no genuine server-side approval fails as NOT_FOUND")


def main():
    test_execution_cannot_be_forged_for_a_genuinely_blocked_approval()
    test_execution_of_a_nonexistent_queue_id_fails_even_with_a_forged_body()
    print("\nAll execution-spoofing regression tests passed.")


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
