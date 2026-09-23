"""Verify build_shariah_candidate translates broker-agnostic order inputs
into the exact candidate shape approve_candidate expects, without the caller
needing to know anything about shariah_gate/option_structure_gate/
account_shariah_gate internals."""

from approval_workflow import approve_candidate
from shariah_candidate import build_shariah_candidate
from option_permissibility import OPTION_DETERMINATION, OPTION_POLICY_PERMITTED

# The shipped determination refuses every option contract (option_permissibility.py).
# These assertions are about the structure/collateral rules, which must stay exercised,
# so they supply a permissive determination through the test-only seam.
# test_option_permissibility.py covers the shipped default.
PERMITTED = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)


SHARIAH_PASS = {"agent": "shariah", "status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"}
SHARIAH_REJECT = {"agent": "shariah", "status": "REJECT", "provider": "ZOYA", "reason": "NON_COMPLIANT"}


def main() -> None:
    # Plain equity candidate: no option_contract in, no option_structure out.
    equity = build_shariah_candidate(
        symbol="AAPL",
        side="BUY",
        signal="BUY",
        quantity=1,
        price=200.0,
        account_type="CASH",
        shariah_override=SHARIAH_PASS,
    )
    assert equity["compliance"]["status"] == "COMPLIANT"
    assert equity["account_type"] == "CASH"
    assert "option_structure" not in equity

    # Alpaca's COVERED_CALL strategy maps to option_structure_gate's
    # "covered_call", carrying through the shares/collateral/contracts the
    # caller already resolved from portfolio_store / account state.
    covered_call = build_shariah_candidate(
        symbol="AAPL",
        side="SELL",
        signal="SELL",
        quantity=1,
        price=3.50,
        account_type="CASH",
        option_contract={"strategy": "COVERED_CALL", "option_type": "CALL", "strike": 210.0},
        shares_held=100,
        shariah_override=SHARIAH_PASS,
    )
    assert covered_call["option_structure"]["structure"] == "covered_call"
    assert covered_call["option_structure"]["shares_held"] == 100
    assert covered_call["option_structure"]["contracts"] == 1

    cash_secured_put = build_shariah_candidate(
        symbol="AAPL",
        side="SELL",
        signal="SELL",
        quantity=1,
        price=2.10,
        account_type="CASH",
        option_contract={"strategy": "CASH_SECURED_PUT", "option_type": "PUT", "strike": 190.0},
        cash_collateral=19000.0,
        shariah_override=SHARIAH_PASS,
    )
    assert cash_secured_put["option_structure"]["structure"] == "cash_secured_put"
    assert cash_secured_put["option_structure"]["strike"] == 190.0
    assert cash_secured_put["option_structure"]["cash_collateral"] == 19000.0

    # A non-compliant underlying is surfaced honestly regardless of structure.
    non_compliant = build_shariah_candidate(
        symbol="XYZ",
        side="BUY",
        signal="BUY",
        quantity=1,
        price=10.0,
        account_type="CASH",
        shariah_override=SHARIAH_REJECT,
    )
    assert non_compliant["compliance"]["status"] == "NON_COMPLIANT"

    # An Alpaca strategy this system doesn't recognize fails closed via
    # option_structure_gate's own unknown_structure rule -- the composer's
    # job is just an honest translation, not re-implementing that check.
    unrecognized = build_shariah_candidate(
        symbol="AAPL",
        side="SELL",
        signal="SELL",
        quantity=1,
        price=1.0,
        account_type="CASH",
        option_contract={"strategy": "IRON_CONDOR", "option_type": "CALL", "strike": 200.0},
        shariah_override=SHARIAH_PASS,
    )
    assert unrecognized["option_structure"]["structure"] == "unknown_structure"

    # End-to-end: the composer's output actually flows through the real
    # approval gate correctly, not just each piece in isolation.
    # The shipped determination refuses every option, even a fully covered one.
    assert approve_candidate(covered_call, approved_by_user=True)["status"] == "REJECT"

    # Under a permissive determination the composed candidate flows through the real
    # approval gate, which is what keeps this path exercised rather than dormant.
    covered_call["option_structure"]["determination"] = PERMITTED
    ready = approve_candidate(covered_call, approved_by_user=True)
    assert ready["status"] == "APPROVED_PAPER_READY"

    # Same covered call, but the caller resolved 0 owned shares (e.g. the
    # portfolio position was already closed) -- must block end-to-end.
    uncovered = build_shariah_candidate(
        symbol="AAPL",
        side="SELL",
        signal="SELL",
        quantity=1,
        price=3.50,
        account_type="CASH",
        option_contract={"strategy": "COVERED_CALL", "option_type": "CALL", "strike": 210.0},
        shares_held=0,
        shariah_override=SHARIAH_PASS,
    )
    blocked = approve_candidate(uncovered, approved_by_user=True)
    assert blocked["status"] == "REJECT"
    assert blocked["reason"] == "option_structure_rejected"

    # Same covered call, fully backed, but on a margin account -- the account
    # gate must block it even though the structure itself is fine.
    margin = build_shariah_candidate(
        symbol="AAPL",
        side="SELL",
        signal="SELL",
        quantity=1,
        price=3.50,
        account_type="MARGIN",
        option_contract={"strategy": "COVERED_CALL", "option_type": "CALL", "strike": 210.0},
        shares_held=100,
        shariah_override=SHARIAH_PASS,
    )
    margin_blocked = approve_candidate(margin, approved_by_user=True)
    assert margin_blocked["status"] == "REJECT"
    assert margin_blocked["reason"] == "margin_account_not_permitted"

    print("PASS: build_shariah_candidate translates Alpaca inputs into the approval-gate contract.")


if __name__ == "__main__":
    main()
