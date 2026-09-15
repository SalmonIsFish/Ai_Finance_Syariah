"""Pin the GET /stock/{symbol}/explain response contract.

Terminal 1's Shariah Trace panel renders this shape, so these assertions are the
handoff contract, not just a regression net. Changing a field name here means
changing the dashboard.

Nothing reaches SEC: explain_symbol takes a `screen` seam, and the route test
swaps local_api.explain_symbol.
"""
import pytest
import auth
@pytest.fixture(autouse=True)
def _owner_auth_fixture():
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    yield
    _my_app.dependency_overrides.pop(_get_owner_actor, None)


import json

import local_api
import shariah_explain
from fastapi.testclient import TestClient


COMPLIANT_SCREEN = {
    "symbol": "CVX",
    "exchange": "NYSE",
    "provider": "SEC_EDGAR",
    "methodology": "SC_MY_SAC",
    "company": "CHEVRON CORP",
    "status": "COMPLIANT",
    "report_date": "2025-12-31",
    "screen": "financial_ratios",
    "ratios": {
        "report_date": "2025-12-31",
        "form": "10-K",
        "total_assets": 324012000000.0,
        "interest_bearing_debt": 43103000000.0,
        "conventional_cash": 7285000000.0,
        "debt_ratio_pct": 13.3029,
        "cash_ratio_pct": 2.2484,
        "limit_pct": 33.0,
        "debt_concepts_used": ["LongTermDebtCurrent", "ShortTermBorrowings"],
        "cash_concepts_used": ["MarketableSecuritiesCurrent"],
    },
    "reason": "debt 13.3% and cash 2.2% of total assets, both under 33%",
}

BANK_SCREEN = {
    "symbol": "JPM",
    "exchange": "NYSE",
    "provider": "SEC_EDGAR",
    "methodology": "SC_MY_SAC",
    "company": "JPMORGAN CHASE & CO",
    "status": "NON_COMPLIANT",
    "report_date": None,
    "screen": "business_activity",
    "reason": "excluded business activity: conventional banking / interest-based lending (SIC 6021)",
}

UNKNOWN_SCREEN = {
    "symbol": "XOM",
    "exchange": "NYSE",
    "provider": "SEC_EDGAR",
    "methodology": "SC_MY_SAC",
    "company": "EXXON MOBIL CORP",
    "status": "UNKNOWN",
    "report_date": None,
    "reason": "no annual filing anchor found",
}


def test_compliant_symbol_carries_verdict_rule_basis_and_citation() -> None:
    payload = shariah_explain.explain_symbol("cvx", screen=lambda _s: COMPLIANT_SCREEN)

    assert payload["symbol"] == "CVX", payload["symbol"]
    assert payload["verdict"]["status"] == "COMPLIANT"
    assert payload["verdict"]["tradeable"] is True
    assert payload["verdict"]["statement"]

    rule = payload["rule"]
    assert rule["id"] == "sc_my_sac.financial_ratios"
    assert rule["tier"] == 2
    assert rule["fired"]

    names = [test["name"] for test in rule["tests"]]
    assert names == ["interest_bearing_debt_ratio", "conventional_cash_ratio"], names
    debt = rule["tests"][0]
    assert debt["value_pct"] == 13.3029
    assert debt["limit_pct"] == 33.0
    assert debt["comparator"] == "<"
    assert debt["passed"] is True
    assert debt["margin_pct"] == 19.6971, debt["margin_pct"]
    assert debt["citation"]["source"].endswith("screening-criteria-breakdown.md")

    # The narrowest margin is what a watcher acts on, and it must be the minimum.
    assert rule["narrowest_margin_pct"] == 19.6971, rule["narrowest_margin_pct"]

    assert [entry["principle"] for entry in payload["fiqh_basis"]] == [
        "Riba",
        "Riba al-Nasiyah",
        "Equity ownership",
    ]
    for entry in payload["fiqh_basis"]:
        assert entry["claim"], entry
        assert entry["citation"]["source"], entry
        assert entry["citation"]["kind"] in {"regulatory_methodology", "secondary_summary"}, entry

    evidence = payload["evidence"]
    assert evidence["filing"]["form"] == "10-K"
    assert evidence["interest_bearing_debt"]["value"] == 43103000000.0
    assert evidence["interest_bearing_debt"]["xbrl_concepts"]
    assert evidence["conventional_cash"]["xbrl_concepts"]

    assert payload["provenance"]["provider"] == "SEC_EDGAR"
    assert payload["provenance"]["methodology"] == "SC_MY_SAC"
    assert payload["provenance"]["limitations"], "limitations must always be stated"
    assert "cannot override" in payload["decision_rule"]


