"""Tests for the SC Malaysia PDF parser (sc_pdf_parser.py) and its ingestion path.

Two kinds of coverage:
  - Fixture-based unit tests against RawWord lists and synthetic ParseResults,
    which run with no PDF file and cover row reconstruction, header detection,
    the acronym-name false-positive guard, duplicate/invalid detection, and
    numbering-anomaly detection deterministically.
  - An integration test against the real SC Malaysia May 2026 PDF, skipped
    (not failed) when the file isn't available locally, which is the
    authoritative check that the real 886-record publication reconciles
    exactly against its own Table 3 sector totals.
"""

from pathlib import Path

import sc_malaysia_import
import sc_malaysia_store
import sc_pdf_parser as p


REAL_PDF_PATH = Path(
    r"E:\Projects Stuff\Multi_Ai_IslamicFinance\00-Raw-Books-PDF\Shariah-compliant-May 2026_final.pdf"
)


def _words(*rows: tuple[str, float, float]) -> list[p.RawWord]:
    """Build a RawWord list from (text, x0, top) tuples, already in reading order."""
    return [p.RawWord(text, x0, top) for text, x0, top in rows]


def _fresh_state(market: str = "MAIN MARKET") -> p._ColumnState:
    return p._ColumnState(
        market=market,
        sector_index=-1,
        current_sector=None,
        current_sector_source="order_inference",
        prev_entry_no=None,
    )


def test_row_reconstruction_simple():
    """A single-line row parses into ticker + issuer name."""
    words = _words(
        ("1.", 42.0, 100.0),
        ("7086", 70.0, 100.0),
        ("Ablegroup", 112.0, 100.0),
        ("Bhd", 150.0, 100.0),
    )
    state = _fresh_state()
    records = p._scan_column(words, state, page_num=21, anomalies=[])
    assert len(records) == 1
    assert records[0].ticker == "7086"
    assert records[0].issuer_name == "Ablegroup Bhd"
    assert records[0].entry_no == 1
    print("PASS: row_reconstruction_simple")


def test_row_reconstruction_wrapped_name():
    """A name wrapping across multiple physical lines reconstructs as one string,
    which is exactly what the prior regex-on-linear-text parser could not do
    reliably and is why it lost 196 of 886 records."""
    words = _words(
        ("3.", 42.0, 100.0),
        ("0122", 70.0, 100.0),
        ("Advance", 112.0, 100.0),
        ("Information", 112.0, 112.0),
        ("Marketing", 112.0, 124.0),
        ("Bhd", 112.0, 124.0),
    )
    state = _fresh_state()
    records = p._scan_column(words, state, page_num=21, anomalies=[])
    assert len(records) == 1
    assert records[0].issuer_name == "Advance Information Marketing Bhd"
    print("PASS: row_reconstruction_wrapped_name")


def test_two_column_extraction():
    """A left/right two-column page reconstructs both columns' rows correctly."""
    words = _words(
        ("1.", 42.0, 100.0),
        ("7086", 70.0, 100.0),
        ("Ablegroup", 112.0, 100.0),
        ("Bhd", 150.0, 100.0),
        ("2.", 42.0, 112.0),
        ("5198", 70.0, 112.0),
        ("ABM", 112.0, 112.0),
        ("Fujiya", 140.0, 112.0),
        ("Bhd", 165.0, 112.0),
    )
    state = _fresh_state()
    records = p._scan_column(words, state, page_num=21, anomalies=[])
    assert [r.ticker for r in records] == ["7086", "5198"]
    assert records[1].issuer_name == "ABM Fujiya Bhd"
    print("PASS: two_column_extraction")


