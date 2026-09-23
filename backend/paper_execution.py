"""Locked-safe paper execution workflow.

This module validates the queue row and execution gates before delegating to a
configured adapter. Broker submission remains opt-in through configuration.
"""

import sqlite3
import json

from alpaca_paper_adapter import ALPACA_ADAPTERS, check_alpaca_status
from alpaca_paper_adapter import reconcile_paper_order as reconcile_alpaca_order
from alpaca_paper_adapter import submit_paper_order as submit_alpaca_order
from approval_queue import (
    get_approval,
    record_broker_reconciliation,
    record_broker_submission,
    update_execution_status,
)
from broker_routing import CODE_NO_ADAPTER, adapter_for
from config import load_settings
from moomoo_status import check_moomoo_status
from moomoo_paper_adapter import (
    broker_submission_from_approval,
    reconcile_paper_order,
    submit_paper_order,
)
from portfolio_store import open_position_quantity
from trading_modes import mode_capabilities


def execute_paper_order(connection: sqlite3.Connection, queue_id: int) -> dict:
    settings = load_settings()
    approval = get_approval(connection, queue_id)
    if approval is None:
        return {"status": "NOT_FOUND", "queue_id": queue_id, "broker_submission": False}

    if approval.get("approval_status") != "APPROVED_PAPER_READY":
        result = update_execution_status(
            connection,
            queue_id,
            status="NOT_APPROVED",
            message="approval_status_must_be_APPROVED_PAPER_READY",
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "broker_submission": False,
        }

    if approval.get("broker_submission"):
        return {
            "id": queue_id,
            "status": "ALREADY_SUBMITTED",
            "queue_id": queue_id,
            "broker_submission": True,
            "execution_status": approval.get("execution_status"),
            "execution_message": approval.get("execution_message")
            or "broker_submission_already_recorded",
            "executed_at": approval.get("executed_at"),
        }

    capabilities = mode_capabilities(settings.trading_mode)
    if not capabilities["paper_execution_allowed"]:
        result = update_execution_status(
            connection,
            queue_id,
            status="TRADING_MODE_BLOCKED",
            message=f"TRADING_MODE {settings.trading_mode} does not allow paper execution",
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "trading_mode": settings.trading_mode,
            "broker_submission": False,
        }

    if not settings.paper_execution_enabled:
        result = update_execution_status(
            connection,
            queue_id,
            status="EXECUTION_LOCKED",
            message="PAPER_EXECUTION_ENABLED is false",
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "paper_execution_enabled": False,
            "broker_submission": False,
        }

    if approval.get("shariah_status") != "PASS":
        result = update_execution_status(
            connection,
            queue_id,
            status="SHARIAH_GATE_FAILED",
            message="shariah_status_must_be_PASS",
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "broker_submission": False,
        }

    if approval.get("risk_status") != "PASS":
        result = update_execution_status(
            connection,
            queue_id,
            status="RISK_GATE_FAILED",
            message="risk_status_must_be_PASS",
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "broker_submission": False,
        }

    audit_gate = validate_approval_payload_for_execution(approval)
    if audit_gate["status"] != "PASS":
        result = update_execution_status(
            connection,
            queue_id,
            status="APPROVAL_AUDIT_FAILED",
            message="; ".join(audit_gate["errors"]),
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "broker_submission": False,
            "approval_audit": audit_gate,
        }

    # Which broker submits this order is decided per market, not per process: Alpaca has
    # no Malaysian access and Moomoo is the only Bursa route. See broker_routing.py,
    # which is the execution twin of market_data.provider_for.
    routing = adapter_for(approval, settings)
    if routing["status"] != "PASS":
        result = update_execution_status(
            connection,
            queue_id,
            # The pre-existing status is kept for "execution is simply off", so the
            # common case reads exactly as it always did. The new status is reserved
            # for the genuinely new condition: configured, but not for this market.
            status=(
                "ADAPTER_NOT_CONFIGURED"
                if routing.get("code") == CODE_NO_ADAPTER
                else "ADAPTER_NOT_CONFIGURED_FOR_MARKET"
            ),
            message=routing["reason"],
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "broker_submission": False,
            "routing": routing,
        }

    order_adapter = routing["adapter"]
    order_market = routing["market"]
    # `moomoo` holds whichever paper broker was chosen; the key name is kept for the
    # existing execute-response contract. PAPER environment == Alpaca paper account.
    # The probe must match the adapter that will actually submit -- gating a Bursa order
    # on whether the *Alpaca* account is ready is a precondition with nothing to do with
    # the order, and a refusal whose stated cause is not the real one is worse than none.
    use_alpaca = order_adapter in ALPACA_ADAPTERS
    moomoo = check_alpaca_status() if use_alpaca else check_moomoo_status(order_market)
    if (
        not moomoo.get("paper_account_ready")
        or moomoo.get("environment") not in {"SIMULATE", "PAPER"}
        or moomoo.get("account_status") != "ACTIVE"
    ):
        result = update_execution_status(
            connection,
            queue_id,
            status="BROKER_NOT_READY" if use_alpaca else "MOOMOO_NOT_READY",
            message=moomoo.get("reason") or moomoo.get("status", "moomoo_not_ready"),
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "moomoo": moomoo,
            "broker_submission": False,
        }

    sell_gate = validate_sell_reduction(connection, approval, moomoo)
    if sell_gate["status"] != "PASS":
        result = update_execution_status(
            connection,
            queue_id,
            status="PORTFOLIO_SELL_GATE_FAILED",
            message=sell_gate["reason"],
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "broker_submission": False,
            "moomoo": moomoo,
            "portfolio_gate": sell_gate,
        }

    submit = submit_alpaca_order if use_alpaca else submit_paper_order
    broker_response = submit(approval, moomoo, order_adapter)
    if not broker_response.get("broker_submission"):
        result = update_execution_status(
            connection,
            queue_id,
            status=broker_response.get("status", "ADAPTER_NOT_CONFIGURED"),
            message=broker_response.get("reason", "paper execution adapter did not submit"),
        )
        return {
            **result,
            "status": result["execution_status"],
            "queue_id": queue_id,
            "paper_execution_enabled": True,
            "broker_submission": False,
            "moomoo": moomoo,
            "adapter": broker_response,
        }

    result = record_broker_submission(connection, queue_id, broker_response=broker_response)
    return {
        **result,
        "status": result["execution_status"],
        "queue_id": queue_id,
        "paper_execution_enabled": True,
        "broker_submission": True,
        "moomoo": moomoo,
    }


