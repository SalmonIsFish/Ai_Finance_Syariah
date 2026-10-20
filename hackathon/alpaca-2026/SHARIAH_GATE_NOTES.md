# Shariah Options-Structure Gate — Design Sketch (not implemented)

> **Superseded (2026-10-05).** The allow-list below predates the determination that option
> contracts are not permitted, covered calls and cash-secured puts included
> (`backend/option_permissibility.py`). The dashboard still cites this file for the
> structure-level arithmetic, which stays exercised under a test-only seam. It is no longer
> a statement of what is permitted.

This is a first-draft sketch for the hackathon's central differentiator: a gate that decides
whether a specific *option structure* on a specific *underlying* is allowed, before any Alpaca
order is placed. It is not implementation-ready code — no backend files have been touched.

## How it extends the existing gate chain

`backend/shariah_gate.py` already implements a fail-closed pattern for stock underlyings:
`check_symbol(symbol) -> {"status": "PASS"|"REJECT", "reason": ...}`, backed by a compliance
dataset (and `zoya_compliance.py` for the Zoya integration). The options gate should follow the
exact same shape rather than inventing a new one, so it composes with the existing
`risk_checks.py` / `approval_workflow.py` / execution-audit chain described in `CLAUDE.md`:

```
Underlying gate (existing):  check_symbol(symbol) -> PASS/REJECT
Structure gate (new):        check_option_structure(structure, position_context) -> PASS/REJECT
Risk gate (existing):        risk_checks.py limits (position %, exposure %, orders/day, etc.)
Approval gate (existing):    human approval queue + execution audit
```

A trade should only reach the approval queue if **both** the underlying and the structure gate
return PASS. Fail-closed: unknown structure -> REJECT, not PASS-by-default (matches the existing
`shariah_gate.py` behavior of rejecting on missing config/data rather than defaulting to allow).

## Proposed structure allow-list

Grounded in the sourced fiqh notes at `E:\Projects Stuff\Multi_Ai_IslamicFinance\01-Shariah-Principles\`
(Riba.md, Gharar.md, Maysir.md, `Equity, stock ownership.md` — themselves sourced from Usmani's
*Introduction to Islamic Finance* and Hans Visser's *Islamic Finance: Principles and Practice*)
rather than LLM-generated citation claims. These are still secondary summaries, not primary AAOIFI
standard text — good enough to ground a hackathon demo's reasoning, not a substitute for a real
Shariah adviser sign-off if this ever went beyond paper trading.

| Structure | Verdict | Condition to PASS | Rationale + source |
|---|---|---|---|
| Covered call | Allow | Agent holds >= 100 shares of a Shariah-PASS underlying per contract | `Equity, stock ownership.md`: *"You must officially own the risk of the shares before you can sell them onward"* — ownership precedes the sale of a right, satisfying the possession requirement |
| Cash-secured put | Allow | 100% cash collateral held, no margin; underlying is Shariah-PASS | Framed as Arboun/Wa'd (a backed purchase commitment); 100% cash backing avoids `Riba.md`'s concern with leverage-financed positions |
| Protective put | Allow | Agent already holds the underlying shares being protected | Defensive use on an owned asset, not the `Maysir.md` pattern of "wealth transferred without productive output" |
| Collar (put + call on owned shares) | Allow | Both legs reference an existing owned position | Combination of the two allowed legs above |
| Naked/uncovered call or put | Reject | — | `Gharar.md` names "derivatives and speculation... where delivery or outcome is highly uncertain" as a prohibited-transaction example; no asset or full cash backing here |
| Straddle / strangle | Reject | — | `Maysir.md`: "pure speculation on options... involves excessive and artificial risk" — a pure volatility bet with no ownership or directional rationale |
| Any margin-financed leg | Reject | — | `Riba.md`: margin/interest-bearing financing is the core prohibited mechanism |

**This is a deliberate scope extension, not a continuation of prior policy — say so explicitly.**
The sister project's own `10-International-Paper\international-paper-scope.md` scoping document
states plainly: *"Leverage and derivatives | Prohibited"* for its non-Malaysian equities phase.
That's the more rigorously-documented version of essentially this same system concluding options
should be excluded entirely. This hackathon's mandatory-options requirement means deliberately
loosening that stance for four specific, asset-backed structures — the demo/writeup should present
that as a conscious, labeled, and narrowly-scoped exception, not imply it was already vetted policy.
This mirrors that project's own fail-closed principle (`shariah-gating-rules.md`): *"No strategy
return, analyst opinion, AI confidence score, or user request can override a failed Shariah gate"*
— the options gate needs to hold itself to the same standard, freshly justified for this new scope.

## Audit trail requirement

Every gate decision (PASS or REJECT) should be logged with a human-readable justification string,
extending the existing execution-audit contract rather than adding a parallel logging path —
e.g. `"covered_call: underlying=MSFT shariah_status=PASS shares_held=100 strike=410 basis=395"`.
This is what turns the compliance layer into a demo-able artifact instead of an invisible filter.

## Alpaca options-level mapping (confirmed via docs.alpaca.markets)

Alpaca gates option strategies by account "trading level," and the levels map cleanly onto the
allow-list above:

| Alpaca level | Strategies unlocked | Needed for |
|---|---|---|
| Level 0 | Options disabled | — |
| Level 1 | Sell covered call, sell cash-secured put | Primary track (Income & Portfolio Overlay) — this is enough |
| Level 2 | + buy calls/puts | Protective puts, secondary/stretch track (Hedging & Risk Protection) |
| Level 3 | + call/put spreads | Not needed for either planned track |

Options trading is **auto-enabled on paper accounts already at Level appropriate for testing** —
no separate upgrade request needed for paper trading. Keeping the primary track scoped to Level 1
(covered calls + cash-secured puts only) means the build doesn't need to touch Alpaca's
options-level upgrade flow at all, which shrinks the integration surface for the 7-day window.

## Open questions for implementation (later session)

- Does Alpaca's options data (Greeks, chain, assignment status) map cleanly onto the
  "agent holds N shares" check needed for covered-call/collar eligibility, or does position
  tracking need to be duplicated locally the way `portfolio_store.py` already does for equities?
- Should the structure gate be a static allow-list (table above) or configurable via env vars the
  way `risk_checks.py` limits are (`MAX_POSITION_PCT`, etc.), for consistency with existing
  patterns?
- Where does the "purification calculator" idea from `IDEAS.md` plug in — as part of this gate, or
  as a separate post-trade reporting step?
