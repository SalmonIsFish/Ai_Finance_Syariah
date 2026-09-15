"""End-to-end smoke test for the option order path, driven entirely through the
real FastAPI app (preview -> approval -> execute), not isolated function calls.

Known limitation #4 in CLAUDE.md says this chain has never been exercised live.
This test can't touch the real Alpaca API without real credentials and a real
API call, so it drives the *actual* production code path -- local_api's real
handlers, build_shariah_candidate, approve_candidate, alpaca_paper_adapter's
real order-construction logic -- and only swaps the single network seam
(`alpaca_paper_adapter.alpaca_request`) that CLAUDE.md's testing conventions
say is the one replaceable point. Nothing else is mocked. If the wiring
between local_api.py and the gate chain is broken, this is what would catch
it before a real API call would.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

os.environ["ALPACA_API_KEY_ID"] = "test-key-id"
os.environ["ALPACA_SECRET_KEY"] = "test-secret-key"
os.environ["ALPACA_MODE"] = "paper"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["TRADING_MODE"] = "approval"

import agent_coordinator
import alpaca_paper_adapter
import local_api
from local_api import app
from portfolio_store import apply_fill_to_position, ensure_portfolio_tables


CASH_ACCOUNT_PAYLOAD = {
    "account_number": "PA1234TEST",
    "status": "ACTIVE",
    "multiplier": "1",
    "trading_blocked": False,
    "account_blocked": False,
    "cash": "20000.00",
    "options_trading_level": 1,
}

MARGIN_ACCOUNT_PAYLOAD = {**CASH_ACCOUNT_PAYLOAD, "multiplier": "4"}

LOW_CASH_ACCOUNT_PAYLOAD = {**CASH_ACCOUNT_PAYLOAD, "cash": "500.00"}


class FakeAlpacaNetwork:
    """Swaps the one network seam CLAUDE.md names; nothing else is mocked."""

    def __init__(self):
        self.account_payload = CASH_ACCOUNT_PAYLOAD
        self._next_order_id = 0
        self.calls = []

    def request(self, method, path, *, credentials, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path == "/v2/account":
            return {"ok": True, "status_code": 200, "data": self.account_payload}
        if method == "POST" and path == "/v2/orders":
            self._next_order_id += 1
            return {
                "ok": True,
                "status_code": 200,
                "data": {
                    "id": f"order-{self._next_order_id}",
                    "status": "accepted",
                    "client_order_id": body.get("client_order_id"),
                    "symbol": body.get("symbol"),
                    "side": body.get("side"),
                    "qty": body.get("qty"),
                },
            }
        raise AssertionError(f"unexpected alpaca_request call: {method} {path}")


def option_preview_request(*, strategy: str, option_type: str, strike: float, price: float) -> dict:
    return {
        "symbol": "AAPL",
        "side": "SELL",
        "quantity": 1,
        "price": price,
        "position_pct": 1.0,
        "total_exposure_pct": 5.0,
        "loss_per_trade_pct": 0.2,
        "daily_loss_pct": 0.3,
        "orders_today": 0,
        "test_fixture": True,
        "asset_class": "option",
        "option_contract": {
            "strategy": strategy,
            "option_type": option_type,
            "strike": strike,
            "expiration": "2026-09-18",
            "underlying": "AAPL",
        },
    }


def seed_shares(db_path: Path, *, symbol: str, quantity: float, account_suffix: str) -> None:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        ensure_portfolio_tables(connection)
        apply_fill_to_position(
            connection,
            symbol=symbol,
            account_suffix=account_suffix,
            account_type="CASH",
            side="BUY",
            quantity=quantity,
            avg_price=180.0,
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    fixture_dir = tempfile.TemporaryDirectory()
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    seed_shares(local_api.DB_PATH, symbol="AAPL", quantity=100, account_suffix="TEST")

    fake_network = FakeAlpacaNetwork()
    original_request = alpaca_paper_adapter.alpaca_request
    alpaca_paper_adapter.alpaca_request = fake_network.request

    os.environ["PAPER_EXECUTION_ENABLED"] = "true"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca"
    client = TestClient(app)

    try:
        # --- Scenario 1: covered call, CASH account, enough owned shares ---
        # -> approved and actually reaches BROKER_SUBMITTED through the real
        # preview/approval/execute handlers.
        fake_network.account_payload = CASH_ACCOUNT_PAYLOAD
        preview = client.post(
            "/paper/preview",
            json=option_preview_request(
                strategy="COVERED_CALL", option_type="CALL", strike=210.0, price=3.50
            ),
        )
        assert preview.status_code == 200, preview.text
        preview_payload = preview.json()["preview"]
        assert preview_payload["status"] == "READY_FOR_APPROVAL"
        assert preview_payload["asset_class"] == "option"

        approval = client.post(
            "/paper/approval", json={"preview": preview_payload, "approved": True}
        )
        assert approval.status_code == 200, approval.text
        approval_payload = approval.json()
        assert approval_payload["approval"]["status"] == "APPROVED_PAPER_READY", approval_payload
        queue_id = approval_payload["queue_id"]

        execution = client.post(
            f"/paper/execute/{queue_id}", json={"confirmation_phrase": "EXECUTE PAPER"}
        )
        assert execution.status_code == 200, execution.text
        execution_payload = execution.json()
        assert execution_payload["status"] == "BROKER_SUBMITTED", execution_payload
        assert execution_payload["broker_submission"] is True
        assert execution_payload["broker_response"]["asset_class"] == "option"
        assert execution_payload["broker_response"]["option_strategy"] == "COVERED_CALL"

        # --- Scenario 2: unrecognized option strategy fails closed at approval,
        # never reaching the broker at all. ---
        unsupported_preview = client.post(
            "/paper/preview",
            json=option_preview_request(
                strategy="IRON_CONDOR", option_type="CALL", strike=210.0, price=1.0
            ),
        )
        unsupported_preview_payload = unsupported_preview.json()["preview"]
        unsupported_approval = client.post(
            "/paper/approval", json={"preview": unsupported_preview_payload, "approved": True}
        )
        unsupported_approval_payload = unsupported_approval.json()
        assert unsupported_approval_payload["approval"]["status"] == "REJECT"
        assert unsupported_approval_payload["approval"]["reason"] == "option_structure_rejected"
        assert (
            unsupported_approval_payload["approval"]["option_structure"]["reason"]
            == "unknown_structure"
        )

        # --- Scenario 3: same fully-collateralized covered call, but the
        # account itself is margin-enabled -- must block on the account gate
        # even though the structure gate alone would pass. ---
        fake_network.account_payload = MARGIN_ACCOUNT_PAYLOAD
        margin_preview = client.post(
            "/paper/preview",
            json=option_preview_request(
                strategy="COVERED_CALL", option_type="CALL", strike=210.0, price=3.50
            ),
        )
        margin_preview_payload = margin_preview.json()["preview"]
        margin_approval = client.post(
            "/paper/approval", json={"preview": margin_preview_payload, "approved": True}
        )
        margin_approval_payload = margin_approval.json()
        assert margin_approval_payload["approval"]["status"] == "REJECT"
        assert margin_approval_payload["approval"]["reason"] == "margin_account_not_permitted"

        # --- Scenario 4: cash-secured put without enough settled cash to
        # cover the strike -- blocked even on an otherwise-clean CASH account.
        fake_network.account_payload = LOW_CASH_ACCOUNT_PAYLOAD
        put_preview = client.post(
            "/paper/preview",
            json=option_preview_request(
                strategy="CASH_SECURED_PUT", option_type="PUT", strike=190.0, price=2.10
            ),
        )
        put_preview_payload = put_preview.json()["preview"]
        put_approval = client.post(
            "/paper/approval", json={"preview": put_preview_payload, "approved": True}
        )
        put_approval_payload = put_approval.json()
        assert put_approval_payload["approval"]["status"] == "REJECT"
        assert put_approval_payload["approval"]["reason"] == "option_structure_rejected"
        assert (
            put_approval_payload["approval"]["option_structure"]["reason"]
            == "insufficient_cash_collateral"
        )

        # --- Scenario 5: a real NO_SIGNAL quant verdict must not block a
        # sell-to-open option. Every scenario above sends test_fixture: true,
        # which injects a quant_override of signal "BUY" -- so none of them can
        # see what the quant agent actually says, and that is precisely what hid
        # the 2026-08-20 bug where a fully-collateralized CVX cash-secured put
        # was refused for quant_no_buy_signal alone.
        #
        # Here the fixture is narrowed to the Shariah verdict only (keeping the
        # screen off the network), and the quant agent is swapped for one that
        # reports the real NO_SIGNAL shape. The order must still be approved.
        fake_network.account_payload = CASH_ACCOUNT_PAYLOAD
        original_overrides = local_api.paper_test_overrides
        original_quant = agent_coordinator.evaluate_quant

        def shariah_fixture_only(request):
            overrides = original_overrides(request)
            return {"shariah_override": overrides["shariah_override"]} if overrides else {}

        def quant_no_signal(symbol):
            return {
                "agent": "quant",
                "status": "NO_SIGNAL",
                "signal": "NO_SIGNAL",
                "reason": "strategy_conditions_not_met",
                "price": 205.78,
                "strategy": {"trend_ok": True, "breakout_ok": False, "breakout_gap_pct": -0.91},
            }

        local_api.paper_test_overrides = shariah_fixture_only
        agent_coordinator.evaluate_quant = quant_no_signal
        try:
            calm_preview = client.post(
                "/paper/preview",
                json=option_preview_request(
                    strategy="COVERED_CALL", option_type="CALL", strike=210.0, price=3.50
                ),
            )
            calm_payload = calm_preview.json()["preview"]
            # The signal is reported honestly; it just is not a blocker.
            assert calm_payload["agent_summary"]["quant"]["signal"] == "NO_SIGNAL", calm_payload
            assert "quant_no_buy_signal" not in calm_payload["blockers"], calm_payload
            assert calm_payload["status"] == "READY_FOR_APPROVAL", calm_payload

            calm_approval = client.post(
                "/paper/approval", json={"preview": calm_payload, "approved": True}
            )
            assert calm_approval.json()["approval"]["status"] == "APPROVED_PAPER_READY", (
                calm_approval.json()
            )

            # The same calm underlying still blocks a directional equity entry.
            equity_preview = client.post(
                "/paper/preview",
                json={
                    "symbol": "AAPL",
                    "side": "BUY",
                    "quantity": 1,
                    "price": 205.78,
                    "position_pct": 1.0,
                    "total_exposure_pct": 5.0,
                    "loss_per_trade_pct": 0.2,
                    "daily_loss_pct": 0.3,
                    "orders_today": 0,
                    "test_fixture": True,
                },
            )
            equity_payload = equity_preview.json()["preview"]
            assert "quant_no_buy_signal" in equity_payload["blockers"], equity_payload
            assert equity_payload["status"] != "READY_FOR_APPROVAL", equity_payload
        finally:
            local_api.paper_test_overrides = original_overrides
            agent_coordinator.evaluate_quant = original_quant

        # Confirm the mock was actually exercised, not silently unused.
        assert any(path == "/v2/orders" for _, path, _ in fake_network.calls)
        assert any(path == "/v2/account" for _, path, _ in fake_network.calls)
    finally:
        alpaca_paper_adapter.alpaca_request = original_request

    print(
        "PASS: option order chain approves, blocks, and executes correctly end-to-end via the real API."
    )


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
