"""Fail-closed Shariah gate for options-structure eligibility.

Deterministic, non-AI, and separate from the underlying-symbol gate in
shariah_gate.py. A trade needs a PASS from both gates before it can reach
the approval queue. See hackathon/alpaca-2026/SHARIAH_GATE_NOTES.md for the
sourced rationale behind each verdict.

TWO QUESTIONS, IN ORDER
-----------------------
This module answers "is this a permitted structure, and is it collateralised?" It is
the narrower question, and it only matters once the prior one is settled: *may an
option contract be entered into at all, and on whose authority?* That prior question
lives in option_permissibility.py and is checked first, below.

As of 2026-09-23 the determination in force is that option contracts are **not
permitted**, so every call here rejects before reaching the structure arithmetic. The
arithmetic is deliberately left intact and exercised by tests under a permissive
determination, so that a scholarly ruling either way is one constant away rather than
a resurrection of deleted code.
"""

from option_permissibility import check_option_permissibility

SHARES_PER_CONTRACT = 100

# Asset-backed or cash-backed structures the policy currently permits, subject
# to the ownership/collateral condition enforced below.
ALLOWED_STRUCTURES = {"covered_call", "cash_secured_put", "protective_put", "collar"}

# Naked/speculative structures rejected outright regardless of backing.
REJECTED_STRUCTURES = {"naked_call", "naked_put", "straddle", "strangle"}


def check_structure(
    *,
    structure: str,
    shares_held: int = 0,
    cash_collateral: float = 0.0,
    strike: float | None = None,
    contracts: int = 1,
    uses_margin: bool = False,
    determination: dict | None = None,
) -> dict:
    normalized = structure.strip().lower()

    # The prior question, before any structure or collateral test: may an option
    # contract be entered into at all? `determination` is a test-only seam -- nothing
    # in production passes it. See option_permissibility.py.
    permissibility = check_option_permissibility(determination)
    if permissibility["status"] != "PASS":
        return {
            "status": "REJECT",
            "reason": permissibility["reason"],
            "structure": normalized,
            "determination": permissibility["determination"],
        }

    if uses_margin:
        return {
            "status": "REJECT",
            "reason": "margin_financing_not_permitted",
            "structure": normalized,
        }

    if normalized in REJECTED_STRUCTURES:
        return {"status": "REJECT", "reason": "structure_not_permitted", "structure": normalized}

    if normalized not in ALLOWED_STRUCTURES:
        return {"status": "REJECT", "reason": "unknown_structure", "structure": normalized}

    if normalized == "covered_call":
        if shares_held >= contracts * SHARES_PER_CONTRACT:
            return {"status": "PASS", "reason": "covered_by_owned_shares", "structure": normalized}
        return {
            "status": "REJECT",
            "reason": "insufficient_underlying_shares",
            "structure": normalized,
        }

    if normalized == "cash_secured_put":
        if strike is None or strike <= 0:
            return {"status": "REJECT", "reason": "strike_required", "structure": normalized}
        if cash_collateral >= contracts * SHARES_PER_CONTRACT * strike:
            return {"status": "PASS", "reason": "cash_secured", "structure": normalized}
        return {
            "status": "REJECT",
            "reason": "insufficient_cash_collateral",
            "structure": normalized,
        }

    # protective_put and collar share the same ownership requirement: both
    # hedge an already-owned position rather than opening a naked leg.
    if shares_held >= contracts * SHARES_PER_CONTRACT:
        reason = (
            "hedges_owned_shares" if normalized == "protective_put" else "collar_on_owned_shares"
        )
        return {"status": "PASS", "reason": reason, "structure": normalized}
    return {
        "status": "REJECT",
        "reason": "no_underlying_position_to_protect",
        "structure": normalized,
    }
