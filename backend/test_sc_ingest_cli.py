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


def main():
    test_a_dry_run_writes_nothing()
    test_apply_stages_as_pending_and_never_activates()
    test_a_staged_publication_screens_nothing()
    test_ingesting_twice_does_not_duplicate()
    print()
    print("All SC ingest CLI tests passed.")


if __name__ == "__main__":
    main()
