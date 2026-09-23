"""Is an option contract permissible at all, and on whose authority?

This is the *prior* question. ``option_structure_gate.check_structure`` asks a narrower
one -- is this a permitted Level 1 structure, and is it collateralised -- and until
2026-09-23 nothing in this system asked whether an option contract may be entered into
in the first place. That gap was structural, not cosmetic: the whole architecture rests
on applying an authority's determination and proving the application, and here there was
no determination to apply.

WHAT THIS MODULE DOES AND DOES NOT CLAIM
----------------------------------------
It does **not** decide anything. It records a determination made elsewhere, by named
authorities, and makes the system enforce it. Per CLAUDE.md, this system is not
Shariah-compliant and makes no such claim; it applies a determination and proves the
application.

THE DETERMINATION IN FORCE
--------------------------
Options are treated as **not permitted**, on the grounds reported for the OIC Islamic
Fiqh Academy and Mufti Taqi Usmani: gharar (whether the option will be exercised is
unknown when the contract is made), maysir (each party is betting against the other),
and the premium -- a promise not being a valid subject of sale.

**The cited sources address option contracts generally.** They do not separately treat a
covered call written against shares already owned, or a cash-secured put fully backed by
settled cash, which is the only thing this system ever did. That silence is not
permission: the objections attach to the contract rather than to the side one takes, and
the writer is the party *receiving* the contested premium. The question is open with the
owner's Shariah lecturer, so the system fails closed -- its rule for anything unknown.

WHY A CONSTANT AND NOT AN ENV VAR
---------------------------------
Changing this is meant to be a deliberate, reviewed code change that shows up in git
history, made because an authority ruled -- not a runtime flag someone flips because an
order was inconvenient. There is no ``os.getenv`` in this module on purpose.
"""

OPTION_POLICY_PROHIBITED = "PROHIBITED"
OPTION_POLICY_PERMITTED = "PERMITTED"

REASON_NOT_PERMITTED = "option_contracts_not_permitted"

OPTION_DETERMINATION: dict = {
    "status": OPTION_POLICY_PROHIBITED,
    "authority": "OIC Islamic Fiqh Academy; Mufti Taqi Usmani (as reported by Islamic Finance Guru)",
    "source_url": "https://www.islamicfinanceguru.com/articles/options-trading-halal-or-haram",
    "recorded_on": "2026-09-23",
    "grounds": (
        "gharar",
        "maysir",
        "premium_is_not_a_valid_subject_of_sale",
    ),
    "scope_note": (
        "The cited sources address option contracts generally and do not separately treat "
        "a covered call written against owned shares or a cash-secured put backed by "
        "settled cash. That question is open with the owner's Shariah lecturer; until it "
        "is answered this system treats it as unresolved and fails closed."
    ),
    "pending_question": (
        "Does writing a covered call against shares already owned, or a cash-secured put "
        "fully backed by settled cash, fall under the same ruling as speculative option "
        "trading?"
    ),
    "review_status": "PENDING_SCHOLARLY_REVIEW",
}


def check_option_permissibility(determination: dict | None = None) -> dict:
    """PASS only if the determination in force explicitly permits option contracts.

    Fails closed: anything that is not exactly ``PERMITTED`` -- including a missing or
    malformed determination -- is a REJECT.

    ``determination`` exists so tests can exercise the structure arithmetic in
    ``option_structure_gate`` under a permissive determination, keeping that code alive
    and checked rather than letting it rot behind a block. Nothing in production passes
    it; see backend/test_option_permissibility.py.
    """
    in_force = OPTION_DETERMINATION if determination is None else determination
    if not isinstance(in_force, dict):
        return {
            "status": "REJECT",
            "reason": REASON_NOT_PERMITTED,
            "determination": {"status": "MALFORMED", "review_status": "PENDING_SCHOLARLY_REVIEW"},
        }

    if in_force.get("status") == OPTION_POLICY_PERMITTED:
        return {"status": "PASS", "reason": "option_contracts_permitted", "determination": in_force}

    return {"status": "REJECT", "reason": REASON_NOT_PERMITTED, "determination": in_force}


def determination_summary() -> str:
    """One human sentence for a UI or a bot to show. Deterministic; no model involved."""
    if OPTION_DETERMINATION.get("status") == OPTION_POLICY_PERMITTED:
        return (
            "Option contracts are permitted under the determination recorded on "
            f"{OPTION_DETERMINATION.get('recorded_on')} "
            f"({OPTION_DETERMINATION.get('authority')})."
        )
    return (
        "Option contracts are not permitted under the determination recorded on "
        f"{OPTION_DETERMINATION.get('recorded_on')} ({OPTION_DETERMINATION.get('authority')}). "
        "Whether a covered call on owned shares or a fully cash-secured put falls under "
        "the same ruling is an open question awaiting scholarly review."
    )
