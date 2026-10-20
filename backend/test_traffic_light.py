"""Tests for the GREEN / YELLOW / RED layer over a Shariah verdict.

The layer reads a verdict the screen already produced; it never screens, never
fetches, and never changes `status`. The tests that matter most are the ones
asserting what can NOT happen: an AI flag can never produce GREEN, a RED is never
softened, and an unrecognised status never reads as GREEN.
"""

import sqlite3

import shariah_screen_store
import traffic_light
from traffic_light import GREEN, RED, YELLOW


def _us(status, *, debt=10.0, cash=10.0, debt_concepts=("LongTermDebtNoncurrent",), **details):
    raw = {
        "COMPLIANT": "COMPLIANT",
        "NON_COMPLIANT": "NON_COMPLIANT",
    }.get(status, status)
    ratios = {
        "report_date": "2025-12-31",
        "debt_ratio_pct": debt,
        "cash_ratio_pct": cash,
        "limit_pct": 33.0,
        "debt_concepts_used": list(debt_concepts),
        "cash_concepts_used": ["CashAndCashEquivalentsAtCarryingValue"],
    }
    gate = {"COMPLIANT": "PASS", "NON_COMPLIANT": "REJECT"}.get(status, "UNKNOWN")
    return {
        "market": "US",
        "status": gate,
        "symbol": "TEST",
        "details": {
            "status": raw,
            "screen": "financial_ratios",
            "report_date": "2025-12-31",
            "ratios": ratios,
            **details,
        },
    }


def _codes(result):
    return [r["code"] for r in result["reasons"]]


def test_clear_pass_is_green():
    result = traffic_light.classify(_us("COMPLIANT"))
    assert result["light"] == GREEN
    assert result["reasons"] == []
    assert result["near_limit_band"] == traffic_light.NEAR_LIMIT_BAND


def test_non_compliant_is_red():
    result = traffic_light.classify(_us("NON_COMPLIANT", debt=34.0, reason="debt 34% >= 33%"))
    assert result["light"] == RED
    # A RED always says why, in the screen's own words.
    assert result["reasons"] == [{"code": "screen_non_compliant", "detail": "debt 34% >= 33%"}]


def test_near_limit_boundary_is_ten_percent_of_the_limit():
    # 33 * 0.9 = 29.7: the first value inside the band.
    assert traffic_light.classify(_us("COMPLIANT", debt=29.7))["light"] == YELLOW
    assert traffic_light.classify(_us("COMPLIANT", debt=29.69))["light"] == GREEN
    result = traffic_light.classify(_us("COMPLIANT", cash=32.9))
    assert _codes(result) == ["near_limit_cash"]


def test_both_ratios_near_limit_report_both():
    result = traffic_light.classify(_us("COMPLIANT", debt=31.0, cash=30.0))
    assert result["light"] == YELLOW
    assert set(_codes(result)) == {"near_limit_debt", "near_limit_cash"}


def test_unknown_or_error_is_yellow_never_green():
    for status in ("UNKNOWN", "ERROR"):
        verdict = {"market": "US", "status": "UNKNOWN", "details": {"status": status}}
        result = traffic_light.classify(verdict)
        assert result["light"] == YELLOW
        assert _codes(result) == ["data_unavailable"]


def test_unrecognised_status_is_yellow():
    result = traffic_light.classify({"market": "US", "status": "MAYBE", "details": {}})
    assert result["light"] == YELLOW


def test_missing_debt_line_is_yellow():
    result = traffic_light.classify(_us("COMPLIANT", debt=0.0, debt_concepts=()))
    assert result["light"] == YELLOW
    assert _codes(result) == ["no_debt_line_in_filing"]


def test_mixed_activity_sic_block_is_yellow_not_red():
    verdict = {
        "market": "US",
        "status": "REJECT",
        "details": {
            "status": "NON_COMPLIANT",
            "screen": "business_activity",
            "exclusion_category": "hospitality_mixed_activity",
        },
    }
    result = traffic_light.classify(verdict)
    assert result["light"] == YELLOW
    assert _codes(result) == ["mixed_business_activity"]


def test_outright_excluded_activity_stays_red():
    verdict = {
        "market": "US",
        "status": "REJECT",
        "details": {
            "status": "NON_COMPLIANT",
            "screen": "business_activity",
            "exclusion_category": "gambling_or_entertainment",
        },
    }
    assert traffic_light.classify(verdict)["light"] == RED


def test_ai_flag_can_add_yellow_but_never_green():
    flag = {"code": "mixed_business_activity", "source": "llm", "quote": "casino revenue 6%"}
    green_case = traffic_light.classify(_us("COMPLIANT"), flags=[flag])
    assert green_case["light"] == YELLOW
    assert green_case["reasons"][0]["quote"] == "casino revenue 6%"

    # A flag cannot rescue a RED or a YELLOW, whatever it says.
    clearing = {"code": "all_clear", "source": "llm", "light": "GREEN"}
    assert traffic_light.classify(_us("NON_COMPLIANT"), flags=[clearing])["light"] == RED
    assert traffic_light.classify(_us("COMPLIANT", debt=31.0), flags=[clearing])["light"] == YELLOW


def test_recovery_after_failed_report_is_yellow_until_confirmed():
    result = traffic_light.classify(_us("COMPLIANT"), prior_report_status="NON_COMPLIANT")
    assert result["light"] == YELLOW
    assert _codes(result) == ["recently_cleared"]
    # The next report confirming PASS clears it.
    assert (
        traffic_light.classify(_us("COMPLIANT"), prior_report_status="COMPLIANT")["light"] == GREEN
    )


def test_a_new_red_is_never_softened_by_history():
    result = traffic_light.classify(_us("NON_COMPLIANT"), prior_report_status="COMPLIANT")
    assert result["light"] == RED


def test_malaysia_follows_the_sc_list_directly():
    assert traffic_light.classify({"market": "MY", "status": "PASS"})["light"] == GREEN
    assert traffic_light.classify({"market": "MY", "status": "REJECT"})["light"] == RED
    assert traffic_light.classify({"market": "MY", "status": "UNKNOWN"})["light"] == YELLOW


def test_prior_report_status_reads_the_screen_log():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for status, report_date in [
        ("NON_COMPLIANT", "2024-12-31"),
        ("ERROR", None),  # a fetch failure has no report and must be skipped
        ("COMPLIANT", "2025-12-31"),
    ]:
        shariah_screen_store.record_shariah_screen(
            conn, {"symbol": "TEST", "status": status, "report_date": report_date}
        )
    assert traffic_light.prior_report_status(conn, "TEST", "2025-12-31") == "NON_COMPLIANT"
    assert traffic_light.prior_report_status(conn, "TEST", "2024-12-31") is None
    assert traffic_light.prior_report_status(conn, "OTHER", "2025-12-31") is None


def test_owner_escalation_moves_to_red_and_nothing_moves_to_green():
    escalation = [{"detail": "segment note shows 8% casino revenue"}]
    for verdict in (_us("COMPLIANT"), _us("COMPLIANT", debt=31.0)):
        result = traffic_light.classify(verdict, escalations=escalation)
        assert result["light"] == RED
        assert result["reasons"][0]["code"] == "owner_escalation"
    # There is no escalation shape that yields GREEN.
    assert (
        traffic_light.classify(_us("NON_COMPLIANT"), escalations=[{"light": "GREEN"}])["light"]
        == RED
    )
    assert traffic_light.classify(_us("COMPLIANT", debt=31.0), escalations=[])["light"] == YELLOW