def test_header_text_match_takes_priority():
    """A row preceded by a recognizable sector header is labeled from that
    header text, not from order-based inference."""
    words = _words(
        ("PROPERTY", 42.0, 200.0),
        ("HARTANAH", 42.0, 212.0),
        ("No.", 42.0, 224.0),
        ("Stock", 70.0, 224.0),
        ("code", 100.0, 224.0),
        ("Name", 130.0, 224.0),
        ("of", 160.0, 224.0),
        ("securities", 175.0, 224.0),
        ("1.", 42.0, 240.0),
        ("7131", 70.0, 240.0),
        ("ACME", 112.0, 240.0),
        ("Holdings", 140.0, 240.0),
        ("Bhd", 175.0, 240.0),
    )
    state = _fresh_state()
    records = p._scan_column(words, state, page_num=27, anomalies=[])
    assert records[0].sector == "Property"
    assert records[0].sector_source == "header_text_match"
    print("PASS: header_text_match_takes_priority")


def test_acronym_heavy_name_not_mistaken_for_header():
    """A genuine issuer name with two consecutive ALL-CAPS acronym words (e.g.
    'PNE PCB Bhd', 'IFCA MSC Bhd') must not be misfiled as a section header --
    only a run of ALL-CAPS words immediately followed by the literal 'No.'
    column-label anchor is treated as a header."""
    words = _words(
        ("131.", 42.0, 300.0),
        ("6637", 70.0, 300.0),
        ("PNE", 112.0, 300.0),
        ("PCB", 140.0, 300.0),
        ("Bhd", 165.0, 300.0),
        ("132.", 42.0, 312.0),
        ("8869", 70.0, 312.0),
        ("Press", 112.0, 312.0),
        ("Metal", 140.0, 312.0),
        ("Aluminium", 165.0, 312.0),
        ("Holdings", 200.0, 312.0),
    )
    state = _fresh_state()
    state.current_sector = "Industrial products and services"
    state.current_sector_source = "header_text_match"
    state.prev_entry_no = 130
    records = p._scan_column(words, state, page_num=23, anomalies=[])
    assert records[0].issuer_name == "PNE PCB Bhd", records[0].issuer_name
    assert records[0].sector == "Industrial products and services"
    print("PASS: acronym_heavy_name_not_mistaken_for_header")


def test_header_after_acronym_name_still_detected():
    """A genuine header appearing right after an acronym-heavy name is still
    found via the 'No.' anchor, not swallowed into the preceding name."""
    words = _words(
        ("14.", 42.0, 100.0),  # continuation, not a fresh reset -- matches the
        ("0023", 70.0, 100.0),  # real document (page 35: "14. 0023 IFCA MSC Bhd")
        ("IFCA", 112.0, 100.0),
        ("MSC", 140.0, 100.0),
        ("Bhd", 165.0, 100.0),
        ("TECHNOLOGY", 42.0, 112.0),
        ("TEKNOLOGI", 42.0, 124.0),
        ("No.", 42.0, 136.0),
        ("Stock", 70.0, 136.0),
        ("code", 100.0, 136.0),
        ("1.", 42.0, 148.0),
        ("0181", 70.0, 148.0),
        ("Aemulus", 112.0, 148.0),
        ("Holdings", 150.0, 148.0),
        ("Bhd", 190.0, 148.0),
    )
    state = _fresh_state()
    state.current_sector = "Property"
    state.current_sector_source = "header_text_match"
    state.sector_index = p.SECTOR_ORDER.index("Property")
    state.prev_entry_no = 13
    records = p._scan_column(words, state, page_num=35, anomalies=[])
    assert records[0].issuer_name == "IFCA MSC Bhd"
    assert records[0].sector == "Property"  # unaffected by the trailing header
    assert records[1].issuer_name == "Aemulus Holdings Bhd"
    assert records[1].sector == "Technology"
    assert records[1].sector_source == "header_text_match"
    print("PASS: header_after_acronym_name_still_detected")


def test_numbering_anomaly_detected():
    """A genuine gap in numbering (not a reset to 1) is flagged."""
    words = _words(
        ("1.", 42.0, 100.0),
        ("7086", 70.0, 100.0),
        ("Ablegroup", 112.0, 100.0),
        ("Bhd", 150.0, 100.0),
        ("3.", 42.0, 112.0),  # skips 2 -- a real gap, not a reset
        ("5198", 70.0, 112.0),
        ("ABM", 112.0, 112.0),
        ("Fujiya", 140.0, 112.0),
        ("Bhd", 165.0, 112.0),
    )
    state = _fresh_state()
    anomalies = []
    p._scan_column(words, state, page_num=21, anomalies=anomalies)
    assert len(anomalies) == 1
    assert anomalies[0].expected_next == 2
    assert anomalies[0].observed == 3
    print("PASS: numbering_anomaly_detected")


