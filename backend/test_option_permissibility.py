"""Option contracts are refused at every entry point, and the structure code stays alive.

Two layers, deliberately:

1. **Production default** -- with the determination as it actually ships, every path that
   could reach an option order refuses it with `option_contracts_not_permitted`, and the
   determination travels with the refusal so the reason is inspectable rather than a bare
   code.

2. **Structure arithmetic under a permissive determination** -- the covered-call share
   count, the cash-secured-put collateral, the margin refusal and the naked-structure
   refusal are all still exercised, by passing a PERMITTED determination through the
   test-only seam. Without this layer the option code would rot exactly the way a
   commented-out block rots, and a scholarly ruling either way would mean resurrecting
   dead code instead of changing one constant.

Layer 2 is not a way of saying the structures are permissible. It keeps the arithmetic
checked so that whatever the ruling turns out to be, the system can act on it.
"""

import option_permissibility
from option_permissibility import (
    OPTION_DETERMINATION,
    OPTION_POLICY_PERMITTED,
    OPTION_POLICY_PROHIBITED,
    REASON_NOT_PERMITTED,
    check_option_permissibility,
    determination_summary,
)
from option_structure_gate import check_structure

PERMITTED = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)


def check_the_shipped_determination_prohibits_options() -> None:
    """The default must be PROHIBITED. If this fails, someone flipped it -- read why."""
    assert OPTION_DETERMINATION["status"] == OPTION_POLICY_PROHIBITED
    assert OPTION_DETERMINATION["review_status"] == "PENDING_SCHOLARLY_REVIEW"

    result = check_option_permissibility()
    assert result["status"] == "REJECT"
    assert result["reason"] == REASON_NOT_PERMITTED


def check_the_determination_carries_its_authority_and_its_open_question() -> None:
    """A refusal nobody can trace to an authority is an opinion, not an applied ruling."""
    determination = check_option_permissibility()["determination"]
    assert determination["authority"]
    assert determination["source_url"].startswith("https://")
    assert determination["recorded_on"] == "2026-09-23"
    assert set(determination["grounds"]) >= {"gharar", "maysir"}
    # The scope note is the honest part: the cited sources did not address a covered
    # call on owned shares, and the system must not pretend they did.
    assert "covered call" in determination["scope_note"]
    assert determination["pending_question"]


def check_it_fails_closed_on_anything_that_is_not_explicitly_permitted() -> None:
    for broken in (None, {}, {"status": ""}, {"status": "MAYBE"}, {"status": "prohibited"}):
        if broken is None:
            continue  # None means "use the shipped determination", covered above
        assert check_option_permissibility(broken)["status"] == "REJECT", broken
    # A non-dict is malformed configuration, not permission.
    assert check_option_permissibility("PERMITTED")["status"] == "REJECT"


def check_every_structure_is_refused_under_the_shipped_determination() -> None:
    """Even a fully collateralised Level 1 structure is refused, and for the right reason."""
    covered_call = check_structure(structure="covered_call", shares_held=1000, contracts=1)
    assert covered_call["status"] == "REJECT"
    assert covered_call["reason"] == REASON_NOT_PERMITTED, (
        "a fully covered call must be refused as impermissible, not as under-collateralised"
    )
    assert covered_call["determination"]["status"] == OPTION_POLICY_PROHIBITED

    cash_secured_put = check_structure(
        structure="cash_secured_put", cash_collateral=1_000_000.0, strike=100.0, contracts=1
    )
    assert cash_secured_put["status"] == "REJECT"
    assert cash_secured_put["reason"] == REASON_NOT_PERMITTED


def check_the_refusal_precedes_the_collateral_test() -> None:
    """Order matters: an impermissible contract must not be reported as fixable.

    If the collateral test ran first, an under-collateralised covered call would come
    back `insufficient_underlying_shares` -- telling the owner to buy more shares for an
    order that no amount of shares can make permissible.
    """
    result = check_structure(structure="covered_call", shares_held=0, contracts=1)
    assert result["reason"] == REASON_NOT_PERMITTED
    assert result["reason"] != "insufficient_underlying_shares"


def check_the_summary_says_what_is_open() -> None:
    summary = determination_summary()
    assert "not permitted" in summary
    assert "open question" in summary
    # CLAUDE.md: never coin "Shariah-aware", never call the system Shariah-compliant.
    assert "shariah-aware" not in summary.lower()
    assert "shariah-compliant system" not in summary.lower()


# --- Layer 2: the structure arithmetic, kept alive under a permissive determination ---


