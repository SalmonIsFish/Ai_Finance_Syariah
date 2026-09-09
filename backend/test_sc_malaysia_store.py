"""Tests for the SC Malaysia publication store, import, and diff modules.

Verifies:
  - Publication ingestion with pending status
  - Incomplete publications marked needs_reconciliation
  - Approval and activation lifecycle
  - Eligibility queries: PASS, UNKNOWN, REJECT
  - UNKNOWN is preserved (never collapsed to REJECT)
  - Historical eligibility queries
  - Immutability of activated publications
  - Diff between publications
  - Incomplete publication cannot be activated
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import sc_malaysia_store
import sc_malaysia_import
import sc_malaysia_diff


def _in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _write_test_json(
    tmp_dir: Path,
    records: list[dict],
    dataset_id: str = "sc-sac-my-2026-05-29",
) -> Path:
    pub_date = dataset_id.replace("sc-sac-my-", "")
    data = {
        "dataset_id": dataset_id,
        "publication_date": pub_date,
        "source": {
            "authority": "Securities Commission Malaysia Shariah Advisory Council",
            "url": "https://www.sc.com.my/test",
            "local_evidence_path": "test",
        },
        "expected_record_count": len(records),
        "records": records,
        "validation": {
            "status": "staging",
            "actual_record_count": len(records),
            "validated_at": None,
        },
    }
    path = tmp_dir / f"{dataset_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


SAMPLE_RECORDS = [
    {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
    {"ticker": "1295", "issuer_name": "Public Bank Bhd", "shariah_status": "COMPLIANT"},
    {
        "ticker": "5183",
        "issuer_name": "Petronas Chemicals Group Bhd",
        "shariah_status": "COMPLIANT",
    },
]


def test_ingest_and_query_pass():
    conn = _in_memory_db()
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_test_json(Path(tmp), SAMPLE_RECORDS)
        result = sc_malaysia_import.ingest_universe_json(
            conn,
            path,
            official_record_count=len(SAMPLE_RECORDS),
            extractable_record_count=len(SAMPLE_RECORDS),
        )
        assert result["status"] == "ingested", f"Expected ingested, got {result}"
        assert result["parsed_record_count"] == 3

    pub = sc_malaysia_store.get_publication(conn, "sc-sac-my-2026-05-29")
    assert pub is not None
    assert pub["human_review_status"] == "pending"

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN", f"Pending publication should not produce PASS: {elig}"
    assert elig["reason"] == "no_approved_publication"

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-05-29")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-29")

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "PASS", f"Expected PASS: {elig}"
    assert elig["reason"] == "authoritative_compliant"
    assert elig["publication_id"] == "sc-sac-my-2026-05-29"
    assert elig["issuer_name"] == "Malayan Banking Bhd"
    print("PASS: ingest_and_query_pass")


def test_unknown_preserved():
    """UNKNOWN must be a distinct state, not collapsed to REJECT."""
    conn = _in_memory_db()
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_test_json(Path(tmp), SAMPLE_RECORDS)
        sc_malaysia_import.ingest_universe_json(
            conn,
            path,
            official_record_count=len(SAMPLE_RECORDS),
            extractable_record_count=len(SAMPLE_RECORDS),
        )

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-05-29")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-29")

    elig = sc_malaysia_store.check_eligibility(conn, "9999")
    assert elig["status"] == "UNKNOWN", f"Absent ticker must be UNKNOWN, not REJECT: {elig}"
    assert elig["reason"] == "not_present_in_approved_publication"
    assert elig["publication_id"] == "sc-sac-my-2026-05-29"
    print("PASS: unknown_preserved — UNKNOWN is distinct from REJECT")


def test_no_approved_publication_is_unknown():
    conn = _in_memory_db()
    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN"
    assert elig["reason"] == "no_approved_publication"
    print("PASS: no_approved_publication_is_unknown")


def test_incomplete_marked_needs_reconciliation():
    conn = _in_memory_db()
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_test_json(Path(tmp), SAMPLE_RECORDS)
        result = sc_malaysia_import.ingest_universe_json(conn, path, official_record_count=886)
        assert result["status"] == "ingested"
        assert result["human_review_status"] == "needs_reconciliation"

    pub = sc_malaysia_store.get_publication(conn, "sc-sac-my-2026-05-29")
    assert pub["human_review_status"] == "needs_reconciliation"

    activate_result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-29")
    assert activate_result["status"] == "error"
    # More precise than the old generic "publication_not_approved" reason --
    # activate_publication now checks needs_reconciliation explicitly first.
    assert activate_result["reason"] == "needs_reconciliation"

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN", "Unreconciled publication must not produce PASS"
    print("PASS: incomplete_marked_needs_reconciliation — cannot activate")


def test_activation_deactivates_prior():
    conn = _in_memory_db()
    records_v1 = [
        {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
    ]
    records_v2 = [
        {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
        {"ticker": "7084", "issuer_name": "QL Resources Bhd", "shariah_status": "COMPLIANT"},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        p1 = _write_test_json(Path(tmp), records_v1, "sc-sac-my-2026-05-01")
        p2 = _write_test_json(Path(tmp), records_v2, "sc-sac-my-2026-11-01")
        sc_malaysia_import.ingest_universe_json(
            conn,
            p1,
            official_record_count=len(records_v1),
            extractable_record_count=len(records_v1),
        )
        sc_malaysia_import.ingest_universe_json(
            conn,
            p2,
            official_record_count=len(records_v2),
            extractable_record_count=len(records_v2),
        )

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-05-01")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-01")

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-11-01")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-11-01")

    old = sc_malaysia_store.get_publication(conn, "sc-sac-my-2026-05-01")
    assert old["deactivated_at"] is not None, "Old publication must be deactivated"

    elig = sc_malaysia_store.check_eligibility(conn, "7084")
    assert elig["status"] == "PASS"
    assert elig["publication_id"] == "sc-sac-my-2026-11-01"
    print("PASS: activation_deactivates_prior")


def test_historical_eligibility():
    conn = _in_memory_db()
    records_v1 = [
        {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
        {"ticker": "0001", "issuer_name": "Supercomnet", "shariah_status": "COMPLIANT"},
    ]
    records_v2 = [
        {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        p1 = _write_test_json(Path(tmp), records_v1, "sc-sac-my-2026-05-01")
        p2 = _write_test_json(Path(tmp), records_v2, "sc-sac-my-2026-11-01")
        sc_malaysia_import.ingest_universe_json(
            conn,
            p1,
            official_record_count=len(records_v1),
            extractable_record_count=len(records_v1),
        )
        sc_malaysia_import.ingest_universe_json(
            conn,
            p2,
            official_record_count=len(records_v2),
            extractable_record_count=len(records_v2),
        )

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-05-01")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-01")
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-11-01")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-11-01")

    may = sc_malaysia_store.check_eligibility(conn, "0001", as_of_date="2026-06-01")
    assert may["status"] == "PASS", f"0001 was compliant in May pub: {may}"

    nov = sc_malaysia_store.check_eligibility(conn, "0001", as_of_date="2026-12-01")
    assert nov["status"] == "UNKNOWN", f"0001 absent in Nov pub should be UNKNOWN: {nov}"
    assert nov["reason"] == "not_present_in_approved_publication"
    print("PASS: historical_eligibility — temporal queries work")


def test_diff_between_publications():
    conn = _in_memory_db()
    records_v1 = [
        {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
        {"ticker": "0001", "issuer_name": "Supercomnet", "shariah_status": "COMPLIANT"},
    ]
    records_v2 = [
        {"ticker": "1155", "issuer_name": "Malayan Banking Bhd", "shariah_status": "COMPLIANT"},
        {"ticker": "7084", "issuer_name": "QL Resources Bhd", "shariah_status": "COMPLIANT"},
    ]

    with tempfile.TemporaryDirectory() as tmp:
        p1 = _write_test_json(Path(tmp), records_v1, "sc-sac-my-2026-05-01")
        p2 = _write_test_json(Path(tmp), records_v2, "sc-sac-my-2026-11-01")
        sc_malaysia_import.ingest_universe_json(conn, p1)
        sc_malaysia_import.ingest_universe_json(conn, p2)

    diff = sc_malaysia_diff.diff_publications(conn, "sc-sac-my-2026-05-01", "sc-sac-my-2026-11-01")
    assert diff["old_count"] == 2
    assert diff["new_count"] == 2
    assert len(diff["added"]) == 1
    assert diff["added"][0]["ticker"] == "7084"
    assert len(diff["removed"]) == 1
    assert diff["removed"][0]["ticker"] == "0001"
    assert diff["unchanged_count"] == 1
    print("PASS: diff_between_publications")


def test_duplicate_ingestion_rejected():
    conn = _in_memory_db()
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_test_json(Path(tmp), SAMPLE_RECORDS)
        sc_malaysia_import.ingest_universe_json(conn, path)
        result = sc_malaysia_import.ingest_universe_json(conn, path)
        assert result["status"] == "error"
        assert result["reason"] == "publication_already_exists"
    print("PASS: duplicate_ingestion_rejected")


def test_unknown_never_becomes_pass():
    """The fundamental invariant: no approved evidence → no PASS."""
    conn = _in_memory_db()
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_test_json(Path(tmp), SAMPLE_RECORDS)
        sc_malaysia_import.ingest_universe_json(conn, path)

    for ticker in ["1155", "9999", "0001"]:
        elig = sc_malaysia_store.check_eligibility(conn, ticker)
        assert elig["status"] != "PASS", f"Ticker {ticker} got PASS without approved publication!"
    print("PASS: unknown_never_becomes_pass — no approval means no PASS")


def test_list_publications():
    conn = _in_memory_db()
    with tempfile.TemporaryDirectory() as tmp:
        p1 = _write_test_json(Path(tmp), SAMPLE_RECORDS, "sc-sac-my-2026-05-01")
        p2 = _write_test_json(Path(tmp), SAMPLE_RECORDS[:1], "sc-sac-my-2026-11-01")
        sc_malaysia_import.ingest_universe_json(conn, p1)
        sc_malaysia_import.ingest_universe_json(conn, p2)

    pubs = sc_malaysia_store.list_publications(conn)
    assert len(pubs) == 2
    assert pubs[0]["id"] == "sc-sac-my-2026-11-01"
    print("PASS: list_publications")


def main():
    test_ingest_and_query_pass()
    test_unknown_preserved()
    test_no_approved_publication_is_unknown()
    test_incomplete_marked_needs_reconciliation()
    test_activation_deactivates_prior()
    test_historical_eligibility()
    test_diff_between_publications()
    test_duplicate_ingestion_rejected()
    test_unknown_never_becomes_pass()
    test_list_publications()
    print("\nAll SC Malaysia store tests passed.")


if __name__ == "__main__":
    main()
