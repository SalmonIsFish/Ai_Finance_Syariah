"""Phase 3 deterministic Portfolio Decision Engine."""

import sqlite3
import json
from datetime import datetime, timezone
import p3_portfolio_engine
from screening_api import screen_ticker, risk_limits
import yahoo_finance


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def authoritative_revalidation(
    connection: sqlite3.Connection,
    portfolio_id: int,
    ticker: str,
    side: str,
    requested_price: float,
    proposed_quantity: float | None = None,
    for_execution: bool = False,
) -> dict:
    """The central authoritative validation pipeline: Account -> Shariah -> Risk -> Constraints."""

    # 1. Account Gate / Portfolio state
    portfolio = p3_portfolio_engine.get_portfolio(connection, portfolio_id)
    if portfolio["status"] != "ACTIVE":
        return {"status": "BLOCKED", "reason": "portfolio_not_active", "details": portfolio}

    # 2. Shariah Gate
    screen_result = screen_ticker(ticker)
    shariah = screen_result.get("shariah", {})
    if shariah.get("status") != "PASS":
        return {
            "status": "BLOCKED",
            "reason": f"shariah_gate_failed: {shariah.get('status')}",
            "details": shariah,
        }

    # 3. Market Data
    # For execution, allow_stale_cache MUST be False. For proposal, it can be True.
    today_iso = datetime.now(timezone.utc).date().isoformat()
    try:
        bars, source = yahoo_finance.fetch_eod_prices(
            ticker,
            start_date=today_iso,
            end_date=today_iso,
            allow_fallback=False if for_execution else True,
            allow_stale_cache=False,
        )
        if not bars:
            return {
                "status": "BLOCKED",
                "reason": "market_data_unavailable",
                "details": {"ticker": ticker},
            }
    except Exception:
        return {
            "status": "BLOCKED",
            "reason": "market_data_unavailable",
            "details": {"ticker": ticker},
        }
    current_price = bars[-1]["close"]

    # 4. Risk Gate & Sizing
    limits = risk_limits().get("limits", {})
    cash = portfolio["current_cash"]
    paper_account_equity = limits.get("paper_account_equity", cash)

    # If equity is provided by limits config but we want to use the local paper portfolio's actual equity:
    positions = p3_portfolio_engine.get_portfolio_positions(connection, portfolio_id)

    max_position_pct = limits.get("max_position_pct", 5.0)
    max_total_exposure_pct = limits.get("max_total_exposure_pct", 25.0)
    max_sector_exposure_pct = limits.get("max_sector_exposure_pct", 20.0)
    max_loss_per_trade_pct = limits.get("max_loss_per_trade_pct", None)

    from backend.pure_risk import (
        RiskState,
        RiskPolicy,
        calculate_target_quantity,
        LossPerUnitRequiredError,
    )

    total_exposure = 0.0
    sector_exposure = {}
    pos_map = {p["ticker"]: p["quantity"] for p in positions}
    sector = shariah.get("sector") or "UNKNOWN"

    # Assemble explicit, contemporaneous valuations for every existing position
    for p in positions:
        try:
            bars, _ = yahoo_finance.fetch_eod_prices(
                p["ticker"],
                start_date=today_iso,
                end_date=today_iso,
                allow_fallback=False if for_execution else True,
                allow_stale_cache=False,
            )
            if not bars:
                return {
                    "status": "BLOCKED",
                    "reason": "portfolio_valuation_unavailable",
                    "details": {"ticker": p["ticker"]},
                }
        except Exception:
            return {
                "status": "BLOCKED",
                "reason": "portfolio_valuation_unavailable",
                "details": {"ticker": p["ticker"]},
            }

        pos_price = bars[-1]["close"]
        pos_market_value = pos_price * p["quantity"]

        total_exposure += pos_market_value

        s = p.get("sector") or "UNKNOWN"
        sector_exposure[s] = sector_exposure.get(s, 0.0) + pos_market_value

    actual_equity = cash + total_exposure

    state = RiskState(
        cash=cash,
        equity=actual_equity,
        positions=pos_map,
        sector_exposure=sector_exposure,
        total_exposure=total_exposure,
    )

    policy = RiskPolicy(
        max_position_pct=max_position_pct,
        max_total_exposure_pct=max_total_exposure_pct,
        max_sector_exposure_pct=max_sector_exposure_pct,
        max_loss_per_trade_pct=max_loss_per_trade_pct,
    )

    # loss_per_unit is the currency of downside per share -- entry price minus a
    # stop price. No stop-loss or exit-distance model exists anywhere in this
    # codebase (only entry signals are implemented, and S001's exit is a
    # next-session technical rule, not a fixed distance from entry), so there is
    # no real stop to subtract. Passing None here is what made every P3 BUY
    # return BLOCKED / loss_per_unit_required.
    #
    # Use the candidate's full notional as a worst-case bound: for a long-only,
    # unlevered, no-short position the most a single trade can lose is what was
    # paid for it. Conservative, not precise -- and deliberately the same answer
    # this repo already gave for the /paper path (local_api.py's
    # authoritative_risk_verdict, docs/PHASE2A_REPORT.md), so the two sizing
    # paths cannot disagree about what a 0.5% per-trade limit means.
    #
    # Do NOT derive this from max_loss_per_trade_pct. That percentage is already
    # the numerator inside calculate_target_quantity
    # (equity * pct/100) / loss_per_unit, so loss_per_unit = price * pct/100
    # cancels it to equity/price -- the cash limit -- silently turning the
    # per-trade loss cap into a no-op while still looking like it is enforced.
    try:
        maximum_quantity = calculate_target_quantity(
            state,
            ticker,
            current_price,
            sector,
            policy,
            side,
            loss_per_unit=current_price,
        )
    except LossPerUnitRequiredError as e:
        return {"status": "BLOCKED", "reason": str(e), "details": {}}

    # If the user proposed a quantity, validate it
    validated_qty = maximum_quantity
    if proposed_quantity is not None:
        if proposed_quantity > maximum_quantity:
            return {
                "status": "BLOCKED",
                "reason": "risk_limit_breach",
                "details": {
                    "proposed_quantity": proposed_quantity,
                    "maximum_quantity": maximum_quantity,
                },
            }
        validated_qty = proposed_quantity

    if validated_qty <= 0:
        return {"status": "BLOCKED", "reason": "zero_quantity_allowed", "details": {}}

    return {
        "status": "PASS",
        "ticker": ticker,
        "side": side,
        "quantity": validated_qty,
        "price": current_price,
        "shariah": shariah,
        "equity": actual_equity,
        "limits_applied": limits,
    }


