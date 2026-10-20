"""Tests for the model's reading of an annual report.

All three seams (locate, fetch_text, llm) are swapped: nothing reaches SEC or
OpenRouter. The properties that matter: an invented quote is discarded, a
reading can only ever add YELLOW, and a failure records nothing.
"""

import json
import sqlite3
from types import SimpleNamespace

import filing_review
import holdings_monitor
import sec_edgar_screen
import traffic_light

FILING_HTML = """
<html><body>
<p>Table of contents. Item 1. Business 3 Item 1A. Risk Factors 9</p>
<h2>Item 1. Business</h2>
<p>We design and sell consumer devices worldwide.</p>
<p>Our resorts segment operates casino gaming floors in two properties, contributing
approximately 6% of total net revenue.</p>
<script>var ignored = "Item 1. Business";</script>
<h2>Item 1A. Risk Factors</h2>
<p>Many risks.</p>
<h3>Segment information</h3>
<p>Devices 94%, Resorts 6%.</p>
</body></html>
"""

REAL_QUOTE = "operates casino gaming floors in two properties"
SETTINGS = SimpleNamespace(openrouter_api_key="test-key", openrouter_model="test-model")


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    holdings_monitor.ensure_monitor_tables(conn)
    return conn


def _locate(symbol):
    return {
        "status": "OK",
        "symbol": symbol,
        "form": "10-K",
        "report_date": "2025-12-31",
        "accession": "0000000000-25-000001",
        "url": "https://www.sec.gov/Archives/edgar/data/1/1/doc.htm",
    }


def _fetch(url):
    return {"ok": True, "status_code": 200, "text": FILING_HTML}


def _llm_reply(findings, summary="read"):
    captured = {}

    def llm(payload):
        captured["payload"] = payload
        content = json.dumps({"findings": findings, "summary": summary})
        return {"ok": True, "data": {"choices": [{"message": {"content": content}}]}}

    return llm, captured


def _review(conn, findings):
    llm, captured = _llm_reply(findings)
    result = filing_review.review_symbol(
        conn, "TEST", locate=_locate, fetch_text=_fetch, llm=llm, settings=SETTINGS
    )
    return result, captured


def test_business_section_skips_the_table_of_contents_and_scripts():
    text = filing_review.html_to_text(FILING_HTML)
    assert "var ignored" not in text
    section = filing_review.business_section(text)
    assert "casino gaming floors" in section
    assert "[Segment information]" in section and "Resorts 6%" in section


def test_verified_finding_over_the_band_becomes_a_yellow_flag():
    conn = _conn()
    result, captured = _review(
        conn,
        [
            {
                "category": "gambling",
                "activity": "casino",
                "share_of_revenue_pct": 6,
                "quote": REAL_QUOTE,
            }
        ],
    )
    assert result["status"] == "OK"
    assert result["recorded"]["kind"] == "flag"
    assert result["recorded"]["activity_pct"] == 6.0
    # The model was given the business section, not the whole filing.
    assert "casino gaming floors" in captured["payload"]["messages"][1]["content"]
    [review] = holdings_monitor.reviews_for(conn, "TEST", "2025-12-31")
    assert (review["kind"], review["source"]) == ("flag", "model")


def test_invented_quote_is_discarded_and_records_nothing():
    conn = _conn()
    result, _ = _review(
        conn,
        [
            {
                "category": "gambling",
                "activity": "casino",
                "share_of_revenue_pct": 30,
                "quote": "the company derives most revenue from lotteries",
            }
        ],
    )
    assert result["verified_findings"] == []
    assert len(result["rejected_findings"]) == 1
    assert result["recorded"] is None
    assert holdings_monitor.reviews_for(conn, "TEST", "2025-12-31") == []


