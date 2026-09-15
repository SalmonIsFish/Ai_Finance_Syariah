"""Verify portfolio-derived exposure blocks unsafe paper approvals."""

import os
import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
os.environ["PAPER_ACCOUNT_EQUITY"] = "1000"
os.environ["MAX_POSITION_PCT"] = "35"
os.environ["MAX_TOTAL_EXPOSURE_PCT"] = "35"
os.environ["MAX_LOSS_PER_TRADE_PCT"] = "0.5"
os.environ["MAX_DAILY_LOSS_PCT"] = "1.0"
os.environ["MAX_ORDERS_PER_DAY"] = "5"
# Pinned rather than inherited: this file asserts exact percentages, so it must
# not start failing the day the shipped default is retuned.
os.environ["MAX_SECTOR_EXPOSURE_PCT"] = "10"

import local_api
from local_api import app
from portfolio_store import ensure_portfolio_tables


def seed_position(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    ensure_portfolio_tables(connection)
    connection.execute(
        """
        INSERT INTO paper_positions (
            symbol,
            account_suffix,
            account_type,
            quantity,
            average_cost,
            cost_basis,
            realized_pnl,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("AAPL", "1740", "MARGIN", 1.0, 250.0, 250.0, 0.0, "2026-07-24T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()


def main() -> None:
    fixture_dir = tempfile.TemporaryDirectory()
    db_path = Path(fixture_dir.name) / "paper_trading.db"
    local_api.DB_PATH = db_path
    seed_position(db_path)

    original_evaluate_candidate = local_api.evaluate_candidate
    original_summarize_history = local_api.summarize_history

    def fake_evaluate_candidate(**kwargs):
        side = kwargs["side"].strip().upper()
        sell_blockers = ["only_buy_side_supported", "quant_no_buy_signal"] if side == "SELL" else []
        return {
            "symbol": kwargs["symbol"].strip().upper(),
            "side": side,
            "quantity": kwargs["quantity"],
            "price": kwargs["price"],
            "notional": round(kwargs["quantity"] * kwargs["price"], 2),
            "decision": "BLOCKED" if sell_blockers else "READY_FOR_APPROVAL",
            "blockers": sell_blockers,
            "broker_submission": False,
            "agent_summary": {
                "shariah": {
                    "agent": "shariah",
                    "market": "US",
                    "provider": "TEST",
                    "status": "PASS",
                },
                "quant": {
                    "agent": "quant",
                    "status": "PASS" if side == "BUY" else "NO_SIGNAL",
                    "signal": "BUY" if side == "BUY" else "NO_SIGNAL",
                    "price": kwargs["price"],
                    "bars": 220,
                    "price_source": "test",
                    "strategy": {"breakout_gap_pct": -3.5, "breakout_level": 333.74},
                },
                "risk": {
                    "agent": "risk_engine",
                    "status": "PASS",
                    "reason": "risk_limits_passed",
                    "details": {"status": "PASS", "checks": {}},
                },
            },
        }

    local_api.evaluate_candidate = fake_evaluate_candidate
    local_api.summarize_history = lambda symbol, **kwargs: {
        "symbol": symbol,
        "source": "test_price",
        "latest_date": "2026-07-24",
        "latest_close": 300.0,
    }

    try:
        client = TestClient(app)
        portfolio = client.get("/portfolio")
        assert portfolio.status_code == 200, portfolio.text
        portfolio_payload = portfolio.json()
        assert portfolio_payload["paper_account_equity"] == 1000.0
        assert portfolio_payload["total_exposure"] == 300.0
        assert portfolio_payload["total_exposure_pct"] == 30.0
        assert portfolio_payload["positions"][0]["account_exposure_pct"] == 30.0
        assert portfolio_payload["risk_limits"]["max_position_pct"] == 35.0
        assert portfolio_payload["risk_limits"]["max_total_exposure_pct"] == 35.0

        preview = client.post(
            "/paper/preview",
            json={
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 1,
                "price": 100.0,
                "position_pct": 0.0,
                "total_exposure_pct": 0.0,
                "loss_per_trade_pct": 0.2,
                "daily_loss_pct": 0.3,
                "orders_today": 0,
            },
        )
        assert preview.status_code == 200, preview.text
        preview_payload = preview.json()
        blocked_preview = preview_payload["preview"]
        assert blocked_preview["status"] == "REJECT"
        assert "portfolio_position_limit" in blocked_preview["blockers"]
        assert "portfolio_total_exposure_limit" in blocked_preview["blockers"]
        assert "portfolio_existing_position" in blocked_preview["blockers"]
        assert "blocker_messages" in blocked_preview
        assert any(
            "above the 35.00% position limit" in item["message"]
            for item in blocked_preview["blocker_messages"]
        )
        assert blocked_preview["quote_snapshot"]["quote_snapshot_source"] == "market_data"
        assert blocked_preview["quote_snapshot"]["latest_close"] == 300.0
        assert blocked_preview["quote_snapshot"]["source"] == "test_price"
        # Sector concentration. Neither AAPL nor anything held has a screen on
        # record here, so both sit in the Unclassified bucket -- which is capped
        # like any other sector precisely so missing data cannot hide a
        # concentrated book. 400/1000 = 40% against the 10% default.
        assert "portfolio_sector_concentration_limit" in blocked_preview["blockers"]
        assert any(
            "Unclassified exposure to 40.00%" in item["message"]
            and "above the 10.00% sector limit" in item["message"]
            for item in blocked_preview["blocker_messages"]
        ), blocked_preview["blocker_messages"]

        portfolio_risk = blocked_preview["agent_summary"]["risk"]["details"]["portfolio"]
        sector = portfolio_risk["sector_concentration"]
        assert sector["status"] == "REJECT", sector
        assert sector["sector"] == "Unclassified", sector
        assert sector["projected_pct"] == 40.0, sector
        assert sector["max_sector_pct"] == 10.0, sector
        assert portfolio_risk["limits"]["max_sector_exposure_pct"] == 10.0, portfolio_risk

        assert portfolio_risk["projected_position_exposure"] == 400.0
        assert portfolio_risk["projected_total_exposure"] == 400.0
        assert portfolio_risk["projected_position_pct"] == 40.0
        assert portfolio_risk["projected_total_exposure_pct"] == 40.0

        approval = client.post(
            "/paper/approval", json={"preview": blocked_preview, "approved": True}
        )
        assert approval.status_code == 200, approval.text
        approval_payload = approval.json()
        assert approval_payload["approval"]["status"] == "REJECT"
        assert approval_payload["approval"]["reason"] == "preview_not_ready_for_approval"

        reduce_preview = client.post(
            "/paper/preview",
            json={
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 1,
                "price": 100.0,
                "position_pct": 30.0,
                "total_exposure_pct": 30.0,
                "loss_per_trade_pct": 0.2,
                "daily_loss_pct": 0.3,
                "orders_today": 0,
            },
        )
        assert reduce_preview.status_code == 200, reduce_preview.text
        reduce_payload = reduce_preview.json()["preview"]
        assert reduce_payload["status"] == "READY_FOR_APPROVAL"
        assert reduce_payload["blockers"] == []
        reduce_risk = reduce_payload["agent_summary"]["risk"]["details"]["portfolio"]
        assert reduce_risk["projected_position_quantity"] == 0
        assert reduce_risk["projected_position_exposure"] == 0.0
        assert reduce_risk["projected_total_exposure_pct"] == 0.0
        assert "portfolio_reduce_position" in reduce_risk["warnings"]
        # A SELL out of an over-concentrated sector must never be blocked for
        # being in that sector: reducing it is the cure, not the disease.
        assert "portfolio_sector_concentration_limit" not in reduce_payload["blockers"]
        reduce_sector = reduce_risk["sector_concentration"]
        assert reduce_sector["sector"] == "Unclassified", reduce_sector
        # A SELL adds nothing to the sector, so the projection is the book as it
        # stands (300), not the book plus the sell notional (400). The projection
        # is deliberately BUY-side only -- it answers "would this order create
        # concentration", and a SELL never can.
        assert reduce_sector["projected_exposure"] == 300.0, reduce_sector
        assert reduce_sector["projected_pct"] == 30.0, reduce_sector

        reduce_approval = client.post(
            "/paper/approval", json={"preview": reduce_payload, "approved": True}
        )
        assert reduce_approval.status_code == 200, reduce_approval.text
        reduce_approval_payload = reduce_approval.json()
        assert reduce_approval_payload["approval"]["status"] == "APPROVED_PAPER_READY"
        assert reduce_approval_payload["approval"]["candidate"]["side"] == "SELL"
        assert reduce_approval_payload["approval"]["candidate"]["signal"] == "SELL"
    finally:
        local_api.evaluate_candidate = original_evaluate_candidate
        local_api.summarize_history = original_summarize_history
        fixture_dir.cleanup()

    print("PASS: portfolio exposure limits block unsafe paper approvals.")


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
