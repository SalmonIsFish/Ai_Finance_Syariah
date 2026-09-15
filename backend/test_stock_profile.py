"""Verify the read-only stock profile contract."""

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import local_api
from portfolio_store import apply_fill_to_position, ensure_portfolio_tables
from watchlist_store import save_opportunity_scan


fixture_dir = tempfile.TemporaryDirectory()
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"


def seed_profile_data() -> None:
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    connection = local_api.db()
    try:
        ensure_portfolio_tables(connection)
        apply_fill_to_position(
            connection,
            symbol="AAPL",
            account_suffix="1234",
            account_type="MARGIN",
            side="BUY",
            quantity=2,
            avg_price=100.0,
        )
        save_opportunity_scan(
            connection,
            scan_id=42,
            scanned_at="2026-07-25T00:00:00+00:00",
            scan={
                "items": [
                    {
                        "symbol": "AAPL",
                        "decision": "BLOCKED",
                        "ready_for_approval": False,
                        "blockers": ["quant_no_buy_signal"],
                        "price": 110.0,
                        "trigger_price": 120.0,
                        "breakout_gap_pct": -8.3333,
                        "watch_status": "NEAR_BREAKOUT",
                        "alert_status": "NONE",
                    }
                ]
            },
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    seed_profile_data()
    client = TestClient(local_api.app)
    original_evaluate_shariah = local_api.evaluate_shariah
    original_summarize_history = local_api.summarize_history
    try:
        local_api.evaluate_shariah = lambda symbol: {
            "agent": "shariah",
            "market": "US",
            "provider": "TEST",
            "status": "PASS",
            "symbol": symbol,
            "reason": "COMPLIANT",
        }
        local_api.summarize_history = lambda symbol, **kwargs: {
            "symbol": symbol.strip().upper(),
            "source": "test_price",
            "bars": 220,
            "min_bars": kwargs["min_bars"],
            "enough_history": True,
            "latest_date": "2026-07-24",
            "latest_close": 110.0,
            "start_date": "2025-07-25",
            "end_date": "2026-07-25",
        }
        response = client.get("/stock/aapl/profile")
    finally:
        local_api.evaluate_shariah = original_evaluate_shariah
        local_api.summarize_history = original_summarize_history

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK"
    assert payload["symbol"] == "AAPL"
    assert payload["market"] == "US"
    assert payload["shariah"]["status"] == "PASS"
    assert payload["market_data"]["latest_close"] == 110.0
    assert payload["latest_opportunity"]["symbol"] == "AAPL"
    assert payload["latest_opportunity"]["payload"]["watch_status"] == "NEAR_BREAKOUT"
    assert payload["portfolio"]["quantity"] == 2.0
    assert payload["portfolio"]["cost_basis"] == 200.0
    assert payload["portfolio"]["market_value"] == 220.0
    assert payload["portfolio"]["account_exposure_pct"] == 2.2
    assert payload["risk_limits"]["max_position_pct"] == 5.0
    assert payload["errors"] == []
    print("PASS: stock profile contract combines symbol, market, scan, and portfolio data.")


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
