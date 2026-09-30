"""The rows stored for an SC publication must reconcile with what it recorded.

official/extractable/parsed cover SC's main compliant list only. The stored
rows also hold the additional-instruments list and Table 2's reclassifications
to non-compliant, so `sc-sac-my-2026-05-29` has 905 rows against counts of 886.
Before this, nothing compared the two: a row dropped at insert, or added or
removed later, left activation passing. These tests pin that it no longer does.
"""

import sqlite3

import pytest

import sc_malaysia_import
import sc_malaysia_store
import sc_pdf_parser as p
from bridge.format import render_publications
from test_sc_pdf_parser import REAL_PDF_PATH, _synthetic_result


PUB_ID = "sc-sac-my-2099-05-01"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(connection)
    yield connection
    connection.close()


def _stage(conn, *, additional=1, reclassified=1, **overrides):
    """A clean publication: 3 main-list rows plus the extra sources, approved."""
    pub = {
        "id": PUB_ID,
        "publication_date": "2099-05-01",
        "source_document_hash": "fixture-hash",
        "official_record_count": 3,
        "extractable_record_count": 3,
        "parsed_record_count": 3,
        "parser_version": "fixture",
        "additional_instrument_row_count": additional,
        "reclassified_row_count": reclassified,
        **overrides,
    }
    sc_malaysia_store.insert_publication(conn, pub)
    securities = [
        {"ticker": t, "shariah_status": "COMPLIANT", "board": "MAIN MARKET"}
        for t in ("1001", "1002", "1003")
    ]
    securities += [
        {"ticker": f"20{i:02d}", "shariah_status": "COMPLIANT", "board": "OTHER_INSTRUMENT"}
        for i in range(additional)
    ]
    securities += [
        {"ticker": f"30{i:02d}", "shariah_status": "NON_COMPLIANT", "board": None}
        for i in range(reclassified)
    ]
    sc_malaysia_store.insert_securities(conn, PUB_ID, securities)
    assert sc_malaysia_store.approve_publication(conn, PUB_ID)["status"] == "approved"


def _pub(conn):
    return sc_malaysia_store.get_publication(conn, PUB_ID)


def test_reconciled_rows_activate(conn):
    _stage(conn)
    rows = sc_malaysia_store.security_row_breakdown(conn, _pub(conn))
    assert rows["total_rows"] == 5
    assert rows["expected_rows"] == 5
    assert rows["other_instrument_rows"] == 1
    assert rows["non_compliant_rows"] == 1
    assert rows["recorded"] is True
    assert rows["reconciles"] is True
    assert sc_malaysia_store.activate_publication(conn, PUB_ID)["status"] == "activated"


def test_dropped_row_refuses_activation(conn):
    _stage(conn)
    conn.execute(
        "DELETE FROM sc_security_status WHERE publication_id = ? AND ticker = '1002'", (PUB_ID,)
    )
    result = sc_malaysia_store.activate_publication(conn, PUB_ID)
    assert result["status"] == "error"
    assert result["reason"] == "security_rows_do_not_reconcile"
    assert result["security_rows"]["total_rows"] == 4
    assert result["security_rows"]["expected_rows"] == 5
    assert _pub(conn)["activated_at"] is None


def test_extra_row_refuses_activation(conn):
    _stage(conn)
    sc_malaysia_store.insert_securities(
        conn, PUB_ID, [{"ticker": "9999", "shariah_status": "COMPLIANT", "board": "MAIN MARKET"}]
    )
    result = sc_malaysia_store.activate_publication(conn, PUB_ID)
    assert result["reason"] == "security_rows_do_not_reconcile"


def test_other_instrument_count_mismatch_refuses_even_when_total_matches(conn):
    _stage(conn)
    conn.execute(
        "UPDATE sc_security_status SET board = 'MAIN MARKET' "
        "WHERE publication_id = ? AND board = 'OTHER_INSTRUMENT'",
        (PUB_ID,),
    )
    rows = sc_malaysia_store.security_row_breakdown(conn, _pub(conn))
    assert rows["total_rows"] == rows["expected_rows"]
    result = sc_malaysia_store.activate_publication(conn, PUB_ID)
    assert result["reason"] == "security_rows_do_not_reconcile"


def test_legacy_publication_without_counts_is_refused(conn):
    """A publication ingested before the columns existed has NULLs. Its rows
    are never inferred to reconcile -- that would check them against themselves."""
    _stage(conn)
    conn.execute(
        "UPDATE sc_publications SET additional_instrument_row_count = NULL, "
        "reclassified_row_count = NULL WHERE id = ?",
        (PUB_ID,),
    )
    rows = sc_malaysia_store.security_row_breakdown(conn, _pub(conn))
    assert rows["recorded"] is False
    assert rows["reconciles"] is False
    assert rows["expected_rows"] is None
    assert rows["total_rows"] == 5
    result = sc_malaysia_store.activate_publication(conn, PUB_ID)
    assert result["reason"] == "security_row_breakdown_not_recorded"


def test_explicit_none_is_not_recorded(conn):
    _stage(conn, reclassified=0, reclassified_row_count=None)
    result = sc_malaysia_store.activate_publication(conn, PUB_ID)
    assert result["reason"] == "security_row_breakdown_not_recorded"


