"""Verify the read-only market overview contract."""

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import local_api
from portfolio_store import apply_fill_to_position, ensure_portfolio_tables
from watchlist_store import save_opportunity_scan, save_watchlist_settings


fixture_dir = tempfile.TemporaryDirectory()
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"


def seed_market_overview_data() -> None:
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    connection = local_api.db()
    try:
        save_watchlist_settings(connection, symbols=["AAPL", "MSFT", "TSLA", "NVDA"], alert_threshold_pct=2.5)
        ensure_portfolio_tables(connection)
        apply_fill_to_position(
            connection,
            symbol="AAPL",
            account_suffix="1234",
            account_type="MARGIN",
            side="BUY",
            quantity=1,
            avg_price=100.0,
        )
        save_opportunity_scan(
            connection,
            scan_id=88,
            scanned_at="2026-07-25T00:00:00+00:00",
            scan={
                "items": [
                    {
                        "symbol": "AAPL",
                        "decision": "READY_FOR_APPROVAL",
                        "ready_for_approval": True,
                        "blockers": [],
                        "price": 110.0,
                        "trigger_price": 108.0,
                        "breakout_gap_pct": 1.8519,
                        "distance_to_trigger": -2.0,
                        "shariah_status": "PASS",
                        "quant_signal": "BUY",
                        "risk_status": "PASS",
                        "watch_status": "READY",
                        "alert_status": "TRIGGERED",
                        "price_source": "tiingo",
                        "data_freshness": "live",
                        "cache_age_hours": None,
                        "bars": 220,
                    },
                    {
                        "symbol": "MSFT",
                        "decision": "BLOCKED",
                        "ready_for_approval": False,
                        "blockers": ["quant_no_buy_signal"],
                        "price": 50.0,
                        "trigger_price": 52.0,
                        "breakout_gap_pct": -3.8462,
                        "distance_to_trigger": 2.0,
                        "shariah_status": "PASS",
                        "quant_signal": "NO_SIGNAL",
                        "risk_status": "PASS",
                        "watch_status": "NEAR_BREAKOUT",
                        "alert_status": "ALERT",
                        "price_source": "tiingo_cache_after_error",
                        "data_freshness": "cached",
                        "cache_age_hours": 30.0,
                        "bars": 220,
                    },
                    {
                        "symbol": "TSLA",
                        "decision": "BLOCKED",
                        "ready_for_approval": False,
                        "blockers": ["market_data_unavailable"],
                        "price": None,
                        "trigger_price": None,
                        "breakout_gap_pct": None,
                        "distance_to_trigger": None,
                        "shariah_status": None,
                        "quant_signal": "NO_SIGNAL",
                        "risk_status": None,
                        "watch_status": "DATA_ERROR",
                        "alert_status": "DATA_ERROR",
                        "price_source": "unavailable",
                        "data_freshness": "unavailable",
                        "cache_age_hours": None,
                        "bars": 0,
                    },
                ]
            },
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    seed_market_overview_data()
    client = TestClient(local_api.app)
    original_summarize_history = local_api.summarize_history
    try:
        local_api.summarize_history = lambda symbol, **kwargs: {
            "symbol": symbol.strip().upper(),
            "source": "test_price",
            "bars": 220,
            "min_bars": kwargs["min_bars"],
            "enough_history": True,
            "latest_date": "2026-07-25",
            "latest_close": 110.0,
        }
        response = client.get("/market-overview?stale_cache_hours=24")
    finally:
        local_api.summarize_history = original_summarize_history

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK"
    assert payload["trading_mode"] == "approval"
    assert payload["broker_submission"] is False
    assert payload["watchlist"]["symbols"] == ["AAPL", "MSFT", "TSLA", "NVDA"]
    assert payload["watchlist"]["count"] == 4
    assert payload["watchlist"]["unscanned_symbols"] == ["NVDA"]
    assert payload["latest_scan"]["scanned_at"] == "2026-07-25T00:00:00+00:00"
    assert payload["latest_scan"]["scanned_count"] == 3
    assert payload["latest_scan"]["coverage_pct"] == 75.0
    assert payload["counts"]["ready"] == 1
    assert payload["counts"]["alerts"] == 1
    assert payload["counts"]["near_breakout"] == 1
    assert payload["counts"]["data_errors"] == 1
    assert payload["data_health"]["freshness_counts"]["live"] == 1
    assert payload["data_health"]["freshness_counts"]["cached"] == 1
    assert payload["data_health"]["freshness_counts"]["unavailable"] == 1
    assert payload["data_health"]["source_counts"]["tiingo"] == 1
    assert payload["data_health"]["stale_cache_symbols"] == ["MSFT"]
    assert payload["portfolio"]["open_positions"] == 1
    assert payload["portfolio"]["total_exposure"] == 110.0
    assert payload["ready_candidates"][0]["symbol"] == "AAPL"
    assert payload["alert_candidates"][0]["symbol"] == "MSFT"
    assert payload["data_error_candidates"][0]["symbol"] == "TSLA"
    assert payload["recent_alert_events"]
    print("PASS: market overview contract summarizes watchlist health and scan freshness.")


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
