"""Verify the read-only positions API contract."""

import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import local_api
from portfolio_store import apply_fill_to_position, ensure_portfolio_tables


fixture_dir = tempfile.TemporaryDirectory()
import pytest

_ENV_ORIG = {k: os.environ.get(k) for k in ["TRADING_MODE", "PAPER_EXECUTION_ENABLED", "PAPER_EXECUTION_ADAPTER", "MOOMOO_MODE", "PAPER_ACCOUNT_EQUITY"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
    os.environ["MOOMOO_MODE"] = "paper"
    os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"
    yield
    for _k in _ENV_ORIG:
        if _ENV_ORIG[_k] is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _ENV_ORIG[_k]

os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"


def seed_position_data() -> None:
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
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    seed_position_data()
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
        response = client.get("/positions")
    finally:
        local_api.summarize_history = original_summarize_history

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK"
    assert payload["position_count"] == 1
    assert payload["paper_account_equity"] == 10000.0
    assert payload["total_exposure"] == 220.0
    assert payload["total_exposure_pct"] == 2.2
    assert payload["total_exposure_status"] == "PASS"
    assert payload["risk_limits"]["max_position_pct"] == 5.0
    assert payload["valuation_status"] == "VALUED"

    position = payload["positions"][0]
    assert position["symbol"] == "AAPL"
    assert position["quantity"] == 2.0
    assert position["average_cost"] == 100.0
    assert position["cost_basis"] == 200.0
    assert position["latest_price"] == 110.0
    assert position["market_value"] == 220.0
    assert position["unrealized_pnl"] == 20.0
    assert position["account_exposure_pct"] == 2.2
    assert position["position_limit_status"] == "PASS"
    assert position["reduce_eligible"] is True
    assert position["max_reduce_quantity"] == 2.0
    print("PASS: positions API returns flattened exposure and reduce eligibility contract.")


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