def test_page_provenance_preserved():
    """Every record carries the page it was extracted from."""
    words = _words(
        ("1.", 42.0, 100.0),
        ("7086", 70.0, 100.0),
        ("Ablegroup", 112.0, 100.0),
        ("Bhd", 150.0, 100.0),
    )
    state = _fresh_state()
    records = p._scan_column(words, state, page_num=21, anomalies=[])
    assert records[0].source_page == 21
    print("PASS: page_provenance_preserved")


def test_ticker_normalization():
    assert p.TICKER_RE.match("7086")
    assert p.TICKER_RE.match("03068")  # LEAP market 5-digit codes
    assert not p.TICKER_RE.match("AB12")
    assert not p.TICKER_RE.match("12")
    print("PASS: ticker_normalization")


def test_collapse_doubled_and_runs():
    assert p._collapse_doubled("TTEECCHHNNOOLLOOGGYY") == "TECHNOLOGY"
    assert (
        p._collapse_doubled("PPRRODUUCCTTSS") == "PPRRODUUCCTTSS"
    )  # not uniformly doubled, left alone
    assert p._collapse_runs("PPRRODUUCCTTSS") == "PRODUCTS"  # the aggressive fallback fixes it
    assert p._collapse_runs("PROPERTY") == "PROPERTY"  # no adjacent repeats, unaffected
    print("PASS: collapse_doubled_and_runs")


def test_match_sector_header_leftmost():
    text = "MAIN MARKET PASARAN UTAMA INDUSTRIAL PRODUCTS AND SERVICES No. Stock code"
    assert p._match_sector_header(text) == "Industrial products and services"
    assert p._match_sector_header("") is None
    assert p._match_sector_header("Not a real sector") is None
    print("PASS: match_sector_header_leftmost")


def _synthetic_result(*, duplicate=False, invalid=False, missing_total=False) -> p.ParseResult:
    result = p.ParseResult(source_document_hash="fixture-hash")
    records = [
        p.MasterRecord(
            ticker="1155",
            issuer_name="Malayan Banking Bhd",
            market="MAIN MARKET",
            sector="Financial services",
            sector_source="header_text_match",
            entry_no=1,
            source_page=32,
        ),
        p.MasterRecord(
            ticker="1295",
            issuer_name="Public Bank Bhd",
            market="MAIN MARKET",
            sector="Financial services",
            sector_source="header_text_match",
            entry_no=2,
            source_page=32,
        ),
    ]
    if duplicate:
        records.append(
            p.MasterRecord(
                ticker="1155",
                issuer_name="Malayan Banking Bhd",
                market="MAIN MARKET",
                sector="Financial services",
                sector_source="header_text_match",
                entry_no=3,
                source_page=32,
            )
        )
    if invalid:
        records.append(
            p.MasterRecord(
                ticker="9999",
                issuer_name="",
                market="MAIN MARKET",
                sector=None,
                sector_source="order_inference",
                entry_no=4,
                source_page=32,
            )
        )
    result.main_list = records
    result.table3_summary = [
        p.SectorSummaryRow(
            sector="Financial services", compliant_count=2, total_count=10, percent=20.0
        )
    ]
    result.table3_official_total = None if missing_total else 2
    return result


def test_reconcile_duplicate_detection():
    report = p.reconcile(_synthetic_result(duplicate=True))
    assert report["duplicate_record_count"] == 1
    assert "1155" in report["duplicate_tickers"]
    print("PASS: reconcile_duplicate_detection")


def test_reconcile_invalid_record_detection():
    report = p.reconcile(_synthetic_result(invalid=True))
    assert report["invalid_record_count"] == 1
    assert report["invalid_records"][0]["ticker"] == "9999"
    print("PASS: reconcile_invalid_record_detection")