def test_small_total_is_a_note_and_an_unstated_share_is_a_flag():
    small = [
        {"category": "alcohol", "activity": "bar", "share_of_revenue_pct": 2, "quote": REAL_QUOTE}
    ]
    assert (
        filing_review.decide_review(filing_review.verify_findings(small, FILING_HTML)[0])["kind"]
        == "note"
    )
    unstated = [{**small[0], "share_of_revenue_pct": None}]
    assert (
        filing_review.decide_review(filing_review.verify_findings(unstated, FILING_HTML)[0])["kind"]
        == "flag"
    )
    # Shares are summed: two 2.5% activities reach the 4.5% band together.
    two = [{**small[0], "share_of_revenue_pct": 2.5}, {**small[0], "share_of_revenue_pct": 2.5}]
    assert (
        filing_review.decide_review(filing_review.verify_findings(two, FILING_HTML)[0])["kind"]
        == "flag"
    )


def test_a_reading_can_never_make_anything_green_or_red():
    conn = _conn()
    # Even a reply that tries to speak in colours or claim compliance is just a flag.
    _review(
        conn,
        [
            {
                "category": "gambling",
                "activity": "casino",
                "share_of_revenue_pct": 99,
                "quote": REAL_QUOTE,
                "light": "RED",
            }
        ],
    )
    reviews = holdings_monitor.reviews_for(conn, "TEST", "2025-12-31")
    assert {r["kind"] for r in reviews} == {"flag"}
    verdict = {"market": "US", "status": "REJECT", "details": {"status": "NON_COMPLIANT"}}
    assert traffic_light.classify(verdict, flags=reviews)["light"] == "RED"


def test_failures_record_nothing():
    conn = _conn()

    def bad_reply(payload):
        return {"ok": True, "data": {"choices": [{"message": {"content": "not json"}}]}}

    result = filing_review.review_symbol(
        conn, "TEST", locate=_locate, fetch_text=_fetch, llm=bad_reply, settings=SETTINGS
    )
    assert result["status"] == "ERROR"
    down = filing_review.review_symbol(
        conn,
        "TEST",
        locate=_locate,
        fetch_text=lambda url: {"ok": False, "reason": "URLError"},
        llm=bad_reply,
        settings=SETTINGS,
    )
    assert down["status"] == "ERROR"
    assert holdings_monitor.reviews_for(conn, "TEST", "2025-12-31") == []


def test_not_configured_and_bursa_are_refused_before_any_fetch():
    conn = _conn()

    def explode(*_):
        raise AssertionError("must not fetch")

    no_key = SimpleNamespace(openrouter_api_key=None, openrouter_model="m")
    assert (
        filing_review.review_symbol(conn, "TEST", locate=explode, settings=no_key)["status"]
        == "NOT_CONFIGURED"
    )
    assert (
        filing_review.review_symbol(conn, "4197", locate=explode, settings=SETTINGS)["status"]
        == "NOT_APPLICABLE"
    )


def test_latest_annual_filing_builds_the_archive_url():
    submissions = {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K", "10-K"],
                "accessionNumber": ["0000320193-26-000001", "0000320193-25-000106", "x"],
                "primaryDocument": ["q.htm", "aapl-20250927.htm", "old.htm"],
                "reportDate": ["2026-03-28", "2025-09-27", "2024-09-28"],
            }
        }
    }
    original_request, original_cache = (
        sec_edgar_screen.sec_request,
        sec_edgar_screen._TICKER_MAP_CACHE,
    )
    sec_edgar_screen._TICKER_MAP_CACHE = {"AAPL": 320193}
    sec_edgar_screen.sec_request = lambda url: {"ok": True, "data": submissions}
    try:
        filing = sec_edgar_screen.latest_annual_filing("aapl")
    finally:
        sec_edgar_screen.sec_request = original_request
        sec_edgar_screen._TICKER_MAP_CACHE = original_cache
    assert filing["status"] == "OK"
    assert filing["report_date"] == "2025-09-27"
    assert filing["url"] == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000106/aapl-20250927.htm"
    )
