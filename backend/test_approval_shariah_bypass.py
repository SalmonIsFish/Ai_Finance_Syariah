"""Regression test: /paper/approval must never trust a client-supplied Shariah verdict.

Found during the Phase 0 integration audit: /paper/approval accepted a raw
`preview` dict from the request body and read its Shariah verdict straight out
of that client-controlled JSON, rather than re-deriving it server-side. A
caller could submit a hand-crafted preview claiming
`agent_summary.shariah.status: "PASS"` for a ticker that is actually REJECT or
UNKNOWN, bypassing the SC Malaysia gate entirely -- while /paper/preview
itself, and every unit test of the gate chain, only ever exercised the
legitimate path where the server computed that verdict.

Fixed in local_api.py by adding authoritative_shariah_verdict(symbol), which
/paper/approval now calls instead of reading preview["agent_summary"]["shariah"].
This test proves the fix: a forged PASS in the submitted preview body must not
change the outcome for a ticker the real gate would reject or mark UNKNOWN.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text(
    json.dumps(
        {
            "validation": {"status": "active"},
            "records": [
                {
                    "ticker": "0001",
                    "issuer_name": "Genuinely Compliant Bhd",
                    "shariah_status": "COMPLIANT",
                }
            ],
        }
    ),
    encoding="utf-8",
)
import pytest

_ENV_ORIG = {k: os.environ.get(k) for k in ["SHARIAH_UNIVERSE_PATH", "TRADING_MODE", "PAPER_EXECUTION_ENABLED", "PAPER_EXECUTION_ADAPTER", "MOOMOO_MODE"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
    os.environ["MOOMOO_MODE"] = "paper"
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

from fastapi.testclient import TestClient

import local_api
import sc_malaysia_store
from local_api import app


def _forged_preview(symbol: str, *, price: float = 10.0, quantity: int = 1) -> dict:
    """A preview body a malicious or buggy client could submit directly to
    /paper/approval without ever having gone through a real /paper/preview
    call -- the Shariah verdict inside it is fabricated."""
    notional = round(price * quantity, 2)
    return {
        "status": "READY_FOR_APPROVAL",
        "symbol": symbol,
        "side": "BUY",
        "quantity": quantity,
        "price": price,
        "notional": notional,
        "blockers": [],
        "agent_summary": {
            "shariah": {
                "agent": "shariah",
                "market": "MY",
                "provider": "FORGED_BY_CLIENT",
                "status": "PASS",
                "symbol": symbol,
                "reason": "fabricated_by_test",
            },
            "risk": {"status": "PASS"},
            "quant": {"status": "PASS", "signal": "BUY"},
        },
        "shariah": {
            "status": "PASS",
            "provider": "FORGED_BY_CLIENT",
            "reason": "fabricated_by_test",
        },
        "risk": {"status": "PASS"},
        "account_type": "CASH",
    }


def _seed_approved_publication(db_path: Path) -> None:
    """Give this test its own approved, activated SC publication so the
    'genuine PASS still works' scenario exercises the real authority path
    (sc_malaysia_store) rather than the legacy JSON fallback, which can never
    assert PASS on its own (see shariah_gate.py)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)
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
        [
            {
                "ticker": "0001",
                "issuer_name": "Genuinely Compliant Bhd",
                "shariah_status": "COMPLIANT",
            }
        ],
    )
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test-fixture")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test-fixture")
    conn.close()


def main() -> None:
    db_path = Path(fixture_dir.name) / "paper_trading.db"
    local_api.DB_PATH = db_path
    sc_malaysia_store.DB_PATH = db_path
    _seed_approved_publication(db_path)
    client = TestClient(app)

    forged = _forged_preview("9999")  # absent from the fixture universe -> UNKNOWN
    response = client.post("/paper/approval", json={"preview": forged, "approved": True})
    assert response.status_code == 200, response.text
    approval = response.json()["approval"]
    assert approval["status"] != "APPROVED_PAPER_READY", (
        f"forged Shariah PASS in the request body was honored: {approval}"
    )
    assert approval["status"] == "REJECT"
    assert approval["reason"] == "compliance_not_confirmed"
    print("PASS: forged PASS for an UNKNOWN ticker is rejected — /paper/approval ignores it")

    forged_reject = _forged_preview("9999")
    forged_reject["agent_summary"]["shariah"]["status"] = "REJECT_CLAIMED_AS_PASS_ANYWAY"
    response2 = client.post("/paper/approval", json={"preview": forged_reject, "approved": True})
    approval2 = response2.json()["approval"]
    assert approval2["status"] == "REJECT"
    assert approval2["reason"] == "compliance_not_confirmed"
    print("PASS: outcome is identical regardless of what the client's preview claims")

    genuine = _forged_preview("0001")  # actually COMPLIANT in the fixture universe
    response3 = client.post("/paper/approval", json={"preview": genuine, "approved": True})
    approval3 = response3.json()["approval"]
    assert approval3["status"] in {"APPROVED_PAPER_READY", "PENDING_APPROVAL"}, approval3
    print("PASS: a genuinely compliant ticker still approves — the fix doesn't break the real path")

    print("\nAll approval-endpoint Shariah-bypass regression tests passed.")


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
