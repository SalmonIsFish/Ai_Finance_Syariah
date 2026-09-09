"""Tests for the human approval/activation workflow (Phase 1, Objective 3).

Covers the critical regression the user explicitly called out: a ticker
present in a staged publication must be UNKNOWN/BLOCKED before activation,
UNKNOWN/BLOCKED after approval alone, and only PASS after BOTH approval AND
activation. Also covers every activation-safety condition individually
(each must block with its own machine-readable reason), reject_publication,
deactivate_publication (including that it preserves history and safely
refuses a non-active publication), and that activating a second publication
auditable-y deactivates the first.
"""

import sqlite3

import sc_malaysia_store


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _seed(
    conn: sqlite3.Connection,
    pub_id: str = "sc-sac-my-test",
    *,
    publication_date: str = "2026-01-01",
    securities: list[dict] | None = None,
    official_record_count: int | None = 1,
    extractable_record_count: int | None = 1,
    parsed_record_count: int | None = 1,
    source_document_hash: str | None = "testhash",
    parser_version: str | None = "test-v1",
) -> None:
    securities = (
        securities
        if securities is not None
        else [{"ticker": "1155", "issuer_name": "Test Bank Bhd", "shariah_status": "COMPLIANT"}]
    )
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": pub_id,
            "publication_date": publication_date,
            "source_document_hash": source_document_hash,
            "official_record_count": official_record_count,
            "extractable_record_count": extractable_record_count,
            "parsed_record_count": parsed_record_count,
            "parser_version": parser_version,
        },
    )
    sc_malaysia_store.insert_securities(conn, pub_id, securities)


def test_before_activation_known_compliant_ticker_is_unknown_blocked():
    conn = _conn()
    _seed(conn)
    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN", elig
    assert elig["reason"] == "no_approved_publication"
    print("PASS: before_activation_known_compliant_ticker_is_unknown_blocked")


def test_after_approval_only_still_unknown_blocked():
    conn = _conn()
    _seed(conn)
    result = sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    assert result["status"] == "approved"
    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN", f"approval alone must never be sufficient for PASS: {elig}"
    assert elig["reason"] == "no_approved_publication"
    print("PASS: after_approval_only_still_unknown_blocked — approval alone is not enough")


def test_after_approval_and_activation_pass():
    conn = _conn()
    _seed(conn)
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    activate_result = sc_malaysia_store.activate_publication(
        conn, "sc-sac-my-test", activated_by="Tester"
    )
    assert activate_result["status"] == "activated", activate_result
    assert activate_result["activated_by"] == "Tester"
    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "PASS", elig
    assert elig["publication_id"] == "sc-sac-my-test"
    print(
        "PASS: after_approval_and_activation_pass — both steps together, and only together, produce PASS"
    )


def test_unknown_ticker_absent_from_publication():
    conn = _conn()
    _seed(conn)
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    elig = sc_malaysia_store.check_eligibility(conn, "9999")
    assert elig["status"] == "UNKNOWN", elig
    assert elig["reason"] == "not_present_in_approved_publication"
    print("PASS: unknown_ticker_absent_from_publication")


def test_explicit_non_compliant_is_reject():
    conn = _conn()
    _seed(
        conn,
        securities=[
            {"ticker": "8888", "issuer_name": "Bad Bhd", "shariah_status": "NON_COMPLIANT"}
        ],
    )
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    elig = sc_malaysia_store.check_eligibility(conn, "8888")
    assert elig["status"] == "REJECT", elig
    assert elig["reason"] == "authoritative_non_compliant"
    print("PASS: explicit_non_compliant_is_reject")


def test_activation_blocked_no_security_records():
    conn = _conn()
    _seed(conn, securities=[])
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert result["status"] == "error"
    assert result["reason"] == "no_security_records"
    print("PASS: activation_blocked_no_security_records")


def test_activation_blocked_counts_do_not_reconcile():
    conn = _conn()
    _seed(conn, official_record_count=886, extractable_record_count=688, parsed_record_count=688)
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert result["status"] == "error"
    assert result["reason"] == "record_counts_do_not_reconcile"
    print("PASS: activation_blocked_counts_do_not_reconcile")


def test_activation_blocked_missing_source_hash():
    conn = _conn()
    _seed(conn, source_document_hash=None)
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert result["status"] == "error"
    assert result["reason"] == "source_document_hash_missing"
    print("PASS: activation_blocked_missing_source_hash")


def test_activation_blocked_missing_parser_version():
    conn = _conn()
    _seed(conn, parser_version=None)
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert result["status"] == "error"
    assert result["reason"] == "parser_version_missing"
    print("PASS: activation_blocked_missing_parser_version")


