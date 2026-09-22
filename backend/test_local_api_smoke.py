"""Smoke-test the local FastAPI contract without submitting orders."""

import json
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text(
    json.dumps(
        {
            "validation": {"status": "active"},
            "records": [
                {
                    "ticker": "0001",
                    "issuer_name": "Test Issuer",
                    "shariah_status": "COMPLIANT",
                }
            ],
        }
    ),
    encoding="utf-8",
)
import pytest

_ENV_ORIG = {
    k: os.environ.get(k)
    for k in [
        "SHARIAH_UNIVERSE_PATH",
        "TRADING_MODE",
        "PAPER_EXECUTION_ENABLED",
        "PAPER_EXECUTION_ADAPTER",
        "MOOMOO_MODE",
        "PAPER_ACCOUNT_EQUITY",
    ]
}

# Pinned rather than inherited. approval-time risk re-derivation sizes
# loss_per_trade_pct as the order's notional over settings.paper_account_equity
# against MAX_LOSS_PER_TRADE_PCT (0.5%), so the AAPL scenario below needs an
# account large enough for a single 333.74 share to be a legitimate trade.
# PAPER_ACCOUNT_EQUITY defaults to 10000, under which that one share is 3.34%
# and the gate correctly refuses it. Left unset, this file's outcome depends on
# whatever each developer's backend/.env happens to contain.
_FIXTURE_ACCOUNT_EQUITY = "100000"


@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
    os.environ["MOOMOO_MODE"] = "paper"
    os.environ["PAPER_ACCOUNT_EQUITY"] = _FIXTURE_ACCOUNT_EQUITY
    yield
    for _k in _ENV_ORIG:
        if _ENV_ORIG[_k] is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _ENV_ORIG[_k]


os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = _FIXTURE_ACCOUNT_EQUITY

from agent_coordinator import evaluate_candidate
from agents.shariah_agent import detect_market
import local_api
import sc_malaysia_store
from local_api import app


