# Development Timeline — Pre-Event Disclosure

**Purpose:** an honest, commit-history-backed record of what was built before the Alpaca AI
Trading Agents Hackathon's official window (Aug 28 – Sep 4, 2026), for inclusion in the
submission if lablab.ai's organizers confirm disclosure is required or useful. See the Discord
thread with Monika (lablab.ai), 2026-08-23/24: pre-event research, tech familiarization, and idea
gathering are explicitly allowed; "preparing repo is a tricky one because judges might look when
the repo was created and consider it the start of building." This document is the disclosure —
drafted proactively rather than waiting to see whether it's mandatory, since the repo's git
history already makes the timeline verifiable either way.

**Repo created:** 2026-07-21. **Event window:** 2026-08-28 – 2026-09-04. Everything below predates
the event window. Nothing in this file has been edited to obscure dates — it is generated
straight from `git log`.

## Phase 1 — Initial scaffold (2026-07-21 to 2026-07-25)

The original local paper-trading app: preview/approval flow, a deterministic agent coordinator,
market-aware Shariah routing, a structured approval queue, a paper-execution lock, portfolio risk
controls, and the first set of API contracts (stock profile, investment committee, market
overview, positions, execution audit). No Alpaca integration yet — this phase targeted Moomoo.

*(No commits between 2026-07-25 and 2026-08-18 — a ~3.5 week gap.)*

## Phase 2 — Alpaca migration and the Shariah gate chain (2026-08-18 to 2026-08-19)

Alpaca paper adapter and market data replace Moomoo as the primary broker path. The full gate
chain takes shape: `shariah_gate`, `option_structure_gate`, `account_shariah_gate`, routed through
one entry point (`shariah_candidate.build_shariah_candidate`). A self-built Shariah screen against
SEC EDGAR replaces the Zoya sandbox. Level 1 option strike selection, a Shariah Trace panel
(verdict → rule → fiqh basis → citation), and the first Replit deployment also land here. This
phase ends with the first live end-to-end proof: a real CVX equity fill against the paper broker.

## Phase 3 — First live option fill, hosting migration (2026-08-20 to 2026-08-21)

A live cash-secured put fill (AAPL) — the option-side counterpart to Phase 2's equity proof — plus
the bug it exposed (an equity-only reduce-only rule wrongly applied to a sell-to-open option).
Portfolio value history and a news room ship on the frontend. Ruff linting is configured. Hosting
moves off Replit to a self-managed VPS at `amanahtrader.uk`.

## Phase 4 — Hardening and dashboard build-out (2026-08-22 to 2026-08-24)

Dashboard redesign (ink-navy/brass operator console). Every screening verdict now logged
(`GET /shariah/screens`). VPS hardening (fail2ban, hardened nginx vhost). Live account
balance/positions/equity-curve endpoints and dashboard panels. A fail-closed, non-advisory
`GET /news` endpoint with AI summaries, bounded so it can't outlast a reverse-proxy timeout.
Repo cleanup for submission (internal docs, stale hosting files, dead scripts removed). Quant
agent given self-explanatory narration and a second (pullback) entry strategy; sector
concentration added as a fourth portfolio risk limit.

## What this timeline does not include

- Anything dated 2026-08-28 or later — none exists yet as of this writing (2026-08-25).
- The dedicated hackathon paper account has not been provisioned yet; all live trades referenced
  in `CLAUDE.md` (`0TCX`) ran on a test account, per `SUBMISSION_CHECKLIST.md`.

## Open question

Whether lablab.ai requires this kind of disclosure, permits pre-event repos with the caveat that
only event-window commits count toward judging, or takes some other position, was not resolved as
of 2026-08-25 — Rahul's direct question to Monika/LabLab Admin in Discord was answered on the
general policy but not on the disclosure point specifically. Update this section once that's
answered, and keep this file updated with event-window work as it happens rather than writing it
retroactively at submission time.
