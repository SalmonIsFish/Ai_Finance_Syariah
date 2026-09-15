"""Tests for the fail-closed AI Copilot."""

import os
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

# Set up environment before importing backend modules
fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text('{"validation": {"status": "active"}, "records": []}', encoding="utf-8")
import pytest

_ENV_ORIG = {k: os.environ.get(k) for k in ["SHARIAH_UNIVERSE_PATH", "TRADING_MODE", "PAPER_EXECUTION_ENABLED", "PAPER_EXECUTION_ADAPTER", "MOOMOO_MODE", "OPENROUTER_API_KEY"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
    os.environ["MOOMOO_MODE"] = "paper"
    os.environ["OPENROUTER_API_KEY"] = "test-key"
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
os.environ["OPENROUTER_API_KEY"] = "test-key"

import local_api
import sc_malaysia_store
from copilot_api import explain_ticker


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


def _seed_publication(conn: sqlite3.Connection) -> None:
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": "test-pub-1",
            "publication_date": "2026-02-01",
            "source_document_hash": "test",
            "official_record_count": 2,
            "extractable_record_count": 2,
            "parsed_record_count": 2,
            "parser_version": "v1",
            "human_review_status": "pending",
        },
    )
    sc_malaysia_store.insert_securities(
        conn,
        "test-pub-1",
        [
            {"ticker": "1155", "issuer_name": "Test PASS", "shariah_status": "COMPLIANT"},
            {"ticker": "8888", "issuer_name": "Test REJECT", "shariah_status": "NON_COMPLIANT"},
        ],
    )
    sc_malaysia_store.approve_publication(conn, "test-pub-1")
    sc_malaysia_store.activate_publication(conn, "test-pub-1")


@mock.patch("copilot_api.openrouter_request")
def test_1_fail_closed_on_mismatch(mock_openrouter):
    conn = _reset_db()
    _seed_publication(conn)
    conn.close()

    # The backend knows 8888 is REJECT. The LLM hallucinates PASS.
    mock_openrouter.return_value = {
        "ok": True,
        "data": {
            "choices": [
                {
                    "message": {
                        "content": '{"explanation": "hallucinated", "status_check": "PASS"}'
                    }
                }
            ]
        }
    }

    try:
        explain_ticker("8888", "Is it compliant?")
        assert False, "Should have failed closed"
    except ValueError as e:
        assert "SAFETY VIOLATION" in str(e)
        assert "contradicts authoritative status" in str(e)
    
    print("PASS: 1 - Copilot fails closed if LLM contradicts authoritative status")


@mock.patch("copilot_api.openrouter_request")
def test_2_success_on_match(mock_openrouter):
    conn = _reset_db()
    _seed_publication(conn)
    conn.close()

    mock_openrouter.return_value = {
        "ok": True,
        "data": {
            "choices": [
                {
                    "message": {
                        "content": '{"explanation": "Valid explanation.", "status_check": "PASS"}'
                    }
                }
            ]
        }
    }

    res = explain_ticker("1155", "Explain it")
    assert res["authoritative_status"] == "PASS"
    assert res["explanation"] == "Valid explanation."
    
    print("PASS: 2 - Copilot succeeds if LLM explicitly matches the deterministic status")


@mock.patch("copilot_api.openrouter_request")
def test_3_fail_on_missing_fields_or_malformed(mock_openrouter):
    conn = _reset_db()
    _seed_publication(conn)
    conn.close()

    mock_openrouter.return_value = {
        "ok": True,
        "data": {
            "choices": [
                {
                    "message": {
                        "content": '{"just_some": "random_json"}'
                    }
                }
            ]
        }
    }

    try:
        explain_ticker("1155", "Explain")
        assert False, "Should fail on missing explanation field"
    except ValueError as e:
        assert "missing" in str(e) or "SAFETY VIOLATION" in str(e)
        
        
    print("PASS: 3 - Copilot fails on malformed LLM response")


@mock.patch("copilot_api.openrouter_request")
def test_4_research_copilot_success(mock_openrouter):
    conn = _reset_db()
    _seed_publication(conn)
    conn.close()

    mock_openrouter.return_value = {
        "ok": True,
        "data": {
            "choices": [
                {
                    "message": {
                        "content": '{"explanation": "Research found...", "status_check": "PASS", "limitations": ["Not investment advice"]}'
                    }
                }
            ]
        }
    }

    import copilot_api
    res = copilot_api.research_copilot_ticker("1155", "What is the research context?")
    assert res["authoritative_status"] == "PASS"
    assert res["explanation"] == "Research found..."
    assert res["limitations"] == ["Not investment advice"]
    
    print("PASS: 4 - Research Copilot succeeds and parses limitations")


def main():
    test_1_fail_closed_on_mismatch()
    test_2_success_on_match()
    test_3_fail_on_missing_fields_or_malformed()
    test_4_research_copilot_success()

if __name__ == "__main__":
    main()