def propose_order(
    connection: sqlite3.Connection, portfolio_id: int, ticker: str, side: str, actor: str = "SYSTEM"
) -> dict:
    # Proposal time validation
    validation = authoritative_revalidation(
        connection, portfolio_id, ticker, side, requested_price=0.0
    )
    if validation["status"] != "PASS":
        raise ValueError(f"Proposal blocked: {validation['reason']}")

    qty = validation["quantity"]
    price = validation["price"]
    shariah = validation["shariah"]

    cursor = connection.execute(
        """
        INSERT INTO p3_portfolio_orders (
            portfolio_id, ticker, side, quantity, requested_price, status, submitted_at, actor,
            shariah_publication_id, shariah_verdict
        ) VALUES (?, ?, ?, ?, ?, 'PROPOSED', ?, ?, ?, ?)
        """,
        (
            portfolio_id,
            ticker,
            side,
            qty,
            price,
            utc_now(),
            actor,
            shariah.get("publication_id"),
            shariah.get("status"),
        ),
    )
    connection.commit()
    return p3_portfolio_engine.get_order(connection, cursor.lastrowid)


def approve_order(connection: sqlite3.Connection, order_id: int, actor: str) -> dict:
    order = p3_portfolio_engine.get_order(connection, order_id)
    if order["status"] != "PROPOSED":
        raise ValueError("Only PROPOSED orders can be approved")

    # Approval time revalidation
    validation = authoritative_revalidation(
        connection,
        order["portfolio_id"],
        order["ticker"],
        order["side"],
        requested_price=order["requested_price"],
        proposed_quantity=order["quantity"],
    )
    if validation["status"] != "PASS":
        # Reject the order
        p3_portfolio_engine.update_order_status(
            connection,
            order_id,
            "PROPOSED",
            "REJECTED",
            {"rejection_reason": validation["reason"], "actor": actor},
        )
        raise ValueError(f"Approval validation failed: {validation['reason']}")

    # Transition
    return p3_portfolio_engine.update_order_status(
        connection, order_id, "PROPOSED", "APPROVED", {"actor": actor}
    )


