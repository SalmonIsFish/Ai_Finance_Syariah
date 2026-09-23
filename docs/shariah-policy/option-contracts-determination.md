# Option contracts — determination in force

**Status: NOT PERMITTED. Recorded 2026-09-23. Pending scholarly review.**

This note records a determination the system *applies*. It is not a ruling by this
project, and nothing here certifies the system. See [[Maysir]] and [[Gharar]] for the
underlying concepts, and `screening-criteria-breakdown.md` for how securities (as opposed
to transaction structures) are classified.

## What was determined, and on what grounds

Option contracts are treated as not permitted, on the grounds reported for the **OIC
Islamic Fiqh Academy** and **Mufti Taqi Usmani**:

| Ground | The objection |
|---|---|
| **Gharar** | Whether the option will be exercised is unknown when the contract is made. |
| **Maysir** | Each party is betting against the other. |
| **The premium** | A promise is not a valid subject of sale; a fee charged for a right is therefore not a valid price. An interest-like element is also raised, the premium being added to the price of the asset. |

Source consulted: <https://www.islamicfinanceguru.com/articles/options-trading-halal-or-haram>
(Islamic Finance Guru, citing the above and the IFG Fatwa Forum — Muftis Billal Omarjee
and Faraz Adam). That page disclaims its own authority: *"This is a financial journalism
platform… not personal advice."*

The article names **arbun** (a down payment the buyer may forfeit) and **wa'd** (a
unilateral promise) as structures discussed in this area, both noted as non-tradeable.

## The open question

**The cited sources address option contracts generally.** They do not separately treat:

- a **covered call** written against shares already owned, or
- a **cash-secured put** fully backed by settled cash

— which is the only thing this system ever did. It never bought options, and never wrote
a naked or speculative leg; `option_structure_gate` rejected those outright.

**That silence is not permission.** The objections above attach to the contract itself
rather than to the side one takes, and the writer is the party *receiving* the contested
premium. Reading the gap as permission would be motivated reasoning.

**The question put to the reviewer:**

> Does writing a covered call against shares already owned, or a cash-secured put fully
> backed by settled cash, fall under the same ruling as speculative option trading?

Until that is answered, the system fails closed — its rule for anything unknown.

## What the code does

`backend/option_permissibility.py` holds the determination as a **module constant**, not
an environment variable. Changing it is a deliberate code change, reviewed and visible in
git history, made because an authority ruled — not a runtime flag flipped because an order
was inconvenient. `backend/test_option_permissibility.py` asserts that no shipped module
may even name the permissive policy.

It is enforced at three points:

| Point | Effect |
|---|---|
| `option_structure_gate.check_structure` | `/paper/approval` refuses, and `/paper/execute` becomes unreachable |
| `agent_coordinator.evaluate_candidate` | `/paper/preview` refuses — it builds no `option_structure`, so without this an option previewed as `READY_FOR_APPROVAL` and was refused only one step later |
| `option_strategy_api.propose_option_strategy` | `GET /stock/{symbol}/option-strategy` returns the determination instead of a contract, before any option-chain request |

The refusal code is `option_contracts_not_permitted`, deliberately distinct from
`option_structure_rejected`. The second can be fixed by changing the order; the first
cannot be fixed at all. Every refusal carries the determination — its authority, its
source, its grounds, its scope note and the open question — so the reason is inspectable
rather than a bare code.

**No option code was deleted.** The selection, structure and collateral logic all remain
live and exercised by tests under a permissive determination supplied through a test-only
seam. A ruling either way is one constant away rather than a resurrection of deleted code.

## What this does not claim

This does not make the system Shariah-compliant, and no such claim is made anywhere. Being
on the Securities Commission Malaysia's SAC list settles the *security*; it says nothing
about a trading strategy, an execution mechanism, or this system. Classification of a
security and permissibility of a transaction structure are separate questions, and this
note concerns only the second.