def test_reconcile_clean_result():
    report = p.reconcile(_synthetic_result())
    assert report["duplicate_record_count"] == 0
    assert report["invalid_record_count"] == 0
    assert report["unresolved_discrepancy"] == 0
    assert report["sector_reconciliation"][0]["diff"] == 0
    print("PASS: reconcile_clean_result")


def test_ingest_needs_reconciliation_on_duplicate(monkeypatch):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)

    monkeypatch.setattr(p, "parse_pdf", lambda path: _synthetic_result(duplicate=True))

    result = sc_malaysia_import.ingest_pdf_publication(
        conn, __file__, publication_date="2099-01-01", source_url="https://example.test"
    )
    assert result["status"] == "ingested"
    assert result["human_review_status"] == "needs_reconciliation"

    pub = sc_malaysia_store.get_publication(conn, "sc-sac-my-2099-01-01")
    assert pub["human_review_status"] == "needs_reconciliation"

    approve_result = sc_malaysia_store.approve_publication(conn, "sc-sac-my-2099-01-01")
    assert approve_result["status"] == "error"
    assert approve_result["reason"] == "needs_reconciliation"

    activate_result = sc_malaysia_store.activate_publication(conn, "sc-sac-my-2099-01-01")
    assert activate_result["status"] == "error"
    print("PASS: ingest_needs_reconciliation_on_duplicate — cannot approve or activate")


def test_ingest_clean_stays_pending_not_approved(monkeypatch):
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)

    monkeypatch.setattr(p, "parse_pdf", lambda path: _synthetic_result())

    result = sc_malaysia_import.ingest_pdf_publication(
        conn, __file__, publication_date="2099-02-01", source_url="https://example.test"
    )
    assert result["status"] == "ingested"
    assert result["human_review_status"] == "pending", (
        "A clean reconciliation must still require explicit human approval, "
        "never auto-approve or auto-activate"
    )

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "UNKNOWN", "Pending (unapproved) publication must not produce PASS"
    print("PASS: ingest_clean_stays_pending_not_approved — no auto-activation")


