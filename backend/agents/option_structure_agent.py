"""Deterministic option-structure agent for paper-order eligibility.

This agent is deterministic and fail-closed. It does not use an LLM.
"""

from option_structure_gate import check_structure


def evaluate_option_structure(
    *,
    structure: str,
    shares_held: int = 0,
    cash_collateral: float = 0.0,
    strike: float | None = None,
    contracts: int = 1,
    uses_margin: bool = False,
    determination: dict | None = None,
) -> dict:
    # `determination` is the test-only seam described in option_permissibility.py. It is
    # threaded rather than swallowed so a test can exercise the structure arithmetic
    # through the real agent, not around it. Nothing in production passes it.
    result = check_structure(
        structure=structure,
        shares_held=shares_held,
        cash_collateral=cash_collateral,
        strike=strike,
        contracts=contracts,
        uses_margin=uses_margin,
        determination=determination,
    )
    return {
        "agent": "option_structure",
        "status": result.get("status"),
        "reason": result.get("reason"),
        "details": result,
    }
