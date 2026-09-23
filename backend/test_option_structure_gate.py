"""Verify the Shariah options-structure gate honors the allow-list design.

See hackathon/alpaca-2026/SHARIAH_GATE_NOTES.md for the sourced rationale behind
each structure's verdict.
"""

from option_structure_gate import check_structure as _check_structure

from functools import partial

from option_permissibility import (
    OPTION_DETERMINATION,
    OPTION_POLICY_PERMITTED,
    REASON_NOT_PERMITTED,
)

# The determination in force refuses every option contract, so the structure rules below
# would all short-circuit. They are the *narrower* question -- is this structure allowed,
# is it collateralised -- and they must stay exercised so a scholarly ruling either way is
# one constant away rather than a rebuild. Hence a permissive determination here.
# test_option_permissibility.py covers what the system actually ships.
PERMITTED = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)

check_structure = partial(_check_structure, determination=PERMITTED)


def main() -> None:
    # First, the shipped default: a fully covered call is refused as impermissible.
    shipped = _check_structure(structure="covered_call", shares_held=100, contracts=1)
    assert shipped["status"] == "REJECT"
    assert shipped["reason"] == REASON_NOT_PERMITTED

    # Everything below runs under PERMITTED; see the note at the top of the file.
    # Covered call: allowed only when the agent already holds enough shares.
    covered = check_structure(structure="covered_call", shares_held=100, contracts=1)
    assert covered["status"] == "PASS"
    assert covered["reason"] == "covered_by_owned_shares"

    naked_covered = check_structure(structure="covered_call", shares_held=50, contracts=1)
    assert naked_covered["status"] == "REJECT"
    assert naked_covered["reason"] == "insufficient_underlying_shares"

    # Cash-secured put: allowed only with full cash collateral for the strike.
    secured_put = check_structure(
        structure="cash_secured_put", cash_collateral=10000.0, strike=95.0, contracts=1
    )
    assert secured_put["status"] == "PASS"
    assert secured_put["reason"] == "cash_secured"

    underfunded_put = check_structure(
        structure="cash_secured_put", cash_collateral=5000.0, strike=95.0, contracts=1
    )
    assert underfunded_put["status"] == "REJECT"
    assert underfunded_put["reason"] == "insufficient_cash_collateral"

    put_without_strike = check_structure(
        structure="cash_secured_put", cash_collateral=10000.0, contracts=1
    )
    assert put_without_strike["status"] == "REJECT"
    assert put_without_strike["reason"] == "strike_required"

    # Protective put: allowed only when hedging an already-owned position.
    protective = check_structure(structure="protective_put", shares_held=100, contracts=1)
    assert protective["status"] == "PASS"
    assert protective["reason"] == "hedges_owned_shares"

    unhedged_protective = check_structure(structure="protective_put", shares_held=0, contracts=1)
    assert unhedged_protective["status"] == "REJECT"
    assert unhedged_protective["reason"] == "no_underlying_position_to_protect"

    # Collar: same ownership requirement as protective put (put + call on owned shares).
    collar = check_structure(structure="collar", shares_held=100, contracts=1)
    assert collar["status"] == "PASS"
    assert collar["reason"] == "collar_on_owned_shares"

    unhedged_collar = check_structure(structure="collar", shares_held=0, contracts=1)
    assert unhedged_collar["status"] == "REJECT"
    assert unhedged_collar["reason"] == "no_underlying_position_to_protect"

    # Multiple contracts scale the share/collateral requirement.
    two_contract_call = check_structure(structure="covered_call", shares_held=100, contracts=2)
    assert two_contract_call["status"] == "REJECT"
    assert two_contract_call["reason"] == "insufficient_underlying_shares"

    # Naked/speculative structures are rejected outright, regardless of backing.
    for structure in ("naked_call", "naked_put", "straddle", "strangle"):
        result = check_structure(structure=structure, shares_held=1000, cash_collateral=1_000_000.0, strike=1.0)
        assert result["status"] == "REJECT", f"{structure} should be rejected"
        assert result["reason"] == "structure_not_permitted"

    # Margin financing is rejected regardless of structure or backing (Riba concern).
    margin_covered_call = check_structure(
        structure="covered_call", shares_held=100, contracts=1, uses_margin=True
    )
    assert margin_covered_call["status"] == "REJECT"
    assert margin_covered_call["reason"] == "margin_financing_not_permitted"

    # Unknown structures fail closed rather than defaulting to allow.
    unknown = check_structure(structure="iron_condor")
    assert unknown["status"] == "REJECT"
    assert unknown["reason"] == "unknown_structure"

    # Structure name normalization is case/whitespace insensitive.
    normalized = check_structure(structure="  Covered_Call ", shares_held=100, contracts=1)
    assert normalized["status"] == "PASS"
    assert normalized["structure"] == "covered_call"

    print("PASS: option structure gate enforces the Shariah allow-list.")


if __name__ == "__main__":
    main()
