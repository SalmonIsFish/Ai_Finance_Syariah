"""Verify /paper/approval routes Alpaca option orders through the Shariah gate chain.

Nothing here contacts Alpaca: check_alpaca_status is replaced with canned account
state, exactly as test_alpaca_execution_wiring.py does for the execution path.
"""

import json
import os
import pathlib
import shutil
import tempfile

import local_api
import portfolio_store


CASH_ACCOUNT = {
    "status": "paper_account_ready",
    "paper_account_ready": True,
    "environment": "PAPER",
    "account_type": "CASH",
    "account_status": "ACTIVE",
    "account_suffix": "0TCX",
    "cash": 25000.0,
    "options_trading_level": 3,
    "broker_submission": False,
}

MARGIN_ACCOUNT = {**CASH_ACCOUNT, "account_type": "MARGIN"}
BROKE_ACCOUNT = {**CASH_ACCOUNT, "cash": 500.0}

SHARIAH_PASS = {
    "agent": "shariah",
    "status": "PASS",
    "provider": "ZOYA",
    "market": "US",
    "reason": "COMPLIANT",
}


def covered_call_preview(**overrides) -> dict:
    preview = {
        "status": "READY_FOR_APPROVAL",
        "execution": "PAPER_ONLY",
        "broker_submission": False,
        "symbol": "AAPL",
        "side": "SELL",
        "quantity": 1,
        "price": 8.55,
        "notional": 855.0,
        "blockers": [],
        "asset_class": "option",
        "option_contract": {
            "strategy": "COVERED_CALL",
            "underlying": "AAPL",
            "expiration": "2026-09-18",
            "option_type": "CALL",
            "strike": 310.0,
        },
        "quote_snapshot": {"symbol": "AAPL", "latest_close": 309.21, "source": "alpaca"},
        "agent_summary": {
            "shariah": SHARIAH_PASS,
            "quant": {"status": "PASS", "signal": "SELL"},
            "risk": {"status": "PASS"},
        },
    }
    preview.update(overrides)
    return preview


def cash_secured_put_preview(**overrides) -> dict:
    return covered_call_preview(
        symbol="MSFT",
        price=3.10,
        notional=310.0,
        option_contract={
            "strategy": "CASH_SECURED_PUT",
            "underlying": "MSFT",
            "expiration": "2026-10-16",
            "option_type": "PUT",
            "strike": 402.5,
        },
        **overrides,
    )


def approve(preview: dict) -> dict:
    request = local_api.PaperApprovalRequest(preview=preview, approved=True)
    return local_api.approve_paper_order(request)


def seed_shares(quantity: float, *, symbol: str = "AAPL", account_suffix: str = "0TCX") -> None:
    connection = local_api.db()
    try:
        portfolio_store.ensure_portfolio_tables(connection)
        connection.execute("DELETE FROM paper_positions")
        if quantity:
            portfolio_store.apply_fill_to_position(
                connection,
                symbol=symbol,
                account_suffix=account_suffix,
                account_type="CASH",
                side="BUY",
                quantity=quantity,
                avg_price=300.0,
            )
        connection.commit()
    finally:
        connection.close()


def check_covered_call_on_cash_account_is_approved() -> None:
    local_api.check_alpaca_status = lambda: CASH_ACCOUNT
    seed_shares(100)

    result = approve(covered_call_preview())
    approval = result["approval"]
    assert approval["status"] == "APPROVED_PAPER_READY", approval
    assert result["queue_id"]

    # The gate chain must have seen the real structure, not an equity order.
    candidate = approval.get("candidate") or {}
    structure = candidate.get("option_structure") or {}
    assert structure.get("structure") == "covered_call", structure
    assert structure.get("shares_held") == 100, "shares must come from the local ledger"
    assert structure.get("contracts") == 1
    assert candidate.get("account_type") == "CASH"


