"""Verify the human-readable Shariah audit trace matches the sourced rationale
in hackathon/alpaca-2026/SHARIAH_GATE_NOTES.md, for the underlying + structure
gate decisions together."""

from option_permissibility import OPTION_DETERMINATION, OPTION_POLICY_PERMITTED
from shariah_trace import build_shariah_trace, describe_approval

PERMITTED_DETERMINATION = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)


def main() -> None:
    # Underlying-only trace (no option structure involved).
    equity_trace = build_shariah_trace(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
    )
    assert equity_trace.startswith("AAPL: underlying=PASS (ZOYA)")
    assert "structure=" not in equity_trace

    # Covered call approved: both gates pass, rationale cites ownership.
    covered_call_trace = build_shariah_trace(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
        option_structure={
            "status": "PASS",
            "reason": "covered_by_owned_shares",
            "details": {"structure": "covered_call"},
        },
    )
    assert "AAPL" in covered_call_trace
    assert "underlying=PASS (ZOYA)" in covered_call_trace
    assert "structure=covered_call -> PASS" in covered_call_trace
    assert "ownership precedes the sale of a right" in covered_call_trace

    # Naked call rejected: rationale cites gharar/maysir, not a generic reason.
    naked_call_trace = build_shariah_trace(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
        option_structure={
            "status": "REJECT",
            "reason": "structure_not_permitted",
            "details": {"structure": "naked_call"},
        },
    )
    assert "structure=naked_call -> REJECT" in naked_call_trace
    assert "gharar" in naked_call_trace.lower() or "maysir" in naked_call_trace.lower()

    # Unrecognized reasons still produce a trace line rather than raising.
    fallback_trace = build_shariah_trace(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
        option_structure={
            "status": "REJECT",
            "reason": "some_future_reason_not_yet_mapped",
            "details": {"structure": "mystery"},
        },
    )
    assert "structure=mystery -> REJECT (some_future_reason_not_yet_mapped)" in fallback_trace

    # Account-level Riba exposure is part of the trace too, independent of
    # the structure gate -- a margin account shows up even when the structure
    # itself would have been fine.
    margin_trace = build_shariah_trace(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
        option_structure={
            "status": "PASS",
            "reason": "covered_by_owned_shares",
            "details": {"structure": "covered_call"},
        },
        account_shariah={
            "status": "REJECT",
            "reason": "margin_account_not_permitted",
            "details": {"account_type": "MARGIN"},
        },
    )
    assert "account=MARGIN -> REJECT" in margin_trace
    assert "riba" in margin_trace.lower()

    # describe_approval derives both gate verdicts itself from the same
    # candidate shape shariah_candidate.build_shariah_candidate produces --
    # callers (local_api.py) shouldn't need to know the gate internals.
    approval_trace = describe_approval(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
        candidate={
            "account_type": "CASH",
            "option_structure": {
                "structure": "covered_call",
                "shares_held": 100,
                "contracts": 1,
                # Test-only seam: the shipped determination refuses every option, and
                # this assertion is about the *trace rendering* of a structure verdict,
                # not about permissibility. See option_permissibility.py.
                "determination": PERMITTED_DETERMINATION,
            },
        },
    )
    assert "structure=covered_call -> PASS" in approval_trace
    assert "account=CASH -> PASS" in approval_trace

    # Equity-only candidate (no option_structure key at all) still traces.
    equity_approval_trace = describe_approval(
        symbol="AAPL",
        shariah={"status": "PASS", "provider": "ZOYA", "reason": "COMPLIANT"},
        candidate={"account_type": "CASH"},
    )
    assert "structure=" not in equity_approval_trace
    assert "account=CASH -> PASS" in equity_approval_trace

    print("PASS: Shariah trace produces citation-backed, human-readable audit lines.")


if __name__ == "__main__":
    main()
