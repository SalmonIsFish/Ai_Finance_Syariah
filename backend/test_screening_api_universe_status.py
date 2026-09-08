"""Tests for the additive shariah_status filter on GET /api/universe.

Covers the Phase 2B dashboard requirement that bulk REJECT securities be
visible/filterable without breaking the existing COMPLIANT-only default
that other callers (including test_screening_api.py) depend on.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text('{"validation": {"status": "active"}, "records": []}', encoding="utf-8")
os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"

from fastapi.testclient import TestClient

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


def _seed_mixed_publication(conn: sqlite3.Connection) -> None:
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": "sc-sac-my-test-mixed",
            "publication_date": "2026-02-01",
            "source_document_hash": "test",
            "official_record_count": 3,
            "extractable_record_count": 3,
            "parsed_record_count": 3,
            "parser_version": "test-fixture-v1",
            "human_review_status": "pending",
        },
    )
    sc_malaysia_store.insert_securities(
        conn,
        "sc-sac-my-test-mixed",
        [
            {"ticker": "1155", "issuer_name": "Compliant Bank Bhd", "shariah_status": "COMPLIANT"},
            {"ticker": "1295", "issuer_name": "Compliant Bhd Two", "shariah_status": "COMPLIANT"},
            {
                "ticker": "8888",
                "issuer_name": "Non Compliant Bhd",
                "shariah_status": "NON_COMPLIANT",
            },
        ],
    )
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test-mixed")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test-mixed")


def test_1_default_omitted_param_matches_original_compliant_only_behavior():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    response = client.get("/api/universe")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert {s["ticker"] for s in body["securities"]} == {"1155", "1295"}
    assert all(s["verdict"] == "PASS" for s in body["securities"])
    print("PASS: 1 — omitting shariah_status preserves the original COMPLIANT-only default")


def test_2_explicit_pass_filter_matches_default():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=PASS").json()
    assert body["count"] == 2
    assert all(s["verdict"] == "PASS" for s in body["securities"])
    print("PASS: 2 — explicit shariah_status=PASS matches the default")


def test_3_reject_filter_surfaces_non_compliant_rows():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=REJECT").json()
    assert body["count"] == 1
    assert body["securities"][0]["ticker"] == "8888"
    assert body["securities"][0]["verdict"] == "REJECT"
    print("PASS: 3 — shariah_status=REJECT surfaces the previously-hidden NON_COMPLIANT row")


def test_4_all_filter_returns_every_row():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=ALL").json()
    assert body["count"] == 3
    tickers = {s["ticker"] for s in body["securities"]}
    assert tickers == {"1155", "1295", "8888"}
    print("PASS: 4 — shariah_status=ALL returns both compliant and non-compliant rows")


def test_5_case_insensitive():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=reject").json()
    assert body["count"] == 1
    print("PASS: 5 — shariah_status matching is case-insensitive")


def test_6_invalid_value_fails_predictably():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    response = client.get("/api/universe?shariah_status=MAYBE")
    assert response.status_code == 400, response.text
    print("PASS: 6 — an invalid shariah_status value returns HTTP 400, not a silent fallback")


def test_7_no_active_publication_stays_empty_regardless_of_filter():
    conn = _reset_db()
    conn.close()

    client = TestClient(app)
    for status in ("PASS", "REJECT", "ALL"):
        body = client.get(f"/api/universe?shariah_status={status}").json()
        assert body["active_publication"] is None
        assert body["count"] == 0
        assert body["securities"] == []
    print("PASS: 7 — no active publication returns the explicit empty shape for every filter value")


def test_8_screen_ticker_endpoint_unchanged():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    response = client.get("/api/screen/1155")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shariah"]["status"] == "PASS"
    assert "eligibility" in body
    print("PASS: 8 — /api/screen/{ticker} is unaffected by the universe filter change")


def test_9_no_mutation_route_introduced():
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET", "HEAD"}, f"{path} accepts {methods} -- must stay read-only"
    print("PASS: 9 — every /api/* route remains GET-only after this change")


def main():
    test_1_default_omitted_param_matches_original_compliant_only_behavior()
    test_2_explicit_pass_filter_matches_default()
    test_3_reject_filter_surfaces_non_compliant_rows()
    test_4_all_filter_returns_every_row()
    test_5_case_insensitive()
    test_6_invalid_value_fails_predictably()
    test_7_no_active_publication_stays_empty_regardless_of_filter()
    test_8_screen_ticker_endpoint_unchanged()
    test_9_no_mutation_route_introduced()
    print("\nAll universe shariah_status filter tests passed.")


if __name__ == "__main__":
    main()