def check_covered_call_on_margin_account_is_rejected() -> None:
    """The margin/Riba hole: borrowing at interest is impermissible."""
    local_api.check_alpaca_status = lambda: MARGIN_ACCOUNT
    seed_shares(100)

    approval = approve(covered_call_preview())["approval"]
    assert approval["status"] == "REJECT", approval
    assert approval["reason"] == "margin_account_not_permitted", approval
    assert approval["broker_submission"] is False


def check_uncovered_call_is_rejected() -> None:
    local_api.check_alpaca_status = lambda: CASH_ACCOUNT
    seed_shares(0)

    approval = approve(covered_call_preview())["approval"]
    assert approval["status"] == "REJECT", approval
    assert approval["reason"] == "option_structure_rejected", approval


def check_under_collateralized_put_is_rejected() -> None:
    """A cash-secured put needs strike x 100 x contracts in settled cash."""
    local_api.check_alpaca_status = lambda: BROKE_ACCOUNT  # $500 against a $40,250 obligation
    seed_shares(0)

    approval = approve(cash_secured_put_preview())["approval"]
    assert approval["status"] == "REJECT", approval
    assert approval["reason"] == "option_structure_rejected", approval

    # The same put is fine once the cash is actually there.
    local_api.check_alpaca_status = lambda: {**CASH_ACCOUNT, "cash": 45000.0}
    ok = approve(cash_secured_put_preview())["approval"]
    assert ok["status"] == "APPROVED_PAPER_READY", ok
    assert (ok.get("candidate") or {}).get("option_structure", {}).get("cash_collateral") == 45000.0


def check_approval_response_carries_shariah_trace() -> None:
    """The dashboard's Ticket tab reads this string directly -- don't lose it."""
    local_api.check_alpaca_status = lambda: CASH_ACCOUNT
    seed_shares(100)

    approved = approve(covered_call_preview())["approval"]
    assert "structure=covered_call -> PASS" in approved["shariah_trace"], approved
    assert "account=CASH -> PASS" in approved["shariah_trace"], approved

    seed_shares(0)
    rejected = approve(covered_call_preview())["approval"]
    assert "structure=covered_call -> REJECT" in rejected["shariah_trace"], rejected


def check_equity_path_is_unchanged() -> None:
    """A plain equity approval must still work and carry no option structure."""
    local_api.check_alpaca_status = lambda: CASH_ACCOUNT
    seed_shares(0)

    equity = covered_call_preview(
        side="BUY",
        quantity=1,
        price=309.21,
        notional=309.21,
        asset_class="equity",
        option_contract=None,
    )
    equity["agent_summary"] = {
        "shariah": SHARIAH_PASS,
        "quant": {"status": "PASS", "signal": "BUY"},
        "risk": {"status": "PASS"},
    }
    approval = approve(equity)["approval"]
    assert approval["status"] == "APPROVED_PAPER_READY", approval
    assert "option_structure" not in (approval.get("candidate") or {})


def check_preview_endpoint_carries_option_intent() -> None:
    """/paper/preview must put the requested option contract onto the preview itself.

    Built separately from the hand-made previews above, which would otherwise hide a
    missing carry-through in the endpoint.
    """
    original_evaluate = local_api.evaluate_preview_request
    original_quote = local_api.quote_snapshot_for_preview
    local_api.evaluate_preview_request = lambda request: {
        "decision": "READY_FOR_APPROVAL",
        "symbol": "AAPL",
        "quantity": request.quantity,
        "price": 8.55,
        "notional": 855.0,
        "blockers": [],
        "blocker_messages": [],
        "agent_summary": {
            "shariah": SHARIAH_PASS,
            "quant": {"status": "PASS", "signal": "SELL"},
            "risk": {"status": "PASS"},
        },
    }
    local_api.quote_snapshot_for_preview = lambda evaluation, request: {
        "symbol": "AAPL",
        "latest_close": 309.21,
        "source": "alpaca",
    }
    try:
        contract = {
            "strategy": "COVERED_CALL",
            "underlying": "AAPL",
            "expiration": "2026-09-18",
            "option_type": "CALL",
            "strike": 310.0,
        }
        request = local_api.PaperPreviewRequest(
            symbol="AAPL",
            side="SELL",
            quantity=1,
            price=8.55,
            position_pct=1.0,
            total_exposure_pct=1.0,
            loss_per_trade_pct=0.1,
            daily_loss_pct=0.1,
            orders_today=0,
            asset_class="option",
            option_contract=contract,
        )
        preview = local_api.preview_paper_order(request)["preview"]
        assert preview["asset_class"] == "option", preview
        assert preview["option_contract"] == contract, preview

        # And an equity request must stay equity with no contract attached.
        equity_request = local_api.PaperPreviewRequest(
            symbol="AAPL",
            side="BUY",
            quantity=1,
            price=309.21,
            position_pct=1.0,
            total_exposure_pct=1.0,
            loss_per_trade_pct=0.1,
            daily_loss_pct=0.1,
            orders_today=0,
        )
        equity_preview = local_api.preview_paper_order(equity_request)["preview"]
        assert equity_preview["asset_class"] == "equity", equity_preview
        assert equity_preview["option_contract"] is None, equity_preview
    finally:
        local_api.evaluate_preview_request = original_evaluate
        local_api.quote_snapshot_for_preview = original_quote


