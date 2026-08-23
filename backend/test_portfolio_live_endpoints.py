"""Verify the live-account dashboard endpoints without contacting Alpaca.

Covers GET /paper/account, GET /paper/positions/live, GET /portfolio/history/live
and the /portfolio -> /portfolio/history snapshot-recording pipeline. Every seam
(local_api's bound references to alpaca_paper_adapter functions, and
alpaca_market_data.alpaca_data_request underneath portfolio_metrics) is swapped
the same way test_alpaca_execution_wiring.py swaps check_alpaca_status -- by
assigning directly onto local_api's module namespace, since `from x import y`
binds a name there rather than a live reference back to x.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text(
    json.dumps({"validation": {"status": "active"}, "records": []}),
    encoding="utf-8",
)
os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"

# These three import after the fixture above, not before it: local_api reads
# SHARIAH_UNIVERSE_PATH at import time and the file has to exist by then.
import alpaca_market_data  # noqa: E402
import local_api  # noqa: E402
from local_api import app  # noqa: E402


READY_ACCOUNT = {
    "status": "paper_account_ready",
    "paper_account_ready": True,
    "environment": "PAPER",
    "account_type": "CASH",
    "account_status": "ACTIVE",
    "account_suffix": "0TCX",
    "options_trading_level": 1,
    "cash": 99895.08,
    "equity": 99829.40,
    "last_equity": 99854.40,
    "buying_power": 38985.86,
    "portfolio_value": 99829.40,
    "daily_change": -25.0,
    "daily_change_pct": -0.025,
    "broker_submission": False,
}

READY_POSITIONS = {
    "status": "ok",
    "positions": [
        {
            "symbol": "CVX",
            "asset_class": "us_equity",
            "side": "long",
            "quantity": 2.0,
            "average_entry_price": 206.89,
            "current_price": 205.27,
            "market_value": 410.54,
            "unrealized_pnl": -3.47,
            "unrealized_pnl_pct": -0.83,
        }
    ],
}


def check_paper_account_route(client: TestClient) -> None:
    local_api.check_alpaca_status = lambda: READY_ACCOUNT
    response = client.get("/paper/account")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["equity"] == 99829.40, payload
    assert payload["daily_change"] == -25.0, payload
    assert payload["buying_power"] == 38985.86, payload


def check_paper_positions_live_route(client: TestClient) -> None:
    local_api.fetch_broker_positions = lambda: READY_POSITIONS
    response = client.get("/paper/positions/live")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok", payload
    assert payload["positions"][0]["symbol"] == "CVX", payload


def check_portfolio_history_live_credentials_missing(client: TestClient) -> None:
    local_api.alpaca_credentials = lambda: None
    response = client.get("/portfolio/history/live")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "credentials_missing", payload
    assert payload["timestamps"] == [], payload
    assert payload["equity"] == [], payload


def check_portfolio_history_live_ok(client: TestClient) -> None:
    local_api.alpaca_credentials = lambda: {"key_id": "k", "secret_key": "s"}
    calls = []

    def fake_data_request(path, params, *, credentials, base_url=None):
        calls.append({"path": path, "params": params})
        return {
            "ok": True,
            "status_code": 200,
            "data": {
                "timestamp": [1755590400, 1755676800, 1755763200],
                "equity": [99800.0, 99850.0, 99829.40],
                "profit_loss": [0.0, 50.0, 29.40],
                "profit_loss_pct": [0.0, 0.0005, 0.0003],
                "base_value": 99800.0,
                "timeframe": "1D",
            },
        }

    alpaca_market_data.alpaca_data_request = fake_data_request
    response = client.get("/portfolio/history/live?period=1M")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "ok", payload
    assert payload["period"] == "1M", payload
    assert len(payload["equity"]) == 3, payload
    assert payload["equity"][-1] == 99829.40, payload
    assert payload["metrics"]["status"] == "OK", payload["metrics"]
    # The request actually built, not just the response that came back.
    assert calls[0]["path"] == "/v2/account/portfolio/history", calls
    assert calls[0]["params"]["period"] == "1M", calls
    assert calls[0]["params"]["timeframe"] == "1D", calls


def check_portfolio_history_live_intraday_refuses_metrics(client: TestClient) -> None:
    """A 5Min window must not be handed to compute_metrics.

    compute_metrics annualizes by a hardcoded 252 and labels its outputs
    "daily". On 5Min bars that is wrong by sqrt(bars per day) while still
    reading as a daily figure, so the endpoint reports NOT_APPLICABLE rather
    than a plausible-looking wrong Sharpe.
    """
    local_api.alpaca_credentials = lambda: {"key_id": "k", "secret_key": "s"}
    calls = []

    def fake_data_request(path, params, *, credentials, base_url=None):
        calls.append(params)
        return {
            "ok": True,
            "status_code": 200,
            "data": {
                "timestamp": [1755590400, 1755590700, 1755591000],
                "equity": [99800.0, 99810.0, 99805.0],
                "timeframe": "5Min",
            },
        }

    alpaca_market_data.alpaca_data_request = fake_data_request
    response = client.get("/portfolio/history/live?period=1D")
    assert response.status_code == 200, response.text
    payload = response.json()
    # period=1D resolves to 5Min server-side without the caller asking for it.
    assert calls[0]["timeframe"] == "5Min", calls
    assert payload["status"] == "ok", payload
    assert payload["timeframe"] == "5Min", payload
    assert len(payload["equity"]) == 3, payload
    assert payload["metrics"]["status"] == "NOT_APPLICABLE", payload["metrics"]
    assert "sharpe_rf_zero" not in payload["metrics"], payload["metrics"]

    # The daily window still gets real metrics -- this refuses one granularity,
    # it does not disable reporting.
    def daily_data_request(path, params, *, credentials, base_url=None):
        return {
            "ok": True,
            "status_code": 200,
            "data": {
                "timestamp": [1755590400, 1755676800, 1755763200],
                "equity": [99800.0, 99850.0, 99829.40],
                "timeframe": "1D",
            },
        }

    alpaca_market_data.alpaca_data_request = daily_data_request
    daily = client.get("/portfolio/history/live?period=1M").json()
    assert daily["metrics"]["status"] == "OK", daily["metrics"]
    assert daily["metrics"]["sharpe_rf_zero"] is not None, daily["metrics"]


def check_portfolio_history_live_broker_failure(client: TestClient) -> None:
    local_api.alpaca_credentials = lambda: {"key_id": "k", "secret_key": "s"}

    def failing_data_request(path, params, *, credentials, base_url=None):
        return {"ok": False, "status_code": 500, "data": {}, "reason": "http_500"}

    alpaca_market_data.alpaca_data_request = failing_data_request
    response = client.get("/portfolio/history/live")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "unreachable", payload
    assert payload["equity"] == [], payload


def check_portfolio_snapshot_recording(client: TestClient) -> None:
    first = client.get("/portfolio")
    assert first.status_code == 200, first.text

    history = client.get("/portfolio/history")
    assert history.status_code == 200, history.text
    snapshots = history.json()["snapshots"]
    assert len(snapshots) == 1, snapshots
    assert snapshots[0]["account_equity"] == first.json()["paper_account_equity"], snapshots

    # Throttled: a second call within the window records nothing new.
    second = client.get("/portfolio")
    assert second.status_code == 200, second.text
    history_again = client.get("/portfolio/history")
    assert len(history_again.json()["snapshots"]) == 1, history_again.json()


def check_portfolio_survives_a_failed_snapshot_write(client: TestClient) -> None:
    """A locked database must not turn a working portfolio read into a 500.

    The snapshot is bookkeeping hanging off a read, exactly like the audit-log
    write in sec_edgar_screen.check_us_symbol -- which swallows its own failure
    for the same reason. Fail-closed is right wherever this repo makes a
    decision and wrong here, where nothing is being decided.
    """
    original = local_api.record_portfolio_snapshot

    def locked_database(connection, snapshot, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    local_api.record_portfolio_snapshot = locked_database
    try:
        response = client.get("/portfolio")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert "paper_account_equity" in payload, payload
        assert "positions" in payload, payload
    finally:
        local_api.record_portfolio_snapshot = original


def main() -> None:
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    client = TestClient(app)

    original_check_status = local_api.check_alpaca_status
    original_fetch_positions = local_api.fetch_broker_positions
    original_credentials = local_api.alpaca_credentials
    original_data_request = alpaca_market_data.alpaca_data_request
    try:
        check_paper_account_route(client)
        check_paper_positions_live_route(client)
        check_portfolio_history_live_credentials_missing(client)
        check_portfolio_history_live_ok(client)
        check_portfolio_history_live_intraday_refuses_metrics(client)
        check_portfolio_history_live_broker_failure(client)
        check_portfolio_snapshot_recording(client)
        check_portfolio_survives_a_failed_snapshot_write(client)
    finally:
        local_api.check_alpaca_status = original_check_status
        local_api.fetch_broker_positions = original_fetch_positions
        local_api.alpaca_credentials = original_credentials
        alpaca_market_data.alpaca_data_request = original_data_request

    print(
        "PASS: live account/positions/portfolio-history endpoints report real broker data and fail closed."
    )


if __name__ == "__main__":
    main()