def main() -> None:
    db_path = Path(fixture_dir.name) / "paper_trading.db"
    local_api.DB_PATH = db_path
    # sc_malaysia_store keeps its own module-level DB_PATH, and
    # shariah_gate.check_symbol reaches the store through
    # sc_malaysia_store.connect_default() rather than through local_api. Without
    # this second redirect the Shariah verdict in this file comes from the real
    # backend/paper_trading.db: the assertions below passed for months only
    # because a self-superseded publication had made every live ticker resolve
    # UNKNOWN, which is the state they assert. Repairing that publication turned
    # them red. The fixture DB below is deliberately empty of publications, so
    # "no approved publication" is now a property of the fixture rather than an
    # accident of production.
    sc_malaysia_store.DB_PATH = db_path
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200, home.text
    home_payload = home.json()
    assert home_payload["live_trading"] is False
    assert home_payload["trading_mode"] == "approval"
    assert home_payload["paper_execution_enabled"] is False
    assert home_payload["paper_execution_adapter"] == "disabled"
    assert home_payload["broker_submission"] is False
    assert "/health" in home_payload["routes"]
    assert "/system/mode" in home_payload["routes"]
    assert "/paper/status" in home_payload["routes"]
    assert "/moomoo/status" in home_payload["routes"]
    assert "/market-data/{symbol}" in home_payload["routes"]
    assert "/stock/{symbol}/profile" in home_payload["routes"]
    assert "/investment-committee" in home_payload["routes"]
    assert "/watchlist" in home_payload["routes"]
    assert "/opportunities" in home_payload["routes"]
    assert "/opportunity-alerts" in home_payload["routes"]
    assert "/agent/evaluate" in home_payload["routes"]
    assert "/paper/preview" in home_payload["routes"]
    assert "/paper/approval" in home_payload["routes"]
    assert "/paper/execute/{queue_id}" in home_payload["routes"]
    assert "/paper/reconcile/{queue_id}" in home_payload["routes"]
    assert "/portfolio" in home_payload["routes"]
    assert "/portfolio/history" in home_payload["routes"]
    assert "/portfolio/history/live" in home_payload["routes"]
    assert "/paper/account" in home_payload["routes"]
    assert "/paper/positions/live" in home_payload["routes"]
    assert "/approvals" in home_payload["routes"]

    health = client.get("/health")
    assert health.status_code == 200, health.text
    health_payload = health.json()
    assert health_payload["status"] == "ok"
    assert health_payload["mode"] == "paper"
    assert health_payload["trading_mode"] == "approval"
    assert health_payload["paper_execution_enabled"] is False
    assert health_payload["paper_execution_adapter"] == "disabled"
    assert health_payload["broker_submission"] is False

    system_mode = client.get("/system/mode")
    assert system_mode.status_code == 200, system_mode.text
    system_mode_payload = system_mode.json()
    assert system_mode_payload["trading_mode"] == "approval"
    assert system_mode_payload["paper_execution_adapter"] == "disabled"
    assert system_mode_payload["capabilities"]["agents_can_recommend"] is True
    assert system_mode_payload["capabilities"]["human_approval_required"] is True
    assert system_mode_payload["effective_paper_execution_allowed"] is False
    assert system_mode_payload["broker_submission"] is False
    assert len(system_mode_payload["agent_team"]) >= 5

    paper_status = client.get("/paper/status")
    assert paper_status.status_code == 200, paper_status.text
    paper_payload = paper_status.json()
    assert paper_payload["mode"] == "SIMULATE"
    assert paper_payload["trading_mode"] == "approval"
    assert paper_payload["approval_required"] is True
    assert paper_payload["paper_execution_enabled"] is False
    assert paper_payload["paper_execution_adapter"] == "disabled"
    assert paper_payload["live_trading"] is False
    assert paper_payload["broker_submission"] is False
    assert detect_market("0001") == "MY"
    assert detect_market("AAPL") == "US"

    portfolio = client.get("/portfolio")
    assert portfolio.status_code == 200, portfolio.text
    portfolio_payload = portfolio.json()
    assert portfolio_payload["status"] == "OK"
    assert portfolio_payload["valuation_status"] == "EMPTY"
    assert portfolio_payload["position_count"] == 0
    assert portfolio_payload["fill_count"] == 0
    assert portfolio_payload["positions"] == []
    assert portfolio_payload["fills"] == []
    assert portfolio_payload["market_value"] is None

    moomoo = client.get("/moomoo/status")
    assert moomoo.status_code == 200, moomoo.text
    moomoo_payload = moomoo.json()
    assert moomoo_payload["mode"] == "paper"
    assert moomoo_payload["paper_execution_enabled"] is False
    assert moomoo_payload["broker_submission"] is False
    assert "paper_account_ready" in moomoo_payload

    original_summarize_history = local_api.summarize_history
    local_api.summarize_history = lambda symbol, **kwargs: {
        "symbol": symbol.strip().upper(),
        "source": "test_fixture",
        "bars": 2,
        "min_bars": kwargs["min_bars"],
        "enough_history": False,
        "latest_date": "2026-07-21",
        "latest_close": 102.0,
        "start_date": "2025-07-22",
        "end_date": "2026-07-22",
    }
    try:
        market_data = client.get("/market-data/TEST")
    finally:
        local_api.summarize_history = original_summarize_history
    assert market_data.status_code == 200, market_data.text
    market_payload = market_data.json()
    assert market_payload["symbol"] == "TEST"
    assert market_payload["source"] == "test_fixture"
    assert market_payload["bars"] == 2
    assert market_payload["enough_history"] is False

    watchlist_update = client.post(
        "/watchlist",
        json={"symbols": ["MSFT", "AAPL"], "alert_threshold_pct": 2.0},
    )
    assert watchlist_update.status_code == 200, watchlist_update.text
    watchlist_update_payload = watchlist_update.json()
    assert watchlist_update_payload["symbols"] == ["MSFT", "AAPL"]
    assert watchlist_update_payload["alert_threshold_pct"] == 2.0

    watchlist = client.get("/watchlist")
    assert watchlist.status_code == 200, watchlist.text
    watchlist_payload = watchlist.json()
    assert watchlist_payload["symbols"] == ["MSFT", "AAPL"]
    assert watchlist_payload["alert_threshold_pct"] == 2.0
    assert "latest_results" in watchlist_payload

    original_scan_evaluate_candidate = local_api.scan_opportunities.__globals__[
        "evaluate_candidate"
    ]
    original_scan_evaluate_quant = local_api.scan_opportunities.__globals__["evaluate_quant"]
    local_api.scan_opportunities.__globals__["evaluate_quant"] = (
        lambda symbol, allow_fallback=False, allow_stale_cache=False: {
            "agent": "quant",
            "status": "PASS" if symbol == "AAPL" else "NO_SIGNAL",
            "symbol": symbol,
            "signal": "BUY" if symbol == "AAPL" else "NO_SIGNAL",
            "reason": "test",
            "price": 100.0,
            "bars": 200,
            "price_source": "test",
            "strategy": {
                "signal": "BUY" if symbol == "AAPL" else "NO_SIGNAL",
                "sma50": 120.0,
                "sma200": 100.0,
                "trend_ok": True,
                "breakout_ok": symbol == "AAPL",
                "breakout_level": 99.0,
                "breakout_gap_pct": 1.0,
            },
        }
    )
    local_api.scan_opportunities.__globals__["evaluate_candidate"] = lambda **kwargs: {
        "symbol": kwargs["symbol"],
        "side": "BUY",
        "quantity": 1,
        "price": 98.0 if kwargs["symbol"] == "MSFT" else 100.0,
        "notional": 98.0 if kwargs["symbol"] == "MSFT" else 100.0,
        "decision": "READY_FOR_APPROVAL" if kwargs["symbol"] == "AAPL" else "BLOCKED",
        "blockers": [] if kwargs["symbol"] == "AAPL" else ["quant_no_buy_signal"],
        "broker_submission": False,
        "agent_summary": {
            "shariah": {"status": "PASS", "reason": "COMPLIANT", "market": "US"},
            "quant": {
                "signal": "BUY" if kwargs["symbol"] == "AAPL" else "NO_SIGNAL",
                "reason": "test",
                "price_source": "test",
                "bars": 200,
                "strategy": {
                    "sma50": 120.0,
                    "sma200": 100.0,
                    "trend_ok": True,
                    "breakout_ok": kwargs["symbol"] == "AAPL",
                    "breakout_level": 99.0,
                    "breakout_gap_pct": 1.0,
                },
            },
            "risk": {"status": "PASS"},
        },
    }
    try:
        opportunities = client.get("/opportunities?symbols=MSFT,AAPL&alert_threshold_pct=2.0")
        forced_opportunities = client.get(
            "/opportunities?force=true&symbols=MSFT,AAPL&alert_threshold_pct=2.0"
        )
    finally:
        local_api.scan_opportunities.__globals__["evaluate_candidate"] = (
            original_scan_evaluate_candidate
        )
        local_api.scan_opportunities.__globals__["evaluate_quant"] = original_scan_evaluate_quant
    assert opportunities.status_code == 200, opportunities.text
    opportunities_payload = opportunities.json()
    assert opportunities_payload["status"] == "SCANNED"
    assert opportunities_payload["throttled"] is False
    assert opportunities_payload["count"] == 2
    assert opportunities_payload["ready_count"] == 1
    assert opportunities_payload["alert_count"] == 1
    assert opportunities_payload["data_error_count"] == 0
    assert opportunities_payload["items"][0]["symbol"] == "AAPL"
    assert opportunities_payload["items"][0]["ready_for_approval"] is True
    assert opportunities_payload["items"][0]["watch_status"] == "READY"
    assert opportunities_payload["items"][0]["trend_ok"] is True
    assert opportunities_payload["items"][0]["breakout_ok"] is True
    assert opportunities_payload["items"][0]["breakout_gap_pct"] == 1.0
    assert opportunities_payload["items"][0]["trigger_price"] == 99.0
    assert opportunities_payload["items"][0]["distance_to_trigger"] == -1.0
    assert opportunities_payload["items"][1]["alert_status"] == "ALERT"
    assert "alert_events" in opportunities_payload

    throttled_opportunities = client.get("/opportunities")
    assert throttled_opportunities.status_code == 200, throttled_opportunities.text
    throttled_payload = throttled_opportunities.json()
    assert throttled_payload["status"] == "THROTTLED"
    assert throttled_payload["throttled"] is True
    assert throttled_payload["wait_seconds"] > 0
    assert throttled_payload["count"] == 2

    assert forced_opportunities.status_code == 200, forced_opportunities.text
    forced_payload = forced_opportunities.json()
    assert forced_payload["status"] == "SCANNED"
    assert forced_payload["throttled"] is False

    opportunity_alerts = client.get("/opportunity-alerts")
    assert opportunity_alerts.status_code == 200, opportunity_alerts.text
    assert isinstance(opportunity_alerts.json(), list)

    agent_evaluation = client.post(
        "/agent/evaluate",
        json={
            "symbol": "0001",
            "side": "BUY",
            "quantity": 2,
            "price": 50.0,
            "position_pct": 1.0,
            "total_exposure_pct": 5.0,
            "loss_per_trade_pct": 0.2,
            "daily_loss_pct": 0.3,
            "orders_today": 0,
        },
    )
    assert agent_evaluation.status_code == 200, agent_evaluation.text
    evaluation_payload = agent_evaluation.json()
    assert evaluation_payload["broker_submission"] is False
    evaluation = evaluation_payload["evaluation"]
    assert set(evaluation["agent_summary"]) == {"shariah", "quant", "risk"}
    assert evaluation["agent_summary"]["shariah"]["market"] == "MY"
    assert evaluation["agent_summary"]["shariah"]["provider"] == "SC_MY_APPROVED_PUBLICATION"
    # No SC publication is approved/activated in this fixture DB, and the
    # legacy JSON fallback can never assert PASS on its own (Phase 0 audit
    # finding: this used to be a live bypass) -- so an unlisted ticker is
    # correctly UNKNOWN here, not PASS.
    assert evaluation["agent_summary"]["shariah"]["status"] == "UNKNOWN"
    assert evaluation["agent_summary"]["risk"]["status"] == "PASS"
    # The fixture price series produces a genuine S001 BUY (trend + breakout).
    # It did not always: this assertion previously expected NO_SIGNAL, which was
    # true only while the fixture held fewer than MIN_BARS=200 bars and SMA200
    # could not be computed at all. Expanding that fixture to 205 bars gave the
    # quant agent enough history to emit a real signal, and this assertion was
    # never updated to match.
    #
    # Asserting BUY here is the stronger test anyway: Shariah is UNKNOWN while
    # quant is bullish and risk passes, so the Shariah blocker proves the gate is
    # decisive on its own rather than merely contributing one reason to an
    # already-doomed order.
    assert evaluation["agent_summary"]["quant"]["signal"] == "BUY"
    assert evaluation["decision"] == "BLOCKED"

    # Two blockers, and the second one is the point. This fixture DB has no
    # Alpaca credentials, so the quant leg falls back to fixture_prices() and
    # reports data_freshness "fixture". That label used to be reported and
    # ignored -- a market-data outage could produce a BUY computed from test data
    # that cleared every gate. It now blocks.
    #
    # Note what this test is really demonstrating: the BUY above was computed
    # from synthetic prices all along. The signal was never evidence of anything
    # about the market; it was evidence about a fixture file.
    assert evaluation["agent_summary"]["quant"]["data_freshness"] == "fixture"
    assert evaluation["blockers"] == ["shariah_rejected", "synthetic_market_data"]

    ready_candidate = evaluate_candidate(
        symbol="0001",
        side="BUY",
        quantity=2,
        price=50.0,
        position_pct=1.0,
        total_exposure_pct=5.0,
        loss_per_trade_pct=0.2,
        daily_loss_pct=0.3,
        orders_today=0,
        # This is a direct unit-test call (not an HTTP request), so a
        # shariah_override here is the intended test seam, not a bypass: no
        # untrusted caller can reach this path with a fabricated verdict --
        # see test_approval_shariah_bypass.py for why /paper/approval itself
        # can no longer be tricked this way.
        shariah_override={
            "agent": "shariah",
            "market": "MY",
            "provider": "TEST_FIXTURE",
            "status": "PASS",
            "symbol": "0001",
            "reason": "test_override",
        },
        quant_override={
            "agent": "quant",
            "status": "PASS",
            "symbol": "0001",
            "signal": "BUY",
            "reason": "test_override",
            "price": 50.0,
            "bars": 200,
            "price_source": "test",
            "strategy": {"signal": "BUY"},
        },
    )
    assert ready_candidate["decision"] == "READY_FOR_APPROVAL"
    assert ready_candidate["broker_submission"] is False
    assert ready_candidate["agent_summary"]["shariah"]["status"] == "PASS"
    assert ready_candidate["agent_summary"]["risk"]["status"] == "PASS"

    original_paper_execution_enabled = os.environ.get("PAPER_EXECUTION_ENABLED")
    os.environ["PAPER_EXECUTION_ENABLED"] = "true"
    try:
        fixture_preview = client.post(
            "/paper/preview",
            json={
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "price": 1.0,
                "position_pct": 1.0,
                "total_exposure_pct": 5.0,
                "loss_per_trade_pct": 0.2,
                "daily_loss_pct": 0.3,
                "orders_today": 0,
                "test_fixture": True,
            },
        )
    finally:
        if original_paper_execution_enabled is None:
            os.environ.pop("PAPER_EXECUTION_ENABLED", None)
        else:
            os.environ["PAPER_EXECUTION_ENABLED"] = original_paper_execution_enabled
    assert fixture_preview.status_code == 200, fixture_preview.text
    fixture_preview_payload = fixture_preview.json()
    assert fixture_preview_payload["preview"]["status"] == "READY_FOR_APPROVAL"
    assert fixture_preview_payload["preview"]["broker_submission"] is False
    assert (
        fixture_preview_payload["preview"]["agent_summary"]["shariah"]["provider"]
        == "PAPER_TEST_FIXTURE"
    )
    assert (
        fixture_preview_payload["preview"]["agent_summary"]["quant"]["price_source"]
        == "paper_test_fixture"
    )

    original_evaluate_candidate = local_api.evaluate_candidate
    local_api.evaluate_candidate = lambda **kwargs: {
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 1,
        "price": 333.74,
        "notional": 333.74,
        "decision": "READY_FOR_APPROVAL",
        "blockers": [],
        "broker_submission": False,
        "agent_summary": {
            "shariah": {
                "agent": "shariah",
                "market": "US",
                "provider": "SEC_EDGAR",
                "status": "PASS",
                "symbol": "AAPL",
                "reason": "COMPLIANT",
                "details": {"status": "COMPLIANT", "symbol": "AAPL"},
            },
            "quant": {
                "agent": "quant",
                "status": "PASS",
                "symbol": "AAPL",
                "signal": "BUY",
                "reason": "test_override",
                "price": 333.74,
                "bars": 218,
                "latest_date": "2026-07-21",
                "price_source": "tiingo",
                "data_freshness": "live",
                "strategy": {"signal": "BUY"},
            },
            "risk": {
                "agent": "risk_engine",
                "status": "PASS",
                "reason": "risk_limits_passed",
                "details": {"status": "PASS", "checks": {}},
            },
        },
    }
    try:
        ready_preview = client.post(
            "/paper/preview",
            json={
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "price": 333.74,
                "position_pct": 1.0,
                "total_exposure_pct": 5.0,
                "loss_per_trade_pct": 0.2,
                "daily_loss_pct": 0.3,
                "orders_today": 0,
            },
        )
    finally:
        local_api.evaluate_candidate = original_evaluate_candidate
    assert ready_preview.status_code == 200, ready_preview.text
    ready_preview_payload = ready_preview.json()
    assert ready_preview_payload["broker_submission"] is False
    assert ready_preview_payload["preview"]["status"] == "READY_FOR_APPROVAL"
    assert ready_preview_payload["preview"]["broker_submission"] is False
    assert ready_preview_payload["preview"]["agent_summary"]["shariah"]["provider"] == "SEC_EDGAR"
    assert ready_preview_payload["preview"]["quote_snapshot"]["symbol"] == "AAPL"
    assert ready_preview_payload["preview"]["quote_snapshot"]["latest_close"] == 333.74
    assert ready_preview_payload["preview"]["quote_snapshot"]["latest_date"] == "2026-07-21"
    assert ready_preview_payload["preview"]["quote_snapshot"]["source"] == "tiingo"
    assert ready_preview_payload["preview"]["quote_snapshot"]["data_freshness"] == "live"

    ready_approval = client.post(
        "/paper/approval",
        json={"preview": ready_preview_payload["preview"], "approved": True},
    )
    assert ready_approval.status_code == 200, ready_approval.text
    ready_approval_payload = ready_approval.json()
    assert ready_approval_payload["broker_submission"] is False
    assert ready_approval_payload["queue_id"] > 0
    assert ready_approval_payload["approval"]["status"] == "APPROVED_PAPER_READY", (
        ready_approval_payload
    )
    assert ready_approval_payload["approval"]["paper_execution_enabled"] is False
    assert ready_approval_payload["approval"]["broker_submission"] is False

    approvals = client.get("/approvals")
    assert approvals.status_code == 200, approvals.text
    approvals_payload = approvals.json()
    assert approvals_payload
    latest_approval = approvals_payload[0]
    assert latest_approval["symbol"] == "AAPL"
    assert latest_approval["side"] == "BUY"
    assert latest_approval["approval_status"] == "APPROVED_PAPER_READY"
    assert latest_approval["execution_environment"] == "SIMULATE"
    assert latest_approval["broker_submission"] is False
    assert latest_approval["shariah_status"] == "PASS"
    assert latest_approval["shariah_market"] == "US"
    assert latest_approval["quant_signal"] == "BUY"
    assert latest_approval["risk_status"] == "PASS"
    latest_payload = json.loads(latest_approval["payload"])
    assert latest_payload["preview"]["quote_snapshot"]["source"] == "tiingo"
    assert latest_payload["preview"]["quote_snapshot"]["latest_date"] == "2026-07-21"

    missing_confirmation = client.post(
        f"/paper/execute/{ready_approval_payload['queue_id']}", json={}
    )
    assert missing_confirmation.status_code == 200, missing_confirmation.text
    missing_confirmation_payload = missing_confirmation.json()
    assert missing_confirmation_payload["status"] == "CONFIRMATION_REQUIRED"
    assert missing_confirmation_payload["required_confirmation"] == "EXECUTE PAPER"
    assert missing_confirmation_payload["broker_submission"] is False

    wrong_confirmation = client.post(
        f"/paper/execute/{ready_approval_payload['queue_id']}",
        json={"confirmation_phrase": "execute paper"},
    )
    assert wrong_confirmation.status_code == 200, wrong_confirmation.text
    wrong_confirmation_payload = wrong_confirmation.json()
    assert wrong_confirmation_payload["status"] == "CONFIRMATION_REQUIRED"
    assert wrong_confirmation_payload["broker_submission"] is False

    locked_execution = client.post(
        f"/paper/execute/{ready_approval_payload['queue_id']}",
        json={"confirmation_phrase": "EXECUTE PAPER"},
    )
    assert locked_execution.status_code == 200, locked_execution.text
    locked_execution_payload = locked_execution.json()
    assert locked_execution_payload["status"] == "EXECUTION_LOCKED"
    assert locked_execution_payload["paper_execution_enabled"] is False
    assert locked_execution_payload["broker_submission"] is False

    preview = client.post(
        "/paper/preview",
        json={
            "symbol": "0001",
            "side": "BUY",
            "quantity": 2,
            "price": 50.0,
            "position_pct": 1.0,
            "total_exposure_pct": 5.0,
            "loss_per_trade_pct": 0.2,
            "daily_loss_pct": 0.3,
            "orders_today": 0,
        },
    )
    assert preview.status_code == 200, preview.text
    preview_payload = preview.json()
    assert preview_payload["broker_submission"] is False
    assert preview_payload["preview"]["status"] == "REJECT"
    assert preview_payload["preview"]["broker_submission"] is False
    # No SC publication is approved/activated in this fixture DB (see the
    # earlier /agent/evaluate assertions above for why this is UNKNOWN, not
    # PASS), and the quant leg runs on fixture prices because this fixture has no
    # Alpaca credentials. Two independent grounds for refusal, each sufficient on
    # its own: the compliance gate rejects on its own authority, and synthetic
    # market data is refused regardless of what any other gate says.
    assert preview_payload["preview"]["agent_summary"]["shariah"]["status"] == "UNKNOWN"
    assert preview_payload["preview"]["agent_summary"]["risk"]["status"] == "PASS"
    assert preview_payload["preview"]["blockers"] == [
        "shariah_rejected",
        "synthetic_market_data",
    ]

    approval = client.post(
        "/paper/approval",
        json={"preview": preview_payload["preview"], "approved": True},
    )
    assert approval.status_code == 200, approval.text
    approval_payload = approval.json()
    assert approval_payload["broker_submission"] is False
    assert approval_payload["queue_id"] > 0
    assert approval_payload["approval"]["broker_submission"] is False
    assert approval_payload["approval"]["paper_execution_enabled"] is False
    assert approval_payload["approval"]["status"] == "REJECT"

    rejected_execution = client.post(
        f"/paper/execute/{approval_payload['queue_id']}",
        json={"confirmation_phrase": "EXECUTE PAPER"},
    )
    assert rejected_execution.status_code == 200, rejected_execution.text
    rejected_execution_payload = rejected_execution.json()
    assert rejected_execution_payload["execution_status"] == "NOT_APPROVED"
    assert rejected_execution_payload["broker_submission"] is False

    print("PASS: local API smoke contract is safe for dashboard use.")


if __name__ == "__main__":
    import auth

    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api

        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(
        username="project_owner", role="admin"
    )
    try:
        main()
    finally:
        _my_app.dependency_overrides.pop(_get_owner_actor, None)
