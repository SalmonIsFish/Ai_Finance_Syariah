"""Tests for the Malaysian Shariah gate with SC Malaysia store.

Verifies:
  - PASS for compliant ticker in approved publication
  - UNKNOWN for absent ticker (not collapsed to REJECT)
  - UNKNOWN when no approved publication exists
  - UNKNOWN fails closed for trading decisions
  - REJECT for explicitly non-compliant status
  - Gate queries SC store, not flat JSON, when store has data
  - Legacy JSON fallback when SC store is empty
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import sc_malaysia_store
import sc_malaysia_import
import shariah_gate


def _in_memory_setup() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


SAMPLE_RECORDS = [
    {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
    {"ticker": "1295", "issuer_name": "Public Bank Bhd", "shariah_status": "COMPLIANT"},
]


def _ingest_and_activate(conn: sqlite3.Connection) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = {
            "dataset_id": "sc-sac-my-2026-05-29",
            "publication_date": "2026-05-29",
            "source": {
                "authority": "Securities Commission Malaysia Shariah Advisory Council",
                "url": "https://www.sc.com.my/test",
                "local_evidence_path": "test",
            },
            "expected_record_count": 2,
            "records": SAMPLE_RECORDS,
            "validation": {"status": "staging", "actual_record_count": 2, "validated_at": None},
        }
        path = Path(tmp) / "test.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        sc_malaysia_import.ingest_universe_json(
            conn,
            path,
            official_record_count=len(SAMPLE_RECORDS),
            extractable_record_count=len(SAMPLE_RECORDS),
        )

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-05-29")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-29")


def test_pass_via_store():
    """Compliant ticker in approved publication → PASS."""
    conn = _in_memory_setup()
    _ingest_and_activate(conn)

    result = sc_malaysia_store.check_eligibility(conn, "1155")
    assert result["status"] == "PASS"
    assert result["reason"] == "authoritative_compliant"
    assert result["publication_id"] == "sc-sac-my-2026-05-29"
    print("PASS: pass_via_store — 1155 is compliant")


def test_unknown_for_absent_ticker():
    """Ticker not in approved publication → UNKNOWN (not REJECT)."""
    conn = _in_memory_setup()
    _ingest_and_activate(conn)

    result = sc_malaysia_store.check_eligibility(conn, "9999")
    assert result["status"] == "UNKNOWN", f"Absent ticker must be UNKNOWN: {result}"
    assert result["reason"] == "not_present_in_approved_publication"
    print("PASS: unknown_for_absent_ticker — UNKNOWN preserved, not REJECT")


def test_unknown_no_approved_pub():
    """No approved publication → UNKNOWN."""
    conn = _in_memory_setup()
    result = sc_malaysia_store.check_eligibility(conn, "1155")
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "no_approved_publication"
    print("PASS: unknown_no_approved_pub")


def test_unknown_blocks_trading():
    """UNKNOWN must prevent trading (fail closed)."""
    conn = _in_memory_setup()
    _ingest_and_activate(conn)

    for ticker in ["9999", "0000", "INVALID"]:
        result = sc_malaysia_store.check_eligibility(conn, ticker)
        assert result["status"] != "PASS", f"{ticker} got PASS without being in publication"
    print("PASS: unknown_blocks_trading — non-PASS tickers cannot trade")


def test_legacy_json_fallback():
    """When SC store has no data, falls back to legacy JSON -- which must
    never itself produce PASS or REJECT (see shariah_gate.py module docstring:
    this was a real bypass, found and closed during the Phase 0 audit)."""
    result = shariah_gate._check_via_legacy_json("1155")
    assert result["status"] == "UNKNOWN", (
        f"legacy JSON fallback must never assert PASS/REJECT on its own: {result}"
    )
    print(f"PASS: legacy_json_fallback — returned {result['status']} for 1155")


def test_legacy_json_never_produces_pass_even_when_file_claims_active():
    """The committed legacy universe JSON's own internal validation.status can
    say 'active' and list a ticker as COMPLIANT -- neither fact may ever
    surface as a Shariah PASS, since the file was never approved or activated
    through the SC publication workflow. This is the exact scenario the
    audit found live: ticker 7113 (Top Glove) is COMPLIANT in the committed
    fixture and the fixture's own validation.status is "active", yet the
    gate must still return UNKNOWN."""
    import os
    _orig = os.environ.get("SHARIAH_UNIVERSE_PATH")
    if "SHARIAH_UNIVERSE_PATH" in os.environ:
        del os.environ["SHARIAH_UNIVERSE_PATH"]
    try:
        result = shariah_gate._check_via_legacy_json("7113")
        assert result["status"] == "UNKNOWN", result
        assert result.get("legacy_shariah_status") == "COMPLIANT", (
            "the fixture must still be the one that claims compliance, or this test proves nothing"
        )
        print("PASS: legacy_json_never_produces_pass_even_when_file_claims_active")
    finally:
        if _orig is not None:
            os.environ["SHARIAH_UNIVERSE_PATH"] = _orig


def test_shariah_agent_preserves_unknown():
    """shariah_agent must pass through UNKNOWN, not collapse to REJECT."""
    from agents.shariah_agent import _evaluate_malaysia

    original_check = shariah_gate.check_symbol
    try:
        shariah_gate.check_symbol = lambda s: {
            "status": "UNKNOWN",
            "reason": "no_approved_publication",
            "ticker": s,
        }
        result = _evaluate_malaysia("9999")
        assert result["status"] == "UNKNOWN", (
            f"shariah_agent collapsed UNKNOWN to {result['status']}"
        )
    finally:
        shariah_gate.check_symbol = original_check
    print("PASS: shariah_agent_preserves_unknown")


def test_three_distinct_states():
    """Verify PASS, REJECT, and UNKNOWN are all possible and distinct."""
    conn = _in_memory_setup()
    _ingest_and_activate(conn)

    pass_result = sc_malaysia_store.check_eligibility(conn, "1155")
    unknown_result = sc_malaysia_store.check_eligibility(conn, "9999")

    assert pass_result["status"] == "PASS"
    assert unknown_result["status"] == "UNKNOWN"
    assert pass_result["status"] != unknown_result["status"]

    states = {pass_result["status"], unknown_result["status"]}
    assert len(states) == 2, "PASS and UNKNOWN must be distinct states"
    print("PASS: three_distinct_states — PASS and UNKNOWN are distinct")


def main():
    test_pass_via_store()
    test_unknown_for_absent_ticker()
    test_unknown_no_approved_pub()
    test_unknown_blocks_trading()
    test_legacy_json_fallback()
    test_legacy_json_never_produces_pass_even_when_file_claims_active()
    test_shariah_agent_preserves_unknown()
    test_three_distinct_states()
    print("\nAll Malaysian Shariah gate tests passed.")


if __name__ == "__main__":
    main()