def execute_order(connection: sqlite3.Connection, order_id: int, actor: str) -> dict:
    # Atomic paper execution
    # Ensure isolation/exclusive transaction if possible in SQLite, connection should be operating in IMMEDIATE or EXCLUSIVE
    # connection.execute("BEGIN IMMEDIATE")
    try:
        order = p3_portfolio_engine.get_order(connection, order_id)
        if order["status"] != "APPROVED":
            raise ValueError("Only APPROVED orders can be executed")

        # Execution time revalidation
        validation = authoritative_revalidation(
            connection,
            order["portfolio_id"],
            order["ticker"],
            order["side"],
            requested_price=order["requested_price"],
            proposed_quantity=order["quantity"],
            for_execution=True,
        )
        if validation["status"] != "PASS":
            raise ValueError(f"Execution validation failed: {validation['reason']}")

        qty = validation["quantity"]
        exec_price = validation["price"]
        side = order["side"]
        portfolio_id = order["portfolio_id"]
        ticker = order["ticker"]
        shariah = validation["shariah"]

        notional = qty * exec_price

        # Update cash
        portfolio = p3_portfolio_engine.get_portfolio(connection, portfolio_id)
        new_cash = (
            portfolio["current_cash"] - notional
            if side == "BUY"
            else portfolio["current_cash"] + notional
        )
        if new_cash < 0:
            raise ValueError("Insufficient cash during execution")

        connection.execute(
            "UPDATE p3_portfolios SET current_cash = ?, updated_at = ? WHERE id = ?",
            (new_cash, utc_now(), portfolio_id),
        )

        # Create fill
        cursor = connection.execute(
            """
            INSERT INTO p3_portfolio_fills (order_id, portfolio_id, ticker, side, quantity, executed_price, filled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, portfolio_id, ticker, side, qty, exec_price, utc_now()),
        )

        # Update position
        existing_pos = connection.execute(
            "SELECT * FROM p3_portfolio_positions WHERE portfolio_id = ? AND ticker = ?",
            (portfolio_id, ticker),
        ).fetchone()
        if side == "BUY":
            if existing_pos:
                old_qty = existing_pos["quantity"]
                old_cost = existing_pos["average_cost"]
                new_qty = old_qty + qty
                new_avg_cost = (
                    ((old_qty * old_cost) + (qty * exec_price)) / new_qty if new_qty > 0 else 0
                )
                connection.execute(
                    "UPDATE p3_portfolio_positions SET quantity = ?, average_cost = ?, sector = ?, updated_at = ? WHERE id = ?",
                    (new_qty, new_avg_cost, shariah.get("sector"), utc_now(), existing_pos["id"]),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO p3_portfolio_positions (portfolio_id, ticker, quantity, average_cost, market_value, unrealized_pnl, realized_pnl, sector, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        portfolio_id,
                        ticker,
                        qty,
                        exec_price,
                        qty * exec_price,
                        shariah.get("sector"),
                        utc_now(),
                    ),
                )
        else:  # SELL
            if not existing_pos or existing_pos["quantity"] < qty:
                raise ValueError("Insufficient position quantity during execution")
            old_qty = existing_pos["quantity"]
            old_cost = existing_pos["average_cost"]
            new_qty = old_qty - qty
            realized_pnl_change = (exec_price - old_cost) * qty

            connection.execute(
                "UPDATE p3_portfolio_positions SET quantity = ?, realized_pnl = realized_pnl + ?, updated_at = ? WHERE id = ?",
                (new_qty, realized_pnl_change, utc_now(), existing_pos["id"]),
            )

        # Write evidence reference
        evidence = {
            "type": "p3_paper_execution",
            "order_id": order_id,
            "portfolio_id": portfolio_id,
            "ticker": ticker,
            "side": side,
            "executed_quantity": qty,
            "executed_price": exec_price,
            "shariah_context": shariah,
            "actor": actor,
        }
        evidence_ref = json.dumps(evidence)

        # Update order to EXECUTED
        order = p3_portfolio_engine.update_order_status(
            connection,
            order_id,
            "APPROVED",
            "EXECUTED",
            {"executed_at": utc_now(), "evidence_reference": evidence_ref},
        )

        connection.commit()
        return order

    except Exception as e:
        connection.rollback()
        # Record rejection if possible outside the transaction block, but here we just raise
        raise e
