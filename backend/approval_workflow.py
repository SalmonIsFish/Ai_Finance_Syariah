"""Approval-required paper workflow; broker submission remains opt-in."""

from agents.account_shariah_agent import evaluate_account
from agents.option_structure_agent import evaluate_option_structure
from config import load_settings
from option_permissibility import REASON_NOT_PERMITTED


def approve_candidate(candidate: dict, *, approved_by_user: bool) -> dict:
    settings = load_settings()
    if settings.moomoo_mode != "paper":
        return {"status": "REJECT", "reason": "paper_mode_required"}
    side = str(candidate.get("side") or "BUY").upper()
    if side not in {"BUY", "SELL"}:
        return {"status": "REJECT", "reason": "side_must_be_BUY_or_SELL"}
    if side == "BUY" and candidate.get("signal") != "BUY":
        return {"status": "REJECT", "reason": "no_buy_signal"}
    if side == "SELL" and candidate.get("signal") != "SELL":
        return {"status": "REJECT", "reason": "sell_signal_required"}
    if candidate.get("compliance", {}).get("status") != "COMPLIANT":
        return {"status": "REJECT", "reason": "compliance_not_confirmed"}
    account_type = candidate.get("account_type")
    if account_type is not None:
        account_result = evaluate_account(account_type=account_type)
        if account_result["status"] != "PASS":
            return {
                "status": "REJECT",
                "reason": account_result["reason"],
                "account_shariah": account_result,
            }
    option_structure = candidate.get("option_structure")
    if option_structure is not None:
        structure_result = evaluate_option_structure(**option_structure)
        if structure_result["status"] != "PASS":
            # Keep "no option contract is permitted" distinct from "this structure or its
            # collateral failed". The second can be fixed by sizing; the first cannot be
            # fixed at all, and reporting it as option_structure_rejected told the owner
            # (and the bridge's rendered reason) to look at collateral.
            reason = (
                REASON_NOT_PERMITTED
                if structure_result.get("reason") == REASON_NOT_PERMITTED
                else "option_structure_rejected"
            )
            return {"status": "REJECT", "reason": reason, "option_structure": structure_result}
    if not approved_by_user:
        return {"status": "PENDING_APPROVAL", "broker_submission": False}
    return {
        "status": "APPROVED_PAPER_READY",
        "broker_submission": False,
        "execution_environment": "SIMULATE",
        "candidate": candidate,
    }
