"""Verify the option-structure agent normalizes the gate result correctly."""

from agents.option_structure_agent import evaluate_option_structure as _evaluate_option_structure

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

evaluate_option_structure = partial(_evaluate_option_structure, determination=PERMITTED)


def main() -> None:
    # The shipped default refuses, and the agent normalizes that result too.
    shipped = _evaluate_option_structure(structure="covered_call", shares_held=100, contracts=1)
    assert shipped["status"] == "REJECT"
    assert shipped["reason"] == REASON_NOT_PERMITTED

    passed = evaluate_option_structure(structure="covered_call", shares_held=100, contracts=1)
    assert passed["agent"] == "option_structure"
    assert passed["status"] == "PASS"
    assert passed["reason"] == "covered_by_owned_shares"
    assert passed["details"]["structure"] == "covered_call"

    rejected = evaluate_option_structure(structure="naked_call")
    assert rejected["agent"] == "option_structure"
    assert rejected["status"] == "REJECT"
    assert rejected["reason"] == "structure_not_permitted"

    print("PASS: option structure agent normalizes gate results.")


if __name__ == "__main__":
    main()
