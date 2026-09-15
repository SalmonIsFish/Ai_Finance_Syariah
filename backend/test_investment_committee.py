"""Verify the read-only Investment Committee aggregate contract."""

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import local_api
from approval_queue import record_approval
from portfolio_store import apply_fill_to_position, ensure_portfolio_tables
from watchlist_store import save_opportunity_scan, save_watchlist_settings


fixture_dir = tempfile.TemporaryDirectory()
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"


def ready_preview(symbol: str = "AAPL") -> dict:
    return {
        "status": "READY_FOR_APPROVAL",
        "execution": "PAPER_ONLY",
        "broker_submission": False,
        "symbol": symbol,
        "side": "BUY",
        "quantity": 1,
        "price": 110.0,
        "notional": 110.0,
        "blockers": [],
        "quote_snapshot": {
            "symbol": symbol,
            "latest_close": 110.0,
            "latest_date": "2026-07-25",
            "source": "test_price",
        },
        "agent_summary": {
            "shariah": {"status": "PASS", "market": "US", "provider": "TEST"},
            "quant": {"status": "PASS", "signal": "BUY"},
            "risk": {"status": "PASS"},
        },
    }


def seed_committee_data() -> None:
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    connection = local_api.db()
    try:
        save_watchlist_settings(connection, symbols=["AAPL", "MSFT"], alert_threshold_pct=2.0)
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
            scan_id=77,
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
                        "shariah_status": "PASS",
                        "quant_signal": "BUY",
                        "risk_status": "PASS",
                        "watch_status": "READY",
                        "alert_status": "TRIGGERED",
                    },
                    {
                        "symbol": "MSFT",
                        "decision": "BLOCKED",
                        "ready_for_approval": False,
                        "blockers": ["quant_no_buy_signal"],
                        "price": 50.0,
                        "trigger_price": 52.0,
                        "breakout_gap_pct": -3.8462,
                        "shariah_status": "PASS",
                        "quant_signal": "NO_SIGNAL",
                        "risk_status": "PASS",
                        "watch_status": "NEAR_BREAKOUT",
                        "alert_status": "ALERT",
                    },
                ]
            },
        )
        record_approval(
            connection,
            preview=ready_preview(),
            approval={
                "status": "APPROVED_PAPER_READY",
                "broker_submission": False,
                "execution_environment": "SIMULATE",
            },
            approved_by_user=True,
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    seed_committee_data()
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
            "latest_close": 110.0 if symbol.strip().upper() == "AAPL" else 50.0,
        }
        response = client.get("/investment-committee?limit=10")
    finally:
        local_api.summarize_history = original_summarize_history

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK"
    assert payload["trading_mode"] == "approval"
    assert payload["broker_submission"] is False
    assert payload["watchlist"]["symbols"] == ["AAPL", "MSFT"]
    assert payload["counts"]["candidates"] == 2
    assert payload["counts"]["ready_for_review"] == 1
    assert payload["counts"]["watch_alerts"] == 1
    assert payload["counts"]["pending_approvals"] == 1
    assert payload["counts"]["open_positions"] == 1
    candidates = {candidate["symbol"]: candidate for candidate in payload["candidates"]}
    assert candidates["AAPL"]["committee_status"] == "READY_FOR_REVIEW"
    assert candidates["AAPL"]["recent_approvals"][0]["approval_status"] == "APPROVED_PAPER_READY"
    assert candidates["MSFT"]["committee_status"] == "WATCH_ALERT"
    assert candidates["MSFT"]["blockers"] == ["quant_no_buy_signal"]
    assert payload["pending_approvals"][0]["symbol"] == "AAPL"
    assert payload["portfolio"]["total_exposure"] == 110.0
    assert payload["portfolio"]["total_exposure_pct"] == 1.1
    assert payload["risk_limits"]["max_total_exposure_pct"] == 25.0
    print("PASS: investment committee contract aggregates candidates, approvals, and portfolio risk.")


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