def test_absent_counts_default_to_main_list_only(conn):
    """Absent keys store 0: a strict claim that there are no extra rows, which
    extra rows then contradict."""
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": PUB_ID,
            "publication_date": "2099-05-01",
            "parsed_record_count": 1,
        },
    )
    pub = _pub(conn)
    assert pub["additional_instrument_row_count"] == 0
    assert pub["reclassified_row_count"] == 0
    sc_malaysia_store.insert_securities(
        conn,
        PUB_ID,
        [
            {"ticker": "1001", "shariah_status": "COMPLIANT"},
            {"ticker": "3001", "shariah_status": "NON_COMPLIANT"},
        ],
    )
    assert sc_malaysia_store.security_row_breakdown(conn, pub)["reconciles"] is False


def test_v1_payload_is_refused(conn):
    result = sc_malaysia_import.ingest_payload(
        conn, {"payload_version": "sc-pdf-payload-v1", "publication": {}, "securities": []}
    )
    assert result["reason"] == "unsupported_payload_version"
    assert result["expected"] == "sc-pdf-payload-v2"


def test_payload_counts_rows_actually_staged(monkeypatch, conn):
    """Counts come from the rows appended, after dedup and Table 2 conflict
    skips -- not from how many entries SC printed."""
    result = _synthetic_result()
    result.additional_instruments = [
        p.ChangeRecord(ticker="5001", issuer_name="Extra Instrument", source_page=20),
        p.ChangeRecord(ticker="1155", issuer_name="Already main list", source_page=20),
    ]
    result.table2_newly_non_compliant = [
        p.ChangeRecord(ticker="6001", issuer_name="Reclassified A", source_page=19),
        p.ChangeRecord(ticker="6002", issuer_name="Reclassified B", source_page=19),
        p.ChangeRecord(ticker="1295", issuer_name="Conflict", source_page=19),
    ]
    monkeypatch.setattr(p, "parse_pdf", lambda path: result)

    payload = sc_malaysia_import.build_pdf_payload(__file__, publication_date="2099-05-01")
    pub = payload["publication"]
    assert payload["payload_version"] == "sc-pdf-payload-v2"
    assert pub["additional_instrument_row_count"] == 1
    assert pub["reclassified_row_count"] == 2
    assert payload["table2_conflicts_skipped"] == ["1295"]
    assert len(payload["securities"]) == 2 + 1 + 2

    assert sc_malaysia_import.ingest_payload(conn, payload)["status"] == "ingested"
    sc_malaysia_store.approve_publication(conn, PUB_ID)
    assert sc_malaysia_store.activate_publication(conn, PUB_ID)["status"] == "activated"


def test_json_import_records_main_list_only(tmp_path, conn):
    path = tmp_path / "u.json"
    path.write_text(
        '{"dataset_id": "%s", "publication_date": "2099-05-01", "records": '
        '[{"ticker": "1001", "shariah_status": "COMPLIANT"}]}' % PUB_ID,
        encoding="utf-8",
    )
    sc_malaysia_import.ingest_universe_json(conn, path)
    rows = sc_malaysia_store.security_row_breakdown(conn, _pub(conn))
    assert rows["recorded"] is True
    assert rows["reconciles"] is True


def test_bridge_renders_reconciled_breakdown(conn):
    _stage(conn)
    pub = _pub(conn)
    text = render_publications(
        {
            "publications": [
                {**pub, "security_rows": sc_malaysia_store.security_row_breakdown(conn, pub)}
            ]
        }
    )
    assert "5: 3 main list + 1 additional + 1 reclassified -- reconciled" in text
    assert "Main list" in text
    assert "3 parsed of 3 official" in text


def test_bridge_describes_legacy_rows_without_claiming_reconciliation(conn):
    _stage(conn)
    conn.execute(
        "UPDATE sc_publications SET additional_instrument_row_count = NULL, "
        "reclassified_row_count = NULL WHERE id = ?",
        (PUB_ID,),
    )
    pub = _pub(conn)
    text = render_publications(
        {
            "publications": [
                {**pub, "security_rows": sc_malaysia_store.security_row_breakdown(conn, pub)}
            ]
        }
    )
    assert "5 (3 main list, 1 other-instrument, 1 non-compliant)" in text
    assert "breakdown not recorded at ingest" in text
    assert "reconciled" not in text


def test_bridge_tolerates_api_without_security_rows():
    """The relay may talk to a deployed API that predates this field."""
    text = render_publications(
        {"publications": [{"id": PUB_ID, "official_record_count": 886, "parsed_record_count": 886}]}
    )
    assert "886 parsed of 886 official" in text
    assert "Rows" not in text


@pytest.mark.skipif(not REAL_PDF_PATH.exists(), reason="real SC PDF not available")
def test_real_pdf_payload_accounts_for_all_905_rows():
    payload = sc_malaysia_import.build_pdf_payload(REAL_PDF_PATH, publication_date="2026-05-29")
    pub = payload["publication"]
    assert pub["parsed_record_count"] == 886
    assert pub["additional_instrument_row_count"] == 1
    assert pub["reclassified_row_count"] == 18
    assert payload["table2_conflicts_skipped"] == []
    assert len(payload["securities"]) == 905
