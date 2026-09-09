# Win Plan — Alpaca AI Trading Agents Hackathon

**SUPERSEDED — 2026-08-29.** Kickoff Q&A confirmed (3x independently) that every file in the
submission repo must be authored during the event window — this repo's pre-kickoff history
(2026-07-21 to 2026-08-26) disqualifies it from being the submission repo. This repo is now a
**private dev-reference only**. The actual hackathon build is at
`E:\Github2\alpaca-hackathon` (github.com/SalmonIsFish/alpaca-hackathon, private) —
read `BUILD_PLAN.md` there, not the rest of this file, for the live plan. This file is kept
below as history of the reasoning that led to the pivot.

---

**Written:** 2026-08-27, the day before kickoff, for resuming work once the event window opens
(Aug 28–Sep 4, 2026, 15:00 UTC deadline). Read this before doing anything else on Day 1.

## Don't rebuild what already works

Before touching code, know what's already proven so effort doesn't get wasted re-verifying it:

- Full gate chain live and tested: Shariah screen (SEC EDGAR) → option-structure gate → account
  Riba gate → risk limits, all fail-closed, all routed through the one entry point
  (`shariah_candidate.build_shariah_candidate`).
- Two real fills already happened pre-event: 1 share CVX (equity) and 1 short AAPL put
  (cash-secured put), both verified three independent ways each. These prove the mechanism works
  but **do not count for judging** — they're on the test account (`0TCX`), and the event requires
  a brand-new dedicated paper account.
- 42 tests passing, dashboard live at `amanahtrader.uk`, `/explain` endpoint with citation-backed
  Shariah reasoning, full evidence trail in `docs/live-trade-evidence/`.
- Both Alpaca transports wired (REST and MCP via `alpaca_mcp`), satisfying the mandatory
  "Trading API + MCP server or CLI" requirement already.

The remaining work is not "does the gate chain work" — it's account setup, generating a real
track record, and producing the submission collateral. Treat any urge to re-architect the gate
chain during the event window as scope risk, not progress.

## Rules, reconfirmed 2026-08-27 (don't trust older notes in this repo without rechecking)

- One track only: **Options Alpha Agents**. The multi-track comparison in `IDEAS.md` is now
  historical — it explains *why* Level 1 structures were chosen, it's not a live decision.