def test_activation_blocked_not_approved():
    conn = _conn()
    _seed(conn)
    result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert result["status"] == "error"
    assert result["reason"] == "publication_not_approved"
    print("PASS: activation_blocked_not_approved")


def test_rejected_publication_cannot_be_approved_or_activated():
    conn = _conn()
    _seed(conn)
    reject_result = sc_malaysia_store.reject_publication(
        conn,
        "sc-sac-my-test",
        reason="Found a transcription error not caught by reconciliation",
        reviewer="Tester",
    )
    assert reject_result["status"] == "rejected"
    assert reject_result["rejected_by"] == "Tester"
    assert reject_result["rejected_at"] is not None
    pub = sc_malaysia_store.get_publication(conn, "sc-sac-my-test")
    assert pub["rejected_by"] == "Tester", "rejection actor must be persisted, not just returned"
    assert pub["rejected_at"] is not None

    approve_result = sc_malaysia_store.approve_publication(
        conn, "sc-sac-my-test", reviewer="Tester"
    )
    assert approve_result["status"] == "error"
    assert approve_result["reason"] == "publication_rejected"

    activate_result = sc_malaysia_store.activate_publication(
        conn, "sc-sac-my-test", activated_by="Tester"
    )
    assert activate_result["status"] == "error"
    assert activate_result["reason"] == "publication_rejected"

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN"
    print("PASS: rejected_publication_cannot_be_approved_or_activated")


def test_deactivate_falls_back_to_unknown_and_preserves_history():
    conn = _conn()
    _seed(conn)
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test", reviewer="Tester")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert sc_malaysia_store.check_eligibility(conn, "1155")["status"] == "PASS"

    result = sc_malaysia_store.deactivate_publication(
        conn, "sc-sac-my-test", reason="Superseded by corrected extraction", deactivated_by="Tester"
    )
    assert result["status"] == "deactivated"

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN", "deactivation must fail closed, not silently keep passing"

    # History is preserved -- not deleted.
    pub = sc_malaysia_store.get_publication(conn, "sc-sac-my-test")
    assert pub is not None
    assert pub["deactivated_at"] is not None
    assert pub["deactivation_reason"] == "Superseded by corrected extraction"
    securities = sc_malaysia_store.publication_securities(conn, "sc-sac-my-test")
    assert len(securities) == 1
    print("PASS: deactivate_falls_back_to_unknown_and_preserves_history")


def test_deactivate_refuses_a_publication_that_is_not_active():
    conn = _conn()
    _seed(conn)
    result = sc_malaysia_store.deactivate_publication(
        conn, "sc-sac-my-test", reason="never activated"
    )
    assert result["status"] == "error"
    assert result["reason"] == "publication_not_currently_active"
    print("PASS: deactivate_refuses_a_publication_that_is_not_active")


def test_activating_second_publication_deactivates_first_auditably():
    conn = _conn()
    _seed(
        conn,
        "sc-sac-my-a",
        publication_date="2026-01-01",
        securities=[{"ticker": "1155", "issuer_name": "A", "shariah_status": "COMPLIANT"}],
    )
    _seed(
        conn,
        "sc-sac-my-b",
        publication_date="2026-02-01",
        securities=[{"ticker": "7084", "issuer_name": "B", "shariah_status": "COMPLIANT"}],
    )
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-a", reviewer="Tester")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-a", activated_by="Tester")
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-b", reviewer="Tester")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-b", activated_by="Tester")

    old = sc_malaysia_store.get_publication(conn, "sc-sac-my-a")
    assert old["deactivated_at"] is not None
    assert old["deactivation_reason"] == "superseded_by:sc-sac-my-b"
    # The superseded publication and its securities are still queryable, not deleted.
    assert sc_malaysia_store.publication_securities(conn, "sc-sac-my-a")
    print("PASS: activating_second_publication_deactivates_first_auditably")


def main():
    test_before_activation_known_compliant_ticker_is_unknown_blocked()
    test_after_approval_only_still_unknown_blocked()
    test_after_approval_and_activation_pass()
    test_unknown_ticker_absent_from_publication()
    test_explicit_non_compliant_is_reject()
    test_activation_blocked_no_security_records()
    test_activation_blocked_counts_do_not_reconcile()
    test_activation_blocked_missing_source_hash()
    test_activation_blocked_missing_parser_version()
    test_activation_blocked_not_approved()
    test_rejected_publication_cannot_be_approved_or_activated()
    test_deactivate_falls_back_to_unknown_and_preserves_history()
    test_deactivate_refuses_a_publication_that_is_not_active()
    test_activating_second_publication_deactivates_first_auditably()
    print("\nAll SC admin workflow tests passed.")


if __name__ == "__main__":
    main()