def check_option_intent_survives_into_the_queue() -> None:
    """The adapter reads option fields out of the stored payload, so they must persist."""
    from alpaca_paper_adapter import order_intent_from_approval
    from approval_queue import get_approval

    local_api.check_alpaca_status = lambda: CASH_ACCOUNT
    seed_shares(100)
    queue_id = approve(covered_call_preview())["queue_id"]

    connection = local_api.db()
    try:
        row = get_approval(connection, queue_id)
    finally:
        connection.close()

    assert row is not None
    stored = json.loads(row["payload"])["preview"]
    assert stored["asset_class"] == "option"
    assert stored["option_contract"]["strategy"] == "COVERED_CALL"

    intent = order_intent_from_approval(row)
    assert intent["asset_class"] == "option", (
        "the adapter must see an option order, not an equity one"
    )
    assert intent["option_contract"]["strike"] == 310.0


def main() -> None:
    saved_env = {
        k: os.environ.get(k)
        for k in [
            "PAPER_EXECUTION_ADAPTER",
            "TRADING_MODE",
            "PAPER_EXECUTION_ENABLED",
            "PAPER_ACCOUNT_EQUITY",
        ]
    }
    original_status = local_api.check_alpaca_status
    original_db_path = local_api.DB_PATH

    # Real db() against a throwaway file, so table setup and connection handling
    # run exactly as they do in production.
    temp_dir = tempfile.mkdtemp(prefix="amanah-shariah-wiring-")
    local_api.DB_PATH = str(pathlib.Path(temp_dir) / "wiring.db")

    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "true"
    # Pinned rather than inherited from backend/.env. Approval-time risk
    # re-derivation sizes loss_per_trade_pct as the order's notional over
    # settings.paper_account_equity against MAX_LOSS_PER_TRADE_PCT (0.5%), so
    # check_equity_path_is_unchanged's 1 AAPL at 309.21 needs an account large
    # enough for that to be a legitimate trade. At the 10000 default it is 3.09%
    # and the gate correctly refuses it. Left unset, this file passes or fails
    # according to whatever each developer's .env happens to contain.
    os.environ["PAPER_ACCOUNT_EQUITY"] = "100000"
    try:
        check_covered_call_on_cash_account_is_approved()
        check_covered_call_on_margin_account_is_rejected()
        check_uncovered_call_is_rejected()
        check_under_collateralized_put_is_rejected()
        check_approval_response_carries_shariah_trace()
        check_equity_path_is_unchanged()
        check_preview_endpoint_carries_option_intent()
        check_option_intent_survives_into_the_queue()
    finally:
        local_api.check_alpaca_status = original_status
        local_api.DB_PATH = original_db_path
        shutil.rmtree(temp_dir, ignore_errors=True)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("PASS: option approvals run through the Shariah, structure, and margin gates.")


if __name__ == "__main__":
    main()
