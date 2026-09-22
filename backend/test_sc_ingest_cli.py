"""The ingest CLI stages data. It must never make data authoritative.

Ingestion is the one step in the SC pipeline that does not require an
authenticated admin, which is defensible only because what it produces screens
nothing: a `pending` publication is inert until `sc_admin_cli approve` and
`activate` are run by a human with credentials. If ingestion could ever produce
an approved or active publication, that whole separation would be gone, and the
live Malaysian screening authority could be changed by anyone who could run a
script. These tests hold that line.

The PDF parse itself is covered by the sc_pdf_parser tests; the parse is stubbed
here so these stay about the CLI's own behaviour -- the dry-run gate, the
immutability check, and the statuses it is allowed to write.
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import sc_ingest_cli
import sc_malaysia_import
import sc_malaysia_store

PUB_ID = "sc-sac-my-2026-05-29"


def _fake_ingest(connection, pdf_path, **kwargs):
    """Stand in for the real parse, writing through the real store functions."""
    sc_malaysia_store.insert_publication(
        connection,
        {
            "id": PUB_ID,
            "publication_date": kwargs["publication_date"],
            "effective_date": kwargs["publication_date"],
            "source_url": None,
            "source_document_hash": "deadbeef",
            "official_record_count": 2,
            "extractable_record_count": 2,
            "parsed_record_count": 2,
            "parser_version": "test",
            "human_review_status": "pending",
            "human_review_notes": None,
        },
    )
    sc_malaysia_store.insert_securities(
        connection,
        PUB_ID,
        [
            {
                "ticker": "5225",
                "issuer_name": "IHH Healthcare Bhd",
                "shariah_status": "COMPLIANT",
                "board": "MAIN",
                "sector": "HEALTH CARE",
            },
            {
                "ticker": "9999",
                "issuer_name": "Reclassified Bhd",
                "shariah_status": "NON_COMPLIANT",
                "board": None,
                "sector": None,
            },
        ],
    )
    return {
        "status": "ingested",
        "publication_id": PUB_ID,
        "publication_date": kwargs["publication_date"],
        "human_review_status": "pending",
        "reconciliation_notes": None,
        "reconciliation_report": {
            "official_stated_total": 2,
            "unique_ticker_count": 2,
            "parsed_record_count": 2,
            "unresolved_discrepancy": 0,
        },
        "securities_inserted": 2,
        "table2_conflicts_skipped": [],
    }


def _run(db_path, pdf_path, *extra):
    original_argv = sys.argv
    original_ingest = sc_malaysia_import.ingest_pdf_publication
    sc_malaysia_import.ingest_pdf_publication = _fake_ingest
    sys.argv = [
        "sc_ingest_cli.py",
        "--pdf",
        str(pdf_path),
        "--publication-date",
        "2026-05-29",
        "--db",
        str(db_path),
        *extra,
    ]
    try:
        return sc_ingest_cli.main()
    finally:
        sys.argv = original_argv
        sc_malaysia_import.ingest_pdf_publication = original_ingest


def _publication(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return sc_malaysia_store.get_publication(conn, PUB_ID)
    finally:
        conn.close()


def _workspace(tmp):
    pdf = Path(tmp) / "sc.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really parsed, the parse is stubbed")
    return Path(tmp) / "sc.db", pdf


def test_a_dry_run_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        db, pdf = _workspace(tmp)
        assert _run(db, pdf) == 0
        assert _publication(db) is None, "a dry run staged a publication"
    print("PASS: a dry run writes no publication")


def test_apply_stages_as_pending_and_never_activates():
    """The guarantee that lets ingestion run without admin credentials."""
    with tempfile.TemporaryDirectory() as tmp:
        db, pdf = _workspace(tmp)
        assert _run(db, pdf, "--apply") == 0

        pub = _publication(db)
        assert pub is not None, "--apply did not write"
        assert pub["human_review_status"] == "pending", pub["human_review_status"]
        assert pub["activated_at"] is None, "ingestion activated a publication"
        assert pub["approved_at"] is None, "ingestion approved a publication"

        conn = sqlite3.connect(db)
        try:
            active = sc_malaysia_store.get_active_publication(conn)
        finally:
            conn.close()
        assert active is None, "ingestion produced an authoritative publication"
    print("PASS: --apply stages as pending and never approves or activates")


def test_a_staged_publication_screens_nothing():
    """Staged is not merely 'not active' -- it must not answer eligibility.

    The failure this guards against is subtle: if check_eligibility read the
    most recent publication rather than the active one, a staged parse would
    silently become the screening authority the moment it landed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db, pdf = _workspace(tmp)
        _run(db, pdf, "--apply")

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            compliant = sc_malaysia_store.check_eligibility(conn, "5225")
            non_compliant = sc_malaysia_store.check_eligibility(conn, "9999")
        finally:
            conn.close()

        assert compliant["status"] == "UNKNOWN", compliant
        assert non_compliant["status"] == "UNKNOWN", non_compliant
    print("PASS: a staged publication screens nothing, compliant or not")