- Four judging criteria: P&L Performance, Technology Implementation, Creativity & Originality,
  Presentation & Execution. (Older docs in this repo reference a fifth "Social Engagement"
  criterion — that's not on the current page; don't spend effort chasing it as if it's scored.)
- Mandatory: Alpaca Trading API + (MCP server or CLI) + options trading incorporated + paper
  environment only + a brand-new dedicated paper account for the final submission.
- **The dedicated account must start at exactly $100,000.** Verify at creation — don't assume
  Alpaca's default paper balance matches.
- **A one-page write-up is a required deliverable**, separate from the video/slides: it must
  cover AI logic, risk gates, and Alpaca infrastructure implementation. See
  `SUBMISSION_CHECKLIST.md` for how this project's modules map onto those three sections.
- Deadline: Sep 4, 2026, 15:00 UTC. Manual fallback exists only with prior organizer approval —
  don't plan around it.

## Ranked priorities — do these in this order

### 1. P&L / track record — the single biggest gap, and the most time-sensitive

This is the one weakness that can't be fixed by working harder on the last day; it needs elapsed
time in the market. Start it Day 1, not Day 5.

- Provision the new dedicated Alpaca paper account **on Day 1**, set its starting balance to
  **exactly $100,000**, and record its ID in `SUBMISSION_CHECKLIST.md` immediately.
- Run the demo trades **against the deployed instance at `amanahtrader.uk`, not a local
  checkout** — `backend/*.db` is gitignored, so a locally-run trade writes to a database the
  deployed instance and any judge visiting the live URL will never see. This bit the project
  before (the CVX position living only in a worktree DB); don't repeat it with the account that
  actually matters.
- Favor short-dated structures (0–7 DTE covered calls / cash-secured puts) specifically so trades
  actually expire and realize P&L inside the 7-day window, rather than sitting open unrealized at
  judging time.
- Trade on multiple separate days, not once — a single trade is a demo, not a track record.
  `portfolio_metrics.py` already computes risk-adjusted return off the equity curve; it just needs
  more than one point on that curve to say anything.
- In the submission writeup, frame results as risk-adjusted return (Sharpe/Sortino, drawdown),
  not raw P&L. This is honest to what the strategy actually is (income, not directional alpha) and
  protects against one unlucky trade tanking the raw number.

### 2. Presentation & Execution — high leverage, mostly logistics now

The thinking work is done; the production work isn't started.

- `research/demo-video-script-draft.md` is a full script but keyed to the pre-event test account
  and has bracketed placeholders — it needs a rewrite pass once the new account has real
  event-window trades, not a read-through.
- Nothing is actually produced yet: no recorded video (MP4), no finished cover image (16:9 — a
  placeholder exists at `assets/cover-image.png`), no slide deck (PDF). Budget real time for this;
  it's not a 30-minute task.
- Show the *reasoning*, not just the result — the `/explain` panel and the Shariah Trace are the
  project's actual strength here; don't let the demo become a plain fills list.

### 3. Creativity & Originality — mostly banked, one framing decision left

- Own the split explicitly in the pitch: the **compliance-gate architecture** is the novel part;
  the **options strategy itself** (fixed ~4% OTM, 1–7 DTE covered calls / cash-secured puts) is
  deliberately conventional and low-risk, not a novel alpha signal. Under a track literally named
  "Options Alpha Agents," say this up front rather than let a judge feel oversold on "alpha."
- If there's spare time: the purification-calculator idea from `IDEAS.md` (earmarking the impure
  portion of premium income for donation) is a cheap Technology Implementation point — pure
  computation on data the Shariah screen already produces, no new API needed.

### 4. Technology Implementation — already the strongest axis, just protect it

- Don't casually modify the gate chain during the event window. `CLAUDE.md`'s rule (run local
  tests before changing anything broker-facing) still applies, especially under deadline pressure.
- Make sure the demo/writeup actually shows the **MCP transport** (`alpaca_mcp`), since "uses MCP
  server or CLI" is an explicit eligibility/judging axis, not just REST — don't let the REST
  adapter be the only thing on screen.

## Known weaknesses, stated plainly

- No account-level track record yet on an eligible account — the biggest risk, and the only one
  that's time-locked rather than effort-locked.
- The options structures are static (fixed OTM band, no adaptive sizing or multi-signal logic) —
  expect this to score fine on compliance creativity but modestly on "alpha" specifically.
- Local vs. deployed database split is a real footgun that already caused a lost demo trade once
  (see `CLAUDE.md` Known limitations §4) — re-read that before running the account-setup trades.
- Submission collateral (video, slides, cover image) is at 0% produced despite scripts existing —
  don't mistake a finished draft for a finished deliverable.
- `PRE_EVENT_TIMELINE.md` flags an open question (as of 2026-08-25) on whether lablab.ai requires
  disclosing pre-event work — confirm this before final submission if it's still unresolved.

## Rough day-by-day skeleton

- **Day 1 (Fri Aug 28, kickoff):** new dedicated paper account provisioned, starting balance set
  to $100,000, ID recorded, first real trade submitted on it against the deployed instance.
- **Days 2–5:** keep trading short-dated structures every day or two to build a real equity curve;
  in parallel, start capturing dashboard footage, drafting final narration with real numbers as
  they land, and drafting the one-page write-up (AI logic / risk gates / Alpaca infrastructure)
  rather than waiting for the curve to "finish."
- **Day 6 (Wed Sep 3):** freeze anything broker-facing; finalize video, slides, cover image,
  one-page write-up, submission copy.
- **Day 7 (Thu Sep 4, 15:00 UTC deadline):** submit early. The manual fallback needs prior
  organizer approval — it is not a safety net to plan around.
