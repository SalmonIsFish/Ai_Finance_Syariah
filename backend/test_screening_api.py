"""API-level tests for the read-only Malaysian screening/eligibility endpoints
(screening_api.py + the /api/* routes in local_api.py), plus the Part 16
"before frontend" checklist from the Phase 0 integration audit:

  1. compliant approved ticker returns PASS
  2. unknown ticker returns UNKNOWN + BLOCKED
  3. explicit non-compliant ticker returns REJECT + BLOCKED
  4. unapproved publication cannot produce PASS
  5. needs-reconciliation publication cannot produce PASS
  6. quant score never overrides Shariah
  7. risk failure blocks (see test_paper_execution_gates.py / test_portfolio_risk_limits.py
     for the trading-path risk gate; this file covers the read-only /api/risk contract)
  8. account failure blocks (see test_account_shariah_gate.py for the gate itself)
  9. evidence is generated/retrievable
  10. API does not expose internal authority bypasses (see test_approval_shariah_bypass.py
      for the /paper/approval-specific regression this complements)
"""

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
import pytest

_ENV_ORIG = {k: os.environ.get(k) for k in ["SHARIAH_UNIVERSE_PATH", "TRADING_MODE", "PAPER_EXECUTION_ENABLED", "MOOMOO_MODE"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
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

os.environ["MOOMOO_MODE"] = "paper"

from fastapi.testclient import TestClient

import evidence
import local_api
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
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _seed_publication(
    conn: sqlite3.Connection,
    pub_id: str,
    *,
    securities: list[dict],
    status: str = "pending",
    approve: bool = False,
    activate: bool = False,
) -> None:
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": pub_id,
            "publication_date": "2026-01-01",
            "source_document_hash": "test",
            "official_record_count": len(securities),
            "extractable_record_count": len(securities),
            "parsed_record_count": len(securities),
            "parser_version": "test-fixture-v1",
            "human_review_status": status,
        },
    )
    sc_malaysia_store.insert_securities(conn, pub_id, securities)
    if approve:
        result = sc_malaysia_store.approve_publication(conn, pub_id)
        assert result["status"] == "approved", result
    if activate:
        result = sc_malaysia_store.activate_publication(conn, pub_id)
        assert result["status"] == "activated", result


def test_1_compliant_approved_ticker_returns_pass():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-1",
        securities=[
            {"ticker": "1155", "issuer_name": "Test Bank Bhd", "shariah_status": "COMPLIANT"}
        ],
        approve=True,
        activate=True,
    )
    conn.close()

    client = TestClient(app)
    response = client.get("/api/shariah/1155")
    assert response.status_code == 200, response.text
    verdict = response.json()["verdict"]
    assert verdict["status"] == "PASS"
    assert verdict["publication_id"] == "sc-sac-my-test-1"
    print("PASS: 1 — compliant approved+activated ticker returns PASS via /api/shariah/{ticker}")


def test_2_unknown_ticker_returns_unknown_and_blocked():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-2",
        securities=[
            {"ticker": "1155", "issuer_name": "Test Bank Bhd", "shariah_status": "COMPLIANT"}
        ],
        approve=True,
        activate=True,
    )
    conn.close()

    client = TestClient(app)
    verdict = client.get("/api/shariah/9999").json()["verdict"]
    assert verdict["status"] == "UNKNOWN"
    assert verdict["reason"] == "not_present_in_approved_publication"

    screen = client.get("/api/screen/9999").json()
    assert screen["eligibility"]["status"] == "BLOCKED"
    print("PASS: 2 — ticker absent from the active publication returns UNKNOWN + BLOCKED")


def test_3_explicit_non_compliant_returns_reject_and_blocked():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-3",
        securities=[
            {"ticker": "8888", "issuer_name": "Bad Bhd", "shariah_status": "NON_COMPLIANT"}
        ],
        approve=True,
        activate=True,
    )
    conn.close()

    client = TestClient(app)
    verdict = client.get("/api/shariah/8888").json()["verdict"]
    assert verdict["status"] == "REJECT"
    assert verdict["reason"] == "authoritative_non_compliant"

    screen = client.get("/api/screen/8888").json()
    assert screen["eligibility"]["status"] == "BLOCKED"
    print("PASS: 3 — explicitly non-compliant ticker returns REJECT + BLOCKED")


def test_4_unapproved_publication_cannot_produce_pass():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-4",
        securities=[
            {"ticker": "1155", "issuer_name": "Test Bank Bhd", "shariah_status": "COMPLIANT"}
        ],
        status="pending",
        approve=False,
        activate=False,
    )
    conn.close()

    client = TestClient(app)
    verdict = client.get("/api/shariah/1155").json()["verdict"]
    assert verdict["status"] == "UNKNOWN"
    assert verdict["reason"] == "no_approved_publication"

    universe = client.get("/api/universe").json()
    assert universe["active_publication"] is None
    assert universe["count"] == 0
    print("PASS: 4 — an ingested-but-unapproved publication cannot produce PASS")


