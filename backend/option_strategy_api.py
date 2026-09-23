"""HTTP view over option_strategy.py: propose a Level 1 contract for a symbol.

option_strategy.py could only be driven from check_option_strategy.py, so a caller
had to hand-build `option_contract` to get an option through /paper/preview. This
module is the missing plumbing, and nothing more -- the selection rules, the policy,
and the rationale all still live in option_strategy.py.

**Selecting is not approving.** A proposed contract has passed no gate. It has not
been screened for the underlying's Shariah status, its structure has not been
checked, the account has not been checked, and no risk limit has been applied. The
payload says so in `next_step`, and hands back the exact /paper/preview body the
caller must post to actually start the gate chain. test_option_strategy.py already
asserts that a selected contract still has to clear everything.

Account facts are passed in by the caller (local_api resolves them through
broker_account_context, the same function the approval path uses) so that settled
cash -- never buying power -- backs a cash-secured put here too.
"""

from market_data import summarize_history
from option_permissibility import check_option_permissibility, determination_summary
from option_strategy import select_cash_secured_put, select_covered_call

SHARES_PER_CONTRACT = 100

STRATEGIES = {"covered_call": "COVERED_CALL", "cash_secured_put": "CASH_SECURED_PUT"}

NOT_APPROVED_NOTE = (
    "A selected contract is a proposal, not an approval. It has cleared no gate: "
    "the underlying has not been screened, the structure has not been checked, the "
    "account has not been checked, and no risk limit has been applied. POST the "
    "preview_request below to /paper/preview, then /paper/approval, to start the "
    "gate chain."
)

GATES_NOT_YET_RUN = ["shariah_gate", "option_structure_gate", "account_shariah_gate", "risk_checks"]


def resolve_strategy(requested: str | None, *, shares_held: int) -> str:
    """Explicit request wins; otherwise write calls if the shares are there.

    Defaulting to a covered call when shares exist keeps the fallback the more
    conservative of the two -- it commits stock already owned rather than cash.
    """
    if requested:
        key = str(requested).strip().lower()
        if key not in STRATEGIES:
            return ""
        return STRATEGIES[key]
    return "COVERED_CALL" if int(shares_held or 0) >= SHARES_PER_CONTRACT else "CASH_SECURED_PUT"


def preview_request_for(symbol: str, selection: dict) -> dict | None:
    """The exact /paper/preview body for this selection, or None if none was made."""
    if selection.get("status") != "SELECTED":
        return None
    return {
        "symbol": symbol,
        "side": selection.get("side", "SELL"),
        "quantity": selection.get("contracts"),
        "price": selection.get("price"),
        "asset_class": "option",
        "option_contract": selection.get("option_contract"),
        # Risk percentages are the caller's to supply; these are placeholders the
        # caller must replace with its own sizing, not defaults that mean anything.
        "position_pct": 0.0,
        "total_exposure_pct": 0.0,
        "loss_per_trade_pct": 0.0,
        "daily_loss_pct": 0.0,
        "orders_today": 0,
    }


def propose_option_strategy(
    symbol: str,
    *,
    account: dict,
    strategy: str | None = None,
    spot: float | None = None,
    policy: dict | None = None,
    quote=None,
    selectors=None,
    determination: dict | None = None,
) -> dict:
    """Propose one Level 1 contract for `symbol`, given live account facts.

    `quote` and `selectors` are test seams; nothing here reaches a network by itself.
    """
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return {"status": "REJECT", "reason": "no symbol supplied", "symbol": ""}

    # Before anything else, and before spending a live option-chain request: may an
    # option contract be entered into at all? This endpoint does not go through
    # option_structure_gate -- selecting is not approving -- so it needs its own check.
    permissibility = check_option_permissibility(determination)
    if permissibility["status"] != "PASS":
        return {
            "status": "REJECT",
            "reason": permissibility["reason"],
            "symbol": normalized,
            "determination": permissibility["determination"],
            "note": determination_summary(),
        }

    shares_held = int(account.get("shares_held") or 0)
    # Settled cash, never buying_power: margin leverage cannot secure a put.
    cash_collateral = float(account.get("cash_collateral") or 0.0)

    resolved = resolve_strategy(strategy, shares_held=shares_held)
    if not resolved:
        return {
            "status": "REJECT",
            "reason": f"unsupported strategy '{strategy}'; Level 1 only",
            "symbol": normalized,
            "supported": sorted(STRATEGIES),
        }

    quote_snapshot = (quote or summarize_history)(normalized, days=30, min_bars=1)
    resolved_spot = spot if spot is not None else quote_snapshot.get("latest_close")
    if not resolved_spot:
        return {
            "status": "REJECT",
            "reason": "no spot price available to anchor strike selection",
            "symbol": normalized,
            "strategy": resolved,
            "quote": quote_snapshot,
        }

    covered_call, cash_secured_put = selectors or (select_covered_call, select_cash_secured_put)
    if resolved == "COVERED_CALL":
        selection = covered_call(
            normalized, shares_held=shares_held, spot=resolved_spot, policy=policy
        )
    else:
        selection = cash_secured_put(
            normalized, cash_available=cash_collateral, spot=resolved_spot, policy=policy
        )

    return {
        "symbol": normalized,
        "strategy": resolved,
        "status": selection.get("status"),
        "selection": selection,
        "rationale": selection.get("rationale"),
        "account_context": {
            "account_type": account.get("account_type"),
            "shares_held": shares_held,
            "cash_collateral": cash_collateral,
            "uses_margin": account.get("uses_margin"),
        },
        "quote": {
            "spot": resolved_spot,
            "source": quote_snapshot.get("source"),
            "as_of": quote_snapshot.get("latest_date"),
        },
        "next_step": {
            "approved": False,
            "gates_not_yet_run": GATES_NOT_YET_RUN,
            "note": NOT_APPROVED_NOTE,
            "preview_request": preview_request_for(normalized, selection),
        },
    }