def parse_payload(approval: dict) -> dict:
    payload = approval.get("payload")
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        return {}
    try:
        parsed = json.loads(payload)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def validate_approval_payload_for_execution(approval: dict) -> dict:
    payload = parse_payload(approval)
    errors = []
    preview = payload.get("preview")
    approval_payload = payload.get("approval")
    if not isinstance(preview, dict):
        errors.append("payload.preview_missing")
        preview = {}
    if not isinstance(approval_payload, dict):
        errors.append("payload.approval_missing")
        approval_payload = {}

    quote = preview.get("quote_snapshot")
    if not isinstance(quote, dict):
        errors.append("payload.preview.quote_snapshot_missing")
    else:
        for field in ["symbol", "latest_close", "source"]:
            if quote.get(field) in {None, ""}:
                errors.append(f"payload.preview.quote_snapshot.{field}_missing")

    agents = preview.get("agent_summary")
    if not isinstance(agents, dict):
        errors.append("payload.preview.agent_summary_missing")
        agents = {}
    shariah = agents.get("shariah") if isinstance(agents.get("shariah"), dict) else {}
    risk = agents.get("risk") if isinstance(agents.get("risk"), dict) else {}
    if shariah.get("status") != "PASS":
        errors.append("payload.preview.agent_summary.shariah.status_must_be_PASS")
    if risk.get("status") != "PASS":
        errors.append("payload.preview.agent_summary.risk.status_must_be_PASS")

    if approval_payload.get("status") != "APPROVED_PAPER_READY":
        errors.append("payload.approval.status_must_be_APPROVED_PAPER_READY")
    blockers = preview.get("blockers")
    if blockers:
        errors.append("payload.preview.blockers_must_be_empty")
    errors.extend(row_payload_consistency_errors(approval, preview, approval_payload, quote))

    return {
        "status": "PASS" if not errors else "REJECT",
        "errors": errors,
        "quote_snapshot": quote if isinstance(quote, dict) else None,
    }


def row_payload_consistency_errors(
    approval: dict, preview: dict, approval_payload: dict, quote: dict | None
) -> list[str]:
    errors = []
    if normalize_text(preview.get("symbol")) != normalize_text(approval.get("symbol")):
        errors.append("payload.preview.symbol_mismatch")
    if normalize_text(preview.get("side")) != normalize_text(approval.get("side")):
        errors.append("payload.preview.side_mismatch")
    if not numbers_equal(preview.get("quantity"), approval.get("quantity")):
        errors.append("payload.preview.quantity_mismatch")
    if not numbers_equal(preview.get("price"), approval.get("price")):
        errors.append("payload.preview.price_mismatch")
    if not numbers_equal(preview.get("notional"), approval.get("notional")):
        errors.append("payload.preview.notional_mismatch")
    if approval_payload.get("execution_environment") != approval.get("execution_environment"):
        errors.append("payload.approval.execution_environment_mismatch")
    if isinstance(quote, dict) and normalize_text(quote.get("symbol")) != normalize_text(
        approval.get("symbol")
    ):
        errors.append("payload.preview.quote_snapshot.symbol_mismatch")
    candidate = approval_payload.get("candidate")
    if isinstance(candidate, dict):
        if normalize_text(candidate.get("symbol")) != normalize_text(approval.get("symbol")):
            errors.append("payload.approval.candidate.symbol_mismatch")
        if normalize_text(candidate.get("side")) != normalize_text(approval.get("side")):
            errors.append("payload.approval.candidate.side_mismatch")
        if not numbers_equal(candidate.get("quantity"), approval.get("quantity")):
            errors.append("payload.approval.candidate.quantity_mismatch")
        if not numbers_equal(candidate.get("price"), approval.get("price")):
            errors.append("payload.approval.candidate.price_mismatch")
        if not numbers_equal(candidate.get("notional"), approval.get("notional")):
            errors.append("payload.approval.candidate.notional_mismatch")
    return errors


