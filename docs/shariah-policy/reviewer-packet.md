# Reviewer packet — what a Shariah scholar is actually being asked to rule on

Status: **draft, awaiting expert panel.** Prepared 2026-09-22.

This packet exists so a reviewer is not handed a codebase. It lists only the
questions where **this system makes its own determination** and therefore needs
one, in order of how much depends on the answer.

## What is NOT being asked

Two things are deliberately out of scope, because the system does not decide them:

- **Malaysian security eligibility.** The Securities Commission Malaysia's Shariah
  Advisory Council classifies securities; this system applies the published list
  and proves it applied it faithfully. The classification is not ours to defend.
  *(Note the converse, which a reviewer already made explicit: being on that list
  does not certify this system.)*
- **Whether algorithmic trading is permissible as such.** The claim made here is
  the narrow one: a systematic strategy does not, by itself, constitute *maysir*;
  permissibility depends on the underlying securities, transaction structure,
  trading mechanism and applicable principles.

---

## Question 1 — The margin account. Blocking, and may disqualify the broker.

**Where:** `docs/shariah-policy/margin-account-policy.md` — *"Status: decision
pending scholar review"*, recorded 2026-08-19.

The broker (Alpaca) offers no cash account. The remedy adopted is a revocable
1× cap, i.e. no leverage is extended in practice, while the account type remains
margin-capable.

**To rule on:**
- Is a 1×-capped margin account acceptable, or is the *margin agreement itself*
  disqualifying regardless of whether leverage is used?
- Should the gate also reject an account with `shorting_enabled`? It currently
  ignores that flag.

**Why it is first:** the policy document states that under the strict reading,
"the conclusion is that Alpaca cannot be used for this system at all, by anyone."
This decides the broker, not a configuration value.

---

## Question 2 — Options. Currently blocked; is the block right?

**Where:** `backend/option_permissibility.py` (the determination) and
`backend/option_structure_gate.py` (the structure table it sits in front of).

*Updated 2026-10-05.* Since 2026-09-23 **no option contract can be entered into.**
The recorded determination follows the grounds reported for the OIC Islamic Fiqh
Academy and Mufti Taqi Usmani: gharar, maysir, and the premium (a promise is not a
valid subject of sale). It is a code constant, not a setting, and it is enforced
at preview, at approval and at strategy proposal.

Before that, four structures were allowed (`covered_call`, `cash_secured_put`,
`protective_put`, `collar`) because a hackathon required options. The notes
recorded that as *"a deliberate scope extension, not a continuation of prior
policy."* One cash-secured put filled on the paper account on 2026-08-20, before
the block. That record is kept, not erased.

**To rule on:**
- Is blocking all option contracts correct for this system?
- The cited sources treat options generally. Does writing a **covered call against
  shares already owned**, or a **cash-secured put fully backed by settled cash**,
  fall under the same ruling? Those are the only two structures this system has
  ever used. If the answer differs between them, the determination and the gate
  table are the only places that change.

---

## Question 3 — Purification. Three questions the code deliberately does not answer.

**Where:** `backend/holdings_compliance.py`, `purification_due()`.

The implemented rule, per the owner's reading of SC guidance: a holding
reclassified non-compliant is disposed of within one month, the original cost is
retained, and anything above cost is given away.

The function is deliberately confined to two inputs with no price source, no date
logic and no database access, so the whole rule can be reviewed at once. Its
docstring names what it cannot settle:

> 1. Which price is `proceeds` — the announcement-day close, or the actual
>    disposal price? They differ, and the difference is the donation.
> 2. Are dividends received while the holding was compliant treated separately
>    from capital gain?
> 3. What applies if the price never recovers to cost within the month?

**Also for the panel:** the repo's own notes on SC screening criteria appear to
answer (2) — dividends after the effective date go to baitulmal — and to permit
holding below cost. Neither is implemented. A reviewer should confirm whether
that reading is correct before it is built.

**Current state:** the system produces an *estimate* of what would be owed at
current prices. Nothing records a discharged obligation. No claim of purification
should be made until this is ruled on and the discharge is recorded.

---

## Question 4 — The US screen is an approximation, not an authority.

**Where:** `backend/sec_edgar_screen.py`, module docstring.

There is no SC equivalent for US equities, so the screen is self-built over SEC
EDGAR filings. The module states its own limits plainly: business activity is
approximated by SIC code rather than a revenue breakdown, and XBRL cannot
distinguish Islamic from conventional instruments, so all cash and all debt are
counted and both ratios are **overstated**.

**Direction of error is toward rejection**, which is the safe direction. Two gaps
are not safe:

- A conglomerate with a benign primary SIC code but non-compliant revenue passes
  unseen.
- The SC's **qualitative limb is not implemented** at all; public perception is
  left to the human approver, who is not a scholar.

**To rule on:**
- Are the thresholds and the SIC exclusion table acceptable as an approximation?
- Is applying a Malaysian SAC-derived methodology to US filings defensible, or
  does the US path need its own basis?
- Is a 5% mixed-revenue tolerance with income purification required? The repo
  carries both a 5% and a 33% threshold in different documents and implements
  only the ratio screen.

---

## Question 5 — For documentation rather than ruling

- **The three-state gate.** `shariah_gate.py` returns PASS / REJECT / UNKNOWN and
  never collapses UNKNOWN into REJECT; UNKNOWN blocks trading but is never
  recorded as a ruling of ineligibility. Verified in code, not assumed. A
  reviewer may wish to confirm the principle is correctly applied, but nothing
  is being asked.
- **Human authorisation.** See `docs/TRADING_MANDATE.md`, generated from the
  enforced configuration.

---

## How to use this packet

Each question above is answerable without reading code. Where a ruling changes
behaviour, the change is confined to one named module — the gate table, the
threshold constants, or the purification function — which is why those were kept
free of surrounding logic.

**Nothing in this repository should describe itself as Shariah-compliant,
certified or validated until questions 1–4 carry a signature.**