def test_business_activity_reject_computes_no_ratios() -> None:
    payload = shariah_explain.explain_symbol("JPM", screen=lambda _s: BANK_SCREEN)

    assert payload["verdict"]["status"] == "NON_COMPLIANT"
    assert payload["verdict"]["tradeable"] is False
    assert payload["rule"]["id"] == "sc_my_sac.business_activity"
    assert payload["rule"]["tier"] == 1
    # A bank passes the ratio tests on its own balance sheet; running them would
    # give the wrong answer, so there must be none to show.
    assert payload["rule"]["tests"] == []
    assert payload["rule"]["narrowest_margin_pct"] is None
    assert payload["evidence"] == {}
    assert payload["fiqh_basis"][0]["principle"] == "Business activity exclusion"


def test_indeterminate_screens_are_not_tradeable() -> None:
    """Fail closed: UNKNOWN and ERROR are not 'probably fine'."""
    for status in ["UNKNOWN", "ERROR"]:
        payload = shariah_explain.explain_symbol("XOM", screen=lambda _s, s=status: {**UNKNOWN_SCREEN, "status": s})
        assert payload["verdict"]["status"] == status
        assert payload["verdict"]["tradeable"] is False, status
        assert payload["rule"]["id"] == "sc_my_sac.indeterminate", status
        assert payload["rule"]["tests"] == []

    blank = shariah_explain.explain_symbol("   ")
    assert blank["verdict"]["tradeable"] is False
    assert blank["verdict"]["status"] == "ERROR"


def test_non_compliant_ratio_marks_the_failing_test() -> None:
    """A failed ratio must be identifiable per-test, not only in the summary string."""
    failing = {
        **COMPLIANT_SCREEN,
        "symbol": "KO",
        "status": "NON_COMPLIANT",
        "reason": "interest-bearing debt / total assets 40.2% >= 33%",
        "ratios": {**COMPLIANT_SCREEN["ratios"], "debt_ratio_pct": 40.2, "cash_ratio_pct": 5.0},
    }
    payload = shariah_explain.explain_symbol("KO", screen=lambda _s: failing)

    assert payload["verdict"]["tradeable"] is False
    debt, cash = payload["rule"]["tests"]
    assert debt["passed"] is False, debt
    assert debt["margin_pct"] == -7.2, debt["margin_pct"]
    assert cash["passed"] is True, cash
    assert payload["rule"]["narrowest_margin_pct"] == -7.2


def test_route_returns_the_same_payload() -> None:
    original = local_api.explain_symbol
    local_api.explain_symbol = lambda symbol: shariah_explain.explain_symbol(symbol, screen=lambda _s: COMPLIANT_SCREEN)
    try:
        client = TestClient(local_api.app)
        response = client.get("/stock/CVX/explain")
        assert response.status_code == 200, response.status_code
        body = response.json()
        assert body["symbol"] == "CVX"
        assert body["verdict"]["tradeable"] is True
        assert body["rule"]["tests"][0]["name"] == "interest_bearing_debt_ratio"
        # The payload must survive JSON serialisation unchanged.
        json.dumps(body)
    finally:
        local_api.explain_symbol = original


def main() -> None:
    test_compliant_symbol_carries_verdict_rule_basis_and_citation()
    test_business_activity_reject_computes_no_ratios()
    test_indeterminate_screens_are_not_tradeable()
    test_non_compliant_ratio_marks_the_failing_test()
    test_route_returns_the_same_payload()
    print("PASS: /stock/{symbol}/explain returns verdict, rule, fiqh basis, and citations.")


if __name__ == "__main__":
    import auth
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    try:
        main()
    finally:
        _my_app.dependency_overrides.pop(_get_owner_actor, None)