def normalize_text(value) -> str:
    return str(value or "").strip().upper()


def numbers_equal(left, right, *, tolerance: float = 0.0001) -> bool:
    try:
        return abs(float(left) - float(right)) <= tolerance
    except (TypeError, ValueError):
        return left == right


def validate_sell_reduction(connection: sqlite3.Connection, approval: dict, moomoo: dict) -> dict:
    side = str(approval.get("side") or "BUY").upper()
    if side != "SELL":
        return {"status": "PASS", "side": side}

    # A sell-to-open option (covered call, cash-secured put) has no local equity
    # position by definition -- ownership/collateral is proven by
    # option_structure_gate and account_shariah_gate at approval time, not by this
    # equity-only ledger check. Checking this here would reject every legitimate
    # option order that isn't coincidentally sitting on top of an equal or larger
    # equity position in the same symbol.
    preview = parse_payload(approval).get("preview")
    asset_class = (preview or {}).get("asset_class", "equity")
    if asset_class == "option":
        return {"status": "PASS", "side": side, "asset_class": asset_class}

    symbol = str(approval.get("symbol") or "").upper()
    quantity = float(approval.get("quantity") or 0)
    account_suffix = moomoo.get("account_suffix")
    available_quantity = open_position_quantity(
        connection, symbol=symbol, account_suffix=account_suffix
    )
    if quantity <= 0:
        return {
            "status": "REJECT",
            "reason": "sell_quantity_must_be_positive",
            "symbol": symbol,
            "requested_quantity": quantity,
            "available_quantity": available_quantity,
            "account_suffix": account_suffix,
        }
    if available_quantity <= 0:
        return {
            "status": "REJECT",
            "reason": f"no local {symbol} position is available for account {account_suffix}",
            "symbol": symbol,
            "requested_quantity": quantity,
            "available_quantity": available_quantity,
            "account_suffix": account_suffix,
        }
    if quantity > available_quantity:
        return {
            "status": "REJECT",
            "reason": f"sell quantity {quantity:g} exceeds local {symbol} position {available_quantity:g} for account {account_suffix}",
            "symbol": symbol,
            "requested_quantity": quantity,
            "available_quantity": available_quantity,
            "account_suffix": account_suffix,
        }
    return {
        "status": "PASS",
        "side": side,
        "symbol": symbol,
        "requested_quantity": quantity,
        "available_quantity": available_quantity,
        "account_suffix": account_suffix,
    }


def reconcile_for_approval(approval: dict) -> dict:
    """Reconcile through the adapter that actually placed the order, not the current setting."""
    stored = broker_submission_from_approval(approval) or {}
    adapter = str(stored.get("adapter") or load_settings().paper_execution_adapter)
    if adapter in ALPACA_ADAPTERS:
        return reconcile_alpaca_order(approval)
    return reconcile_paper_order(approval)


def reconcile_submitted_paper_order(connection: sqlite3.Connection, queue_id: int) -> dict:
    approval = get_approval(connection, queue_id)
    if approval is None:
        return {"status": "NOT_FOUND", "queue_id": queue_id, "broker_submission": False}

    if not approval.get("broker_submission"):
        return {
            "status": "BROKER_NOT_SUBMITTED",
            "queue_id": queue_id,
            "broker_submission": False,
            "reason": "approval row has no broker submission",
        }

    reconciliation = reconcile_for_approval(approval)
    if not reconciliation.get("broker_submission"):
        return {
            "status": reconciliation.get("status", "BROKER_RECONCILE_ERROR"),
            "queue_id": queue_id,
            "broker_submission": False,
            "adapter": reconciliation,
        }

    result = record_broker_reconciliation(connection, queue_id, reconciliation=reconciliation)
    return {
        **result,
        "status": result["execution_status"],
        "queue_id": queue_id,
        "broker_submission": True,
    }