def test_5_needs_reconciliation_publication_cannot_produce_pass():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-5",
        securities=[
            {"ticker": "1155", "issuer_name": "Test Bank Bhd", "shariah_status": "COMPLIANT"}
        ],
        status="needs_reconciliation",
    )
    approve_result = sc_malaysia_store.approve_publication(conn, "sc-sac-my-test-5")
    assert approve_result["status"] == "error"
    assert approve_result["reason"] == "needs_reconciliation"
    conn.close()

    client = TestClient(app)
    verdict = client.get("/api/shariah/1155").json()["verdict"]
    assert verdict["status"] == "UNKNOWN"
    print("PASS: 5 — a needs_reconciliation publication cannot be approved or produce PASS")


def test_6_quant_score_never_overrides_shariah():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-6",
        securities=[{"ticker": "9999", "issuer_name": "Absent Bhd", "shariah_status": "COMPLIANT"}],
        approve=True,
        activate=True,
    )
    conn.close()

    client = TestClient(app)
    # A DIFFERENT ticker, absent from the active publication -- however
    # attractive its quant signal might be, eligibility must stay BLOCKED.
    screen = client.get("/api/screen/1234").json()
    assert screen["shariah"]["status"] == "UNKNOWN"
    assert screen["eligibility"]["status"] == "BLOCKED"
    assert "attractiveness" in screen["attractiveness"]
    components = screen["attractiveness"]["components"]
    assert "compliance" not in components and "shariah" not in components, (
        "attractiveness must never contain a compliance/shariah component"
    )
    print("PASS: 6 — quant/attractiveness never overrides an UNKNOWN or REJECT Shariah verdict")


def test_7_risk_limits_endpoint_reports_configured_hard_limits():
    client = TestClient(app)
    response = client.get("/api/risk")
    assert response.status_code == 200, response.text
    limits = response.json()["limits"]
    for key in (
        "max_position_pct",
        "max_total_exposure_pct",
        "max_loss_per_trade_pct",
        "max_daily_loss_pct",
        "max_orders_per_day",
        "max_sector_exposure_pct",
    ):
        assert key in limits, f"missing {key}"
    print("PASS: 7 — /api/risk reports every configured hard limit")


def test_8_universe_publication_detail_reports_review_status():
    conn = _reset_db()
    _seed_publication(
        conn,
        "sc-sac-my-test-8",
        securities=[
            {"ticker": "1155", "issuer_name": "A Bhd", "shariah_status": "COMPLIANT"},
            {"ticker": "8888", "issuer_name": "B Bhd", "shariah_status": "NON_COMPLIANT"},
        ],
    )
    conn.close()

    client = TestClient(app)
    detail = client.get("/api/shariah/publication/sc-sac-my-test-8").json()
    assert detail["publication"]["human_review_status"] == "pending"
    assert detail["compliant_count"] == 1
    assert detail["non_compliant_count"] == 1

    missing = client.get("/api/shariah/publication/does-not-exist")
    assert missing.status_code == 404
    print("PASS: 8 — publication detail reports review status; unknown id is a real 404")


def test_9_evidence_is_generated_and_retrievable():
    with tempfile.TemporaryDirectory() as tmp:
        original_dir = evidence.EVIDENCE_DIR
        original_file = evidence.DECISIONS_FILE
        evidence.EVIDENCE_DIR = Path(tmp)
        evidence.DECISIONS_FILE = Path(tmp) / "decisions.jsonl"
        try:
            record = evidence.build_decision_record(
                ticker="1155",
                shariah_result={
                    "status": "PASS",
                    "reason": "authoritative_compliant",
                    "publication_id": "sc-sac-my-test-9",
                    "publication_date": "2026-01-01",
                    "source_document_hash": "test",
                },
                final_decision="APPROVED",
                decision_reason="all_gates_passed",
            )
            evidence.append_decision(record)

            client = TestClient(app)
            response = client.get("/api/evidence/1155")
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["count"] == 1
            assert body["decisions"][0]["shariah"]["publication_id"] == "sc-sac-my-test-9"
        finally:
            evidence.EVIDENCE_DIR = original_dir
            evidence.DECISIONS_FILE = original_file
    print(
        "PASS: 9 — a decision written to the evidence trail is retrievable via /api/evidence/{ticker}"
    )


def test_10_api_exposes_no_authority_bypass():
    """The screening API is entirely GET-based and read-only; there is no
    endpoint here that accepts a client-supplied verdict of any kind. This is
    a structural check that none of the new routes take a body at all."""
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET", "HEAD"}, (
            f"{path} accepts {methods} -- the screening API must stay read-only"
        )
    print("PASS: 10 — every /api/* route is GET-only; no route accepts a client-supplied verdict")


def main():
    test_1_compliant_approved_ticker_returns_pass()
    test_2_unknown_ticker_returns_unknown_and_blocked()
    test_3_explicit_non_compliant_returns_reject_and_blocked()
    test_4_unapproved_publication_cannot_produce_pass()
    test_5_needs_reconciliation_publication_cannot_produce_pass()
    test_6_quant_score_never_overrides_shariah()
    test_7_risk_limits_endpoint_reports_configured_hard_limits()
    test_8_universe_publication_detail_reports_review_status()
    test_9_evidence_is_generated_and_retrievable()
    test_10_api_exposes_no_authority_bypass()
    print("\nAll screening API tests passed.")


if __name__ == "__main__":
    main()
