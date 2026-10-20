"""Tests for the AAOIFI comparison view of the US screen.

The real screen runs on canned SEC payloads (sec_fixture.compliant_responses:
assets 1,000, interest-bearing debt 200, cash 100), with a dei cover page of 10
shares added, so market cap = 10 x price. The verdict log is swapped out.
"""

import copy
import sqlite3

import holdings_monitor
import sec_edgar_screen
import sec_fixture
import shariah_screen_store
import traffic_light
from agents import shariah_agent


def _screen(symbol="AAA", *, price, shares=10.0, methodology=sec_edgar_screen.METHODOLOGY_AAOIFI):
    responses = copy.deepcopy(sec_fixture.compliant_responses(symbol))
    facts_url = sec_edgar_screen.SEC_FACTS_URL.format(cik=900_000)
    if shares is not None:
        responses[facts_url]["facts"]["dei"] = {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [{"end": "2026-01-15", "val": shares, "filed": "2026-02-01"}]}
            }
        }

    def fake(url):
        if url in responses:
            return {"ok": True, "status_code": 200, "data": responses[url]}
        return {"ok": False, "status_code": 404, "data": {}, "reason": "http_404"}

    real_request, real_record = sec_edgar_screen.sec_request, sec_edgar_screen._record_screen
    sec_edgar_screen.reset_ticker_cache()
    sec_edgar_screen.sec_request = fake
    sec_edgar_screen._record_screen = lambda verdict: None
    try:
        return sec_edgar_screen.check_us_symbol(
            symbol, methodology=methodology, price_lookup=lambda s: price
        )
    finally:
        sec_edgar_screen.sec_request, sec_edgar_screen._record_screen = real_request, real_record
        sec_edgar_screen.reset_ticker_cache()


def test_default_is_still_sc_on_total_assets():
    verdict = _screen(price=1.0, methodology=sec_edgar_screen.METHODOLOGY_SC)
    assert verdict["methodology"] == "SC_MY_SAC"
    assert verdict["status"] == "COMPLIANT"
    assert verdict["ratios"]["debt_ratio_pct"] == 20.0  # 200 / 1,000 assets
    assert verdict["ratios"]["limit_pct"] == 33.0


def test_aaoifi_measures_against_market_cap_with_a_30_percent_limit():
    passing = _screen(price=100.0)  # market cap 1,000 -> debt 20%, cash 10%
    assert passing["methodology"] == "AAOIFI"
    assert passing["status"] == "COMPLIANT"
    assert passing["ratios"]["limit_pct"] == 30.0
    assert passing["ratios"]["market_cap"] == 1000.0
    assert "market capitalisation" in passing["reason"]
    assert "approximation" in passing["ratios"]

    failing = _screen(price=50.0)  # market cap 500 -> debt 40%
    assert failing["status"] == "NON_COMPLIANT"
    assert "interest-bearing debt / market capitalisation 40.0% >= 30%" in failing["reason"]


def test_aaoifi_near_limit_band_is_ten_percent_of_thirty():
    verdict = _screen(price=72.0)  # market cap 720 -> debt 27.8%: yellow from 27%
    light = traffic_light.classify({"market": "US", "status": "PASS", "details": verdict})
    assert verdict["status"] == "COMPLIANT"
    assert light["light"] == "YELLOW"
    assert light["reasons"][0]["code"] == "near_limit_debt"


def test_aaoifi_without_price_or_shares_is_unknown_never_compliant():
    assert _screen(price=None)["reason"] == "price_unavailable_for_market_cap"
    assert _screen(price=float("nan"))["status"] == "UNKNOWN"
    assert _screen(price=100.0, shares=None)["reason"] == "shares_outstanding_not_reported"


def test_the_order_gate_call_never_uses_aaoifi():
    calls = []
    real = shariah_agent.check_us_symbol
    shariah_agent.check_us_symbol = lambda symbol, **kwargs: (
        calls.append(kwargs) or {"status": "COMPLIANT"}
    )
    try:
        shariah_agent.evaluate_shariah("AAA")
        shariah_agent.evaluate_shariah("AAA", methodology="AAOIFI", price_lookup=lambda s: 1.0)
    finally:
        shariah_agent.check_us_symbol = real
    assert calls[0] == {}  # the gate's call: no methodology, so SC SAC
    assert calls[1]["methodology"] == "AAOIFI"


def test_monitor_setting_selects_the_methodology():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert holdings_monitor.get_methodology(conn) == "SC_MY_SAC"
    holdings_monitor.set_methodology(conn, "AAOIFI")
    assert holdings_monitor.get_methodology(conn) == "AAOIFI"
    try:
        holdings_monitor.set_methodology(conn, "MADE_UP")
        raise AssertionError("an unknown methodology must be refused")
    except holdings_monitor.HoldingError:
        pass

    seen = []
    real = shariah_agent.evaluate_shariah

    def spy(symbol, **kwargs):
        seen.append(kwargs)
        return {"market": "US", "status": "PASS", "symbol": symbol, "details": {}}

    shariah_agent.evaluate_shariah = spy
    try:
        holdings_monitor.upsert_holding(conn, symbol="AAA", quantity=1, average_cost=1)
        result = holdings_monitor.run_monitor(conn, price_lookup=lambda s: 5.0)
    finally:
        shariah_agent.evaluate_shariah = real
    assert result["methodology"] == "AAOIFI"
    assert seen[0]["methodology"] == "AAOIFI" and seen[0]["price_lookup"]("AAA") == 5.0
    assert "AAOIFI" in result["basis"]


def test_recently_cleared_compares_only_the_same_methodology():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    shariah_screen_store.record_shariah_screen(
        conn,
        {
            "symbol": "AAA",
            "status": "NON_COMPLIANT",
            "report_date": "2024-12-31",
            "methodology": "SC_MY_SAC",
        },
    )
    shariah_screen_store.record_shariah_screen(
        conn,
        {
            "symbol": "AAA",
            "status": "COMPLIANT",
            "report_date": "2024-12-31",
            "methodology": "AAOIFI",
        },
    )
    assert traffic_light.prior_report_status(conn, "AAA", "2025-12-31") == "NON_COMPLIANT"
    assert traffic_light.prior_report_status(conn, "AAA", "2025-12-31", "AAOIFI") == "COMPLIANT"
