"""Coordinate local deterministic agents before paper-order approval."""

from agents.option_structure_agent import evaluate_option_structure
from agents.quant_agent import evaluate_quant
from agents.risk_engine import evaluate_risk
from agents.shariah_agent import evaluate_shariah


def evaluate_candidate(
    *,
    symbol: str,
    side: str,
    quantity: int,
    price: float | None,
    position_pct: float,
    total_exposure_pct: float,
    loss_per_trade_pct: float,
    daily_loss_pct: float,
    orders_today: int,
    shariah_override: dict | None = None,
    quant_override: dict | None = None,
    option_structure: dict | None = None,
    asset_class: str = "equity",
) -> dict:
    normalized_symbol = symbol.strip().upper()
    normalized_side = side.strip().upper()

    shariah = (
        shariah_override if shariah_override is not None else evaluate_shariah(normalized_symbol)
    )
    quant = quant_override if quant_override is not None else evaluate_quant(normalized_symbol)
    selected_price = price if price is not None else quant.get("price")
    risk = evaluate_risk(
        position_pct=position_pct,
        total_exposure_pct=total_exposure_pct,
        loss_per_trade_pct=loss_per_trade_pct,
        daily_loss_pct=daily_loss_pct,
        orders_today=orders_today,
    )

    option_structure_result = (
        evaluate_option_structure(**option_structure) if option_structure is not None else None
    )

    blockers = []
    # Options are written by selling to open (covered call, cash-secured put)
    # and closed by buying back -- the BUY-only restriction is an equity-only
    # rule. Reduce-only SELL protection for equities lives in local_api.py's
    # portfolio risk overlay, not here.
    if normalized_side != "BUY" and asset_class != "option":
        blockers.append("only_buy_side_supported")
    if shariah["status"] != "PASS":
        blockers.append("shariah_rejected")
    # The quant agent decides whether to open a directional long, so its BUY
    # signal is an equity-entry filter -- the same shape of equity-only rule as
    # the side restriction above. No Level 1 option structure is a directional
    # entry: a covered call is written against stock already owned, a
    # cash-secured put means "willing to own at this price" rather than "this is
    # breaking out today", and buying a short leg back reduces risk. Requiring a
    # breakout for any of them blocks the strategy whenever the underlying is
    # merely calm, which on 2026-08-20 was 19 of 21 liquid large caps.
    #
    # This removes no protection. That the shares are owned and the cash is
    # committed is proven by option_structure_gate and account_shariah_gate at
    # approval time; the signal below is reported either way, just not as a
    # blocker.
    if asset_class != "option" and quant.get("signal") != "BUY":
        blockers.append("quant_no_buy_signal")
    # Synthetic prices must never reach a gate decision. alpaca_market_data
    # falls back to fixture_prices() when Alpaca errors, labels the result
    # "fixture_after_alpaca_error", and until now nothing read that label -- so a
    # market-data outage could produce a BUY computed from test data that passed
    # every gate while truthfully reporting `data_freshness: "fixture"`.
    #
    # Unlike the BUY-signal rule above, this is NOT equity-specific: fabricated
    # prices are wrong for any asset class, so no option exemption.
    #
    # "cached" is allowed -- that is real Alpaca data inside its TTL, and the
    # age is reported. "unknown" blocks, because provenance that cannot be
    # established is not provenance.
    if quant.get("data_freshness") in ("fixture", "unknown"):
        blockers.append("synthetic_market_data")
    if risk["status"] != "PASS":
        blockers.append("risk_rejected")
    if selected_price is None or selected_price <= 0:
        blockers.append("valid_price_required")
    if option_structure_result is not None and option_structure_result["status"] != "PASS":
        blockers.append("option_structure_rejected")

    decision = "READY_FOR_APPROVAL" if not blockers else "BLOCKED"
    notional = round(quantity * selected_price, 2) if selected_price else None
    agent_summary = {
        "shariah": shariah,
        "quant": quant,
        "risk": risk,
    }
    if option_structure_result is not None:
        agent_summary["option_structure"] = option_structure_result
    return {
        "symbol": normalized_symbol,
        "side": normalized_side,
        "quantity": quantity,
        "price": selected_price,
        "notional": notional,
        "decision": decision,
        "blockers": blockers,
        "broker_submission": False,
        "agent_summary": agent_summary,
    }
