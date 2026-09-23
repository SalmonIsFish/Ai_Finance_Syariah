"""Verify approval states without contacting Moomoo."""

from approval_workflow import approve_candidate
from option_permissibility import (
    OPTION_DETERMINATION,
    OPTION_POLICY_PERMITTED,
    REASON_NOT_PERMITTED,
)

# Test-only: lets the structure arithmetic stay exercised while the shipped
# determination refuses every option. See option_permissibility.py.
PERMITTED_DETERMINATION = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)


candidate = {
    "signal": "BUY",
    "compliance": {"status": "COMPLIANT", "source": "LOCAL_TEST_FIXTURE"},
    "symbol": "TEST_ONLY",
    "quantity": 1,
    "price": 100.0,
}

print("Pending:", approve_candidate(candidate, approved_by_user=False))
print("Approved:", approve_candidate(candidate, approved_by_user=True))

# Under the determination in force (option_permissibility.py), a fully covered call is
# refused at approval -- and refused as impermissible, not as under-collateralised. The
# user approving it makes no difference; that is the point of a gate.
covered_call_candidate = {
    **candidate,
    "option_structure": {"structure": "covered_call", "shares_held": 100, "contracts": 1},
}
covered_call_result = approve_candidate(covered_call_candidate, approved_by_user=True)
assert covered_call_result["status"] == "REJECT"
assert covered_call_result["option_structure"]["reason"] == REASON_NOT_PERMITTED

# With a permissive determination supplied through the test-only seam, the same
# candidate approves -- proving the refusal above comes from the permissibility gate and
# that the structure path still works end to end. See test_option_permissibility.py.
permitted_covered_call = {
    **candidate,
    "option_structure": {
        "structure": "covered_call",
        "shares_held": 100,
        "contracts": 1,
        "determination": PERMITTED_DETERMINATION,
    },
}
permitted_result = approve_candidate(permitted_covered_call, approved_by_user=True)
assert permitted_result["status"] == "APPROVED_PAPER_READY", permitted_result

# A non-compliant option structure is rejected for its own reason even when options are
# permitted -- the structure rules are independent of the permissibility question.
naked_call_candidate = {
    **candidate,
    "option_structure": {"structure": "naked_call", "determination": PERMITTED_DETERMINATION},
}
naked_call_result = approve_candidate(naked_call_candidate, approved_by_user=True)
assert naked_call_result["status"] == "REJECT"
assert naked_call_result["reason"] == "option_structure_rejected"
assert naked_call_result["option_structure"]["status"] == "REJECT"
assert naked_call_result["option_structure"]["reason"] == "structure_not_permitted"

# No option_structure key at all is the existing equity-only behavior.
assert "option_structure" not in candidate

# A margin-enabled account is rejected even for a plain compliant equity buy --
# this is the gap flagged in the Alpaca adapter: account_type was computed but
# never actually gated on. Closing it here, not in the adapter, keeps every
# broker adapter subject to the same Shariah account check.
margin_account_candidate = {**candidate, "account_type": "MARGIN"}
margin_account_result = approve_candidate(margin_account_candidate, approved_by_user=True)
assert margin_account_result["status"] == "REJECT"
assert margin_account_result["reason"] == "margin_account_not_permitted"

cash_account_candidate = {**candidate, "account_type": "CASH"}
cash_account_result = approve_candidate(cash_account_candidate, approved_by_user=True)
assert cash_account_result["status"] == "APPROVED_PAPER_READY"

# No account_type key at all is the existing behavior (unaffected).
assert "account_type" not in candidate

print("PASS: approval workflow enforces the option-structure gate additively.")
print("PASS: approval workflow rejects margin-enabled accounts.")
print("TEST ONLY: no Moomoo connection and no order submission.")