def check_covered_call_arithmetic_still_works() -> None:
    passing = check_structure(
        structure="covered_call", shares_held=100, contracts=1, determination=PERMITTED
    )
    assert passing["status"] == "PASS"
    assert passing["reason"] == "covered_by_owned_shares"

    short = check_structure(
        structure="covered_call", shares_held=99, contracts=1, determination=PERMITTED
    )
    assert short["status"] == "REJECT"
    assert short["reason"] == "insufficient_underlying_shares"


def check_cash_secured_put_arithmetic_still_works() -> None:
    passing = check_structure(
        structure="cash_secured_put",
        cash_collateral=10_000.0,
        strike=100.0,
        contracts=1,
        determination=PERMITTED,
    )
    assert passing["status"] == "PASS"
    assert passing["reason"] == "cash_secured"

    short = check_structure(
        structure="cash_secured_put",
        cash_collateral=9_999.0,
        strike=100.0,
        contracts=1,
        determination=PERMITTED,
    )
    assert short["status"] == "REJECT"
    assert short["reason"] == "insufficient_cash_collateral"


def check_margin_and_naked_structures_are_still_refused_when_permitted() -> None:
    """These refusals are independent of the permissibility question and must survive."""
    margin = check_structure(
        structure="covered_call", shares_held=1000, uses_margin=True, determination=PERMITTED
    )
    assert margin["reason"] == "margin_financing_not_permitted"

    for naked in ("naked_call", "naked_put", "straddle", "strangle"):
        result = check_structure(structure=naked, shares_held=10_000, determination=PERMITTED)
        assert result["status"] == "REJECT", naked
        assert result["reason"] == "structure_not_permitted", naked

    unknown = check_structure(structure="iron_condor", determination=PERMITTED)
    assert unknown["reason"] == "unknown_structure"


def check_the_seam_is_not_used_in_production() -> None:
    """`determination` is a test seam. Nothing shipped may pass it."""
    import ast
    from pathlib import Path

    backend = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(backend.rglob("*.py")):
        if "__pycache__" in path.parts or path.name.startswith("test_"):
            continue
        # These thread the seam as a pass-through parameter; none of them originates a
        # value. That is why the stronger check below exists: an exemption list can only
        # grow, but "no shipped module may name PERMITTED" cannot be weakened quietly.
        if path.name in {
            "option_permissibility.py",
            "option_structure_gate.py",
            "option_strategy_api.py",
            "option_structure_agent.py",
            "agent_coordinator.py",
        }:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for keyword in node.keywords:
                    if keyword.arg == "determination":
                        offenders.append(path.relative_to(backend).as_posix())
    assert not offenders, (
        f"these modules pass the test-only determination seam: {sorted(set(offenders))}"
    )


def check_no_shipped_module_can_mint_a_permissive_determination() -> None:
    """The real risk is not threading the seam -- it is writing PERMITTED somewhere.

    Any non-test module that names OPTION_POLICY_PERMITTED, or spells the literal
    "PERMITTED" into a determination, could hand the gate a permission nobody granted.
    Only option_permissibility.py may mention it.
    """
    from pathlib import Path

    backend = Path(__file__).resolve().parent
    offenders = []
    for path in sorted(backend.rglob("*.py")):
        if "__pycache__" in path.parts or path.name.startswith("test_"):
            continue
        if path.name == "option_permissibility.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "OPTION_POLICY_PERMITTED" in source:
            offenders.append(path.relative_to(backend).as_posix())
    assert not offenders, (
        "only option_permissibility.py may name the permissive policy; "
        f"these do: {sorted(set(offenders))}"
    )


def main() -> None:
    check_the_shipped_determination_prohibits_options()
    check_the_determination_carries_its_authority_and_its_open_question()
    check_it_fails_closed_on_anything_that_is_not_explicitly_permitted()
    check_every_structure_is_refused_under_the_shipped_determination()
    check_the_refusal_precedes_the_collateral_test()
    check_the_summary_says_what_is_open()
    check_covered_call_arithmetic_still_works()
    check_cash_secured_put_arithmetic_still_works()
    check_margin_and_naked_structures_are_still_refused_when_permitted()
    check_the_seam_is_not_used_in_production()
    check_no_shipped_module_can_mint_a_permissive_determination()
    assert option_permissibility.OPTION_DETERMINATION["status"] == OPTION_POLICY_PROHIBITED
    print("PASS: option contracts are refused at every entry point; structure arithmetic intact.")


if __name__ == "__main__":
    main()
