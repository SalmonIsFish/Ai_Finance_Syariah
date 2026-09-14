# Operator Console Brief — Dashboard v2 Design Direction

Prepared September 2026. Scope: dashboard only — no changes to the gate chain, `local_api.py`
decision logic, or API contracts. This is a rebuild of `dashboard/index.html` as a proper app,
keeping the ink-navy-and-brass identity the current CSS already reaches for.

## 1. What's already right (keep it)

The current stylesheet's own comments show real intent, not defaults:

> "Ink-navy operator console. Brass is the only accent — it never doubles as a status color, so
> PASS/FAIL/WARN always read from --ok/--warn/--bad alone, regardless of accent hue."

Three things to keep outright:

- **The operator-console idea.** Dark ink-navy ground, one warm brass accent reserved for
  actions/emphasis only, never for meaning. Keep this discipline.
- **The committee-of-officers metaphor.** The four agents already read as *Quant Manager*,
  *Shariah Compliance Officer*, *Risk Manager*, *Trader — Execution Desk*, set in serif, with an
  "officer-desk-seal" element for a stamped verdict (see `.officer-desk-seal` in the current
  `dashboard/index.html`). This is a distinctive, on-subject idea — a compliance panel issuing a
  ruling — and should be the centerpiece of the new build, not stripped out for a generic SaaS
  look.
- **The light mode already avoids the obvious trap.** Its own comment rejects "the cream + serif
  + terracotta combination" that most AI-generated fintech UIs default to. Keep that instinct.

## 2. What's actually missing: a fourth color

Amanah Trader's whole safety model rests on a three-state Shariah gate: `PASS`, `REJECT`,
`UNKNOWN` — and the one rule that can't bend is that `UNKNOWN` must never collapse into `REJECT`,
visually or otherwise. The current stylesheet only defines `--ok`, `--bad`, and a `--warn`
borrowed from generic dashboard convention. **There is no color for "we don't know yet"** — the
exact state this architecture exists to protect. Today, when a third state needs a color, it
either borrows red (reads as "leaning reject") or amber (reads as "a warning," not "unresolved").

**Fix: add a fourth semantic token, `--unknown`** — a cool slate-blue, nowhere near warn's amber
or bad's red, so an unresolved verdict reads as genuinely distinct:

| Token | Dark value | Light value | Role |
|---|---|---|---|
| `--unknown` | `#7f9fd9` | `#3a5a8c` | UNKNOWN gate state — pending/unresolved, never a synonym for reject |

This one addition does more for the rebuild's honesty than any other visual change — it makes the
gate chain's core promise legible at a glance, which is the actual job of this screen.

## 3. Palette (full reference)

Existing ink-navy-and-brass system, kept, plus the addition above:

| Name | Dark hex | Light hex | Role |
|---|---|---|---|
| Ink | `#0b0f1a` | `#f4f6f9` | Ground |
| Panel | `#131a2c` | `#ffffff` | Cards, surfaces |
| Brass | `#b8925a` | `#7c5b2e` | The one accent — actions only, never status |
| Ok | `#5fae86` | `#2f8f63` | PASS, gains, cleared |
| **Unknown (new)** | `#7f9fd9` | `#3a5a8c` | UNKNOWN gate state — pending/unresolved |
| Warn | `#e3b341` | `#c07f1f` | Approaching a limit |
| Bad | `#e0685c` | `#c2483c` | REJECT, losses, failed |

All other existing tokens (`--text`, `--muted`, `--subtle`, `--border`, `--border-strong`,
`--radius`, `--shadow`) carry over unchanged from the current `dashboard/index.html` `:root` and
`[data-theme="light"]` blocks — don't reinvent these, just port them into Tailwind config as
CSS-variable-backed colors rather than replacing with Tailwind's default palette.

## 4. Type

Three roles, already implicit in the current file — apply them consistently via components
instead of ad hoc per-element `font-family` declarations:

- **Display / serif** — headings, officer names/roles, the verdict seal. Currently Georgia stack;
  recommend Source Serif 4 (Google Fonts) for the same "printed ruling" register with better
  hinting, or keep the existing Georgia stack if avoiding an external font load matters more.
- **UI / sans** — Inter, for chrome, labels, buttons, everything structural. Already in use, keep
  it.
- **Data / mono** — the existing mono stack, for every number: prices, quantities, percentages,
  tickers. Apply `font-variant-numeric: tabular-nums` everywhere numbers appear in columns — the
  current build doesn't do this consistently, so columns of prices don't align.

## 5. The verdict card, finished

The "officer" pattern already exists in the code (`.officer-role`, `.officer-detail`,
`.officer-desk-seal`). Two refinements:

- **Confidence as binary, not a percentage.** Show `High` / `Low`, not `87%`. Research on
  agent-decision interfaces consistently finds a binary confidence read produces faster decisions
  than a precise-looking percentage nobody can actually calibrate against — and this codebase has
  no real calibrated-probability model backing a number like "87%" anyway, so a fabricated-looking
  precision would be actively misleading.
- **The seal only appears on an actual PASS.** For REJECT or UNKNOWN, the status chip alone
  carries the verdict — no seal — so "stamped" keeps meaning something rather than decorating
  every card regardless of outcome.

Example card content (for reference, not literal markup):

```
Shariah Compliance Officer
Ruling on CVX · BUY 1 @ $207.12                    [UNKNOWN]
Publication: sc-sac-my-2026-05-29
Reason: symbol_not_us_or_my
Confidence: High — no ambiguous match
```

```
Shariah Compliance Officer
Ruling on 1023.KL · BUY 400 @ RM4.12                [PASS]
Publication: sc-sac-my-2026-05-29 (activated)
Reason: symbol_compliant
✒ Ruled compliant — sc-sac-my-2026-05-29
```

## 6. Structure: one long page → a console with rooms

The current build is a single scroll through roughly twenty sections: Market Overview,
Opportunities, Stock Profile, Agent Summary, Committee, Approval Queue, Paper Orders, Risk
Policy, Account Balances, Portfolio Value History, Current Allocation, Live Broker Positions,
News, Execution Audit, Audit History, and more. Everything competes for the same screen at once.

Proposed rooms — **every existing section should map into exactly one of these; none should be
dropped silently**:

| Room | Contains | Why |
|---|---|---|
| **The Desk** (front door) | Ticket entry (symbol/side/qty/price), the four officer cards evaluating live, approve/reject action | The one screen that must be reachable in a single click from anywhere — this is the app's whole reason to exist. This is the "Agent Evaluation" + "Agent Summary" flow from the current dashboard, and it deserves to be the hero view, not folded into a later phase. |
| **Portfolio & Risk** | Account Balances, Portfolio Value History, Current Allocation, Live Broker Positions, Risk Policy | Standing state. Primary-metric pattern: live equity shown large, everything else secondary. |
| **Market & Screening** | Market Overview, Opportunities, Stock Profile, News | Research/"what should I consider" questions — merged, since they're all the same job today spread across four sections. |
| **The Ledger** | Approval Queue (history, not the live evaluation — that's The Desk), Paper Orders, Execution Audit, Audit History, Event History | One searchable, filterable record instead of four. Status honesty matters most here: pending vs. cleared vs. failed, always visible, never implied. |

Within each room, progressive disclosure replaces "show everything": a summary row that expands
to detail on click, not a page rendering all detail whether or not anyone's looking.

## 7. The approval moment

The one irreversible action in the app deserves a dedicated review, not a button next to a table
row. What it should show:

| Field | Example |
|---|---|
| Symbol / side / qty | CVX · BUY · 1 share |
| Server-derived risk verdict | PASS — position 0.21% of 5.00% limit |
| Weekly loss so far | 0.30% of 2.00% limit |
| What approving actually does | Submits to the paper broker immediately — not reversible from here |

The verdict shown at approval must be labeled as what it is — the value
`authoritative_risk_verdict` / `authoritative_shariah_verdict` just recomputed server-side at
click time, not the client's cached copy from preview. Label it "recomputed just now," not just
"risk: pass," so the UI makes that server-side re-derivation visible rather than silently trusted.

## 8. Build approach

4,138 lines in one HTML file is why "fix the spacing" turns into "fix everything" — there's no
seam to change one thing without touching the whole document. React + Vite + Tailwind, compiling
to static output served the same way the current file is (FastAPI static serving under
`/dashboard/`, behind the existing owner-only auth gate). Tailwind's utility classes should read
from the CSS-custom-property palette in section 3, not Tailwind's default theme. The officer card,
the status chip, and the ledger row should each be one component reused everywhere they appear,
instead of copy-pasted blocks that drift apart over time.

## 9. What this brief does not touch

- The gate chain, its logic, or its API contracts. Frontend rebuild against existing endpoints
  only.
- No LLM in the decision path, in the UI or otherwise. A model may narrate a verdict already
  reached; it must never appear to be the one reaching it. (Also: this whole layer is currently
  deliberately deferred per `NEXT_STEPS.md` pending the model-choice decision — nothing here
  should anticipate or half-build it.)
- The owner-only auth gate on `/dashboard/` and its API surface. The rebuild ships behind it, not
  around it.
- PASS / REJECT / UNKNOWN semantics. The fourth color is a rendering fix, not a new state — the
  gate chain already returns three; the screen just couldn't say so until now.
