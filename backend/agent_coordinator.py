"""Coordinate local deterministic agents before paper-order approval."""

from agents.option_structure_agent import evaluate_option_structure
from option_permissibility import REASON_NOT_PERMITTED as REASON_OPTION_NOT_PERMITTED
from option_permissibility import check_option_permissibility
from agents.quant_agent import evaluate_quant
from agents.risk_engine import evaluate_risk
from agents.shariah_agent import evaluate_shariah


def _record_decision(result: dict, *, source: str = "scan") -> None:
    """Append this evaluation to the append-only evidence trail.

    The project's claim is that the gate chain *enforces and proves*: an order
    failing any gate cannot be submitted, and every decision is recorded with its
    evidence. The recording half was not true. evidence.py implemented
    build_decision_record() and append_decision() in full, and **nothing ever
    called append_decision** -- only the read path was wired, through
    screening_api.evidence_for_ticker. On 2026-09-22 `data/evidence/` did not
    exist locally or on the droplet, and GET /api/evidence/{ticker} returned
    `{"count": 0, "decisions": []}` for everything. The trail was a read endpoint
    over a file nobody wrote.

    Swappable seam and failures are swallowed, for the same reason
    sec_edgar_screen._record_screen swallows its own audit write: this is
    bookkeeping hanging off a decision that has already been made correctly, and
    a full disk must never turn a working REJECT into an error. Observability,
    not a gate.
    """
    import evidence

    summary = result.get("agent_summary") or {}
    record = evidence.build_decision_record(
        ticker=result.get("symbol", ""),
        shariah_result=summary.get("shariah") or {},
        quant_result=summary.get("quant"),
        risk_result=summary.get("risk"),
        account_result=summary.get("option_structure"),
        final_decision=result.get("decision", ""),
        # The blockers ARE the reason. An empty list means nothing objected,
        # which is itself worth recording rather than leaving blank.
        decision_reason=", ".join(result.get("blockers") or []) or "no_blockers",
        market_data_snapshot={
            "price": result.get("price"),
            "price_source": (summary.get("quant") or {}).get("price_source"),
            "data_freshness": (summary.get("quant") or {}).get("data_freshness"),
            "as_of_date": (summary.get("quant") or {}).get("as_of_date"),
        },
        source=source,
    )
    evidence.append_decision(record)


# Tests swap this to avoid writing to the real evidence trail.
record_decision = _record_decision


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
    record_evidence: bool = True,
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
    # May an option contract be entered into at all? This has to be asked here and not
    # only in option_structure_gate, because /paper/preview builds no `option_structure`
    # -- the structure gate runs at approval time. Without this an option previewed as
    # READY_FOR_APPROVAL and was refused only one step later, which tells the owner an
    # order is viable when nothing can ever approve it.
    option_determination = (option_structure or {}).get("determination")
    if (
        asset_class == "option"
        and check_option_permissibility(determination=option_determination)["status"] != "PASS"
    ):
        blockers.append(REASON_OPTION_NOT_PERMITTED)
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
        # Distinguish "this contract is not permissible at all" from "this structure or
        # its collateral failed". They are different refusals with different remedies:
        # the second can be fixed by sizing, the first cannot be fixed at all until an
        # authority rules. Collapsing them would tell the owner to add collateral for an
        # order that no collateral can make permissible.
        if option_structure_result.get("reason") == REASON_OPTION_NOT_PERMITTED:
            if REASON_OPTION_NOT_PERMITTED not in blockers:
                blockers.append(REASON_OPTION_NOT_PERMITTED)
        else:
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
    result = {
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

    # Record BLOCKED as well as READY_FOR_APPROVAL. A refusal is the decision
    # most worth proving later -- "your system let this through" and "your system
    # stopped this" are both claims that need evidence.
    #
    # `record_evidence=False` is for callers that are not finished deciding.
    # local_api.evaluate_preview_request runs apply_portfolio_risk_overlay after
    # this returns, and that overlay can clear `only_buy_side_supported` and turn
    # a BLOCKED sell into READY_FOR_APPROVAL. Recording here regardless meant an
    # approved, executed SELL was preserved in the trail as BLOCKED -- the
    # evidence contradicting what the system actually did, which for a project
    # whose claim is "enforces and proves" is worse than having no trail, because
    # it looks like proof. Those callers record the final decision themselves.
    if record_evidence:
        try:
            record_decision(result)
        except Exception:
            # Deliberately silent. See _record_decision's docstring: a failed
            # write must not change a decision that was already made correctly.
            pass

    return result