def test_no_parser_output_bypasses_approval_workflow(monkeypatch):
    """However clean or dirty the parse, PASS is reachable only through
    insert -> approve -> activate. There is no path from parse_pdf() output
    directly to a PASS verdict."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)

    monkeypatch.setattr(p, "parse_pdf", lambda path: _synthetic_result())

    sc_malaysia_import.ingest_pdf_publication(
        conn, __file__, publication_date="2099-03-01", source_url="https://example.test"
    )
    # Ingested but neither approved nor activated -- must be UNKNOWN, not PASS.
    assert sc_malaysia_store.check_eligibility(conn, "1155")["status"] == "UNKNOWN"

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2099-03-01")
    # Approved but not yet activated -- still must not be PASS.
    assert sc_malaysia_store.check_eligibility(conn, "1155")["status"] == "UNKNOWN"

    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2099-03-01")
    # Only after the full insert -> approve -> activate sequence does PASS appear.
    assert sc_malaysia_store.check_eligibility(conn, "1155")["status"] == "PASS"
    print("PASS: no_parser_output_bypasses_approval_workflow")


def test_real_pdf_reconciles_exactly():
    """Integration check against the actual SC Malaysia May 2026 PDF.

    This is the authoritative regression guard against the 196-record loss
    that motivated rewriting the parser: the official total (886), Table 3's
    own sector sum, the parsed record count, and the unique ticker count must
    all agree exactly, with zero duplicates, zero invalid records, and zero
    numbering anomalies.
    """
    if not REAL_PDF_PATH.exists():
        print("SKIP: real_pdf_reconciles_exactly — PDF not available at expected path")
        return

    result = p.parse_pdf(REAL_PDF_PATH)
    report = p.reconcile(result)

    assert report["official_stated_total"] == 886, report["official_stated_total"]
    assert report["table3_sector_sum"] == 886, report["table3_sector_sum"]
    assert report["parsed_record_count"] == 886, report["parsed_record_count"]
    assert report["unique_ticker_count"] == 886, report["unique_ticker_count"]
    assert report["duplicate_record_count"] == 0, report["duplicate_tickers"]
    assert report["invalid_record_count"] == 0, report["invalid_records"]
    assert report["numbering_anomaly_count"] == 0
    assert report["unresolved_discrepancy"] == 0

    for row in report["sector_reconciliation"]:
        assert row["diff"] == 0, row

    print(
        "PASS: real_pdf_reconciles_exactly — 886/886/886, all 12 sectors exact, "
        "0 duplicates, 0 invalid, 0 anomalies"
    )


def test_real_pdf_table1_table2_supplementary():
    """Table 1/Table 2 are change-highlight lists whose entries are already
    part of (Table 1) or explicitly outside (Table 2) the main compliant list
    -- they must not silently inflate or corrupt the master roster count."""
    if not REAL_PDF_PATH.exists():
        print("SKIP: real_pdf_table1_table2_supplementary — PDF not available")
        return

    result = p.parse_pdf(REAL_PDF_PATH)
    assert len(result.table1_newly_compliant) == 44
    assert len(result.table2_newly_non_compliant) == 18
    assert len(result.additional_instruments) == 1

    main_tickers = {r.ticker for r in result.main_list}
    table2_tickers = {r.ticker for r in result.table2_newly_non_compliant}
    assert not (main_tickers & table2_tickers), (
        "A ticker explicitly reclassified non-compliant (Table 2) must not "
        "also appear in the current compliant master list"
    )
    print("PASS: real_pdf_table1_table2_supplementary")


def test_real_pdf_leap_footnote_contamination():
    """Verify that LEAP Market bilingual footnote text is cleanly stripped from issuer names."""
    if not REAL_PDF_PATH.exists():
        print("SKIP: test_real_pdf_leap_footnote_contamination — PDF not available")
        return

    result = p.parse_pdf(REAL_PDF_PATH)
    
    alpha = next((r for r in result.main_list if r.ticker == "03051"), None)
    sl = next((r for r in result.main_list if r.ticker == "03008"), None)
    
    assert alpha is not None
    assert sl is not None
    
    assert "Sophisticated Investors" not in alpha.issuer_name, alpha.issuer_name
    assert "Akta Pasaran" not in alpha.issuer_name, alpha.issuer_name
    assert alpha.issuer_name == "Alpha Ocean Resources Bhd", alpha.issuer_name
    
    assert "Sophisticated Investors" not in sl.issuer_name, sl.issuer_name
    assert "Akta Pasaran" not in sl.issuer_name, sl.issuer_name
    assert sl.issuer_name == "SL Innovation Capital Bhd", sl.issuer_name
    
    print("PASS: test_real_pdf_leap_footnote_contamination")


def main():
    test_row_reconstruction_simple()
    test_row_reconstruction_wrapped_name()
    test_two_column_extraction()
    test_header_text_match_takes_priority()
    test_acronym_heavy_name_not_mistaken_for_header()
    test_header_after_acronym_name_still_detected()
    test_numbering_anomaly_detected()
    test_page_provenance_preserved()
    test_ticker_normalization()
    test_collapse_doubled_and_runs()
    test_match_sector_header_leftmost()
    test_reconcile_duplicate_detection()
    test_reconcile_invalid_record_detection()
    test_reconcile_clean_result()

    class _FakeMonkeypatch:
        def __init__(self):
            self._originals = []

        def setattr(self, obj, name, value):
            self._originals.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._originals):
                setattr(obj, name, value)
            self._originals = []

    for test_fn in (
        test_ingest_needs_reconciliation_on_duplicate,
        test_ingest_clean_stays_pending_not_approved,
        test_no_parser_output_bypasses_approval_workflow,
    ):
        mp = _FakeMonkeypatch()
        try:
            test_fn(mp)
        finally:
            mp.undo()

    test_real_pdf_reconciles_exactly()
    test_real_pdf_table1_table2_supplementary()
    test_real_pdf_leap_footnote_contamination()

    print("\nAll SC PDF parser tests passed.")


if __name__ == "__main__":
    main()