def test_ingesting_twice_does_not_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        db, pdf = _workspace(tmp)
        assert _run(db, pdf, "--apply") == 0
        assert _run(db, pdf, "--apply") == 0, "a second ingest should be a no-op, not an error"

        conn = sqlite3.connect(db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM sc_security_status WHERE publication_id = ?", (PUB_ID,)
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 2, f"re-ingesting duplicated securities: {count}"
    print("PASS: re-ingesting is a no-op rather than a duplicate")


def _payload(status="pending", pub_id=PUB_ID, version=None):
    return {
        "status": "parsed",
        "payload_version": (version if version is not None else sc_malaysia_import.PAYLOAD_VERSION),
        "publication": {
            "id": pub_id,
            "publication_date": "2026-05-29",
            "effective_date": "2026-05-29",
            "source_url": None,
            "source_document_hash": "deadbeef",
            "official_record_count": 1,
            "extractable_record_count": 1,
            "parsed_record_count": 1,
            "parser_version": "test",
            "human_review_status": status,
            "human_review_notes": None,
        },
        "securities": [
            {
                "ticker": "5225",
                "issuer_name": "IHH Healthcare Bhd",
                "shariah_status": "COMPLIANT",
                "board": "MAIN",
                "sector": "HEALTH CARE",
            }
        ],
        "reconciliation_report": {
            "official_stated_total": 1,
            "unique_ticker_count": 1,
            "parsed_record_count": 1,
            "unresolved_discrepancy": 0,
        },
        "table2_conflicts_skipped": [],
    }


def _run_export(db_path, payload_path, *extra):
    original_argv = sys.argv
    sys.argv = [
        "sc_ingest_cli.py",
        "--from-export",
        str(payload_path),
        "--publication-date",
        "2026-05-29",
        "--db",
        str(db_path),
        *extra,
    ]
    try:
        return sc_ingest_cli.main()
    finally:
        sys.argv = original_argv


def test_an_exported_payload_ingests_without_a_pdf_parser():
    """The whole point of the export route: no pdfplumber on the target host."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sc.db"
        path = Path(tmp) / "p.json"
        path.write_text(json.dumps(_payload()), encoding="utf-8")

        assert _run_export(db, path, "--apply") == 0
        pub = _publication(db)
        assert pub is not None
        assert pub["human_review_status"] == "pending"
        assert pub["source_document_hash"] == "deadbeef", (
            "provenance must stay the PDF's, not the intermediate file's"
        )
    print("PASS: an exported payload ingests with no PDF parser, preserving provenance")


def test_a_needs_reconciliation_payload_is_not_laundered():
    """Moving a parse between machines must not upgrade its status.

    This is the attack the payload route would otherwise open: parse somewhere,
    get needs_reconciliation, carry the file over and have it land as pending
    -- which IS activatable. ingest_payload recomputes nothing for exactly this
    reason, and this test is why that matters.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sc.db"
        path = Path(tmp) / "p.json"
        path.write_text(json.dumps(_payload(status="needs_reconciliation")), encoding="utf-8")

        assert _run_export(db, path, "--apply") == 0
        assert _publication(db)["human_review_status"] == "needs_reconciliation"
    print("PASS: a needs_reconciliation payload stays needs_reconciliation")


def test_a_payload_for_another_date_is_refused():
    """Otherwise one publication's securities file under another's date."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sc.db"
        path = Path(tmp) / "p.json"
        path.write_text(json.dumps(_payload(pub_id="sc-sac-my-2025-11-28")), encoding="utf-8")

        assert _run_export(db, path, "--apply") == 1, "a mismatched payload was accepted"
        assert _publication(db) is None
    print("PASS: a payload naming another publication date is refused")


def test_an_unknown_payload_version_is_refused():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "sc.db"
        path = Path(tmp) / "p.json"
        path.write_text(json.dumps(_payload(version="sc-pdf-payload-v99")), encoding="utf-8")

        assert _run_export(db, path, "--apply") == 1, "an unknown payload version was accepted"
        assert _publication(db) is None
    print("PASS: an unrecognised payload version is refused rather than guessed at")


def test_the_next_steps_hint_names_a_real_admin():
    """A hint that cannot be run as printed is worse than no hint.

    This printed `--username <you>` until 2026-09-22, when the owner copied it
    verbatim mid-activation and had to stop and ask what to substitute.
    """
    import auth

    original = auth._load_configured_users
    try:
        auth._load_configured_users = lambda: {
            "project_owner": {"role": "admin", "password_hash": "x"},
            "a_reviewer": {"role": "reviewer", "password_hash": "x"},
        }
        assert sc_ingest_cli._admin_username() == "project_owner", (
            "must pick the admin, not a reviewer"
        )

        # Fails soft: an unreadable auth config must not take down an ingest
        # that already succeeded. The placeholder is the acceptable outcome.
        def _boom():
            raise RuntimeError("unparseable SC_ADMIN_AUTH_USERS")

        auth._load_configured_users = _boom
        assert sc_ingest_cli._admin_username() == "<your-admin-username>"
    finally:
        auth._load_configured_users = original
    print("PASS: the next-steps hint names the configured admin and fails soft")


def main():
    test_a_dry_run_writes_nothing()
    test_apply_stages_as_pending_and_never_activates()
    test_a_staged_publication_screens_nothing()
    test_ingesting_twice_does_not_duplicate()
    test_an_exported_payload_ingests_without_a_pdf_parser()
    test_a_needs_reconciliation_payload_is_not_laundered()
    test_a_payload_for_another_date_is_refused()
    test_an_unknown_payload_version_is_refused()
    test_the_next_steps_hint_names_a_real_admin()
    print()
    print("All SC ingest CLI tests passed.")


if __name__ == "__main__":
    main()
