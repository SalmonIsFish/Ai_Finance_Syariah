# Submission Checklist — Alpaca AI Trading Agents Hackathon

Mapped from the event page (https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon)
and the general lablab.ai Hackathon Rule Book (https://lablab.ai/hackathon-rules), both fetched
2026-08-18. Nothing here is done yet — this is a checklist to fill in before 4 Sep 2026, 15:00 UTC
(11:00 PM Malaysia Time).

## Account setup

**Update (2026-08-27):** re-checked the event page's "Account requirements" section directly —
adds two hard requirements that weren't captured here before.

- [ ] Sign up for Alpaca, open a paper trading account for early prototyping (any account is fine
      for this stage — "explore freely" per the event page).
- [ ] Before final submission: create a **brand-new** Alpaca paper trading account dedicated to
      this hackathon. Projects on a reused/existing account are **not eligible for judging** —
      this is a hard disqualifier per the event page.
- [ ] **Set the competition account's starting balance to exactly $100,000.** This is a stated
      account requirement, not a suggestion — verify it at account creation, don't assume Alpaca's
      default paper balance matches.
- [ ] Record that account's ID — required in the final submission for judges to evaluate P&L.

## Core technical requirements (all mandatory to qualify)

- [ ] Autonomous agent built using Alpaca's **Trading API**.
- [ ] Uses either Alpaca's **MCP server** or **CLI** (not optional — one of the two is required).
      Repo: https://github.com/alpacahq/alpaca-mcp-server — run via `uvx alpaca-mcp-server`
      (needs Python 3.10+ and `uv`); Docker deployment also available.
- [ ] Strategy **incorporates options trading** in some form (mandatory in every track). Level 1
      (covered call + cash-secured put) covers the primary track and needs no account upgrade —
      see `SHARIAH_GATE_NOTES.md` for the full level mapping.
- [ ] Everything runs against the **paper trading environment** — no real capital.

## Alpaca credentials (do this yourself — do not paste the key into chat)

The MCP server reads `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` from **environment variables only**,
set in the MCP client's own config (its docs say "credentials are set in one place only" — no
`.env` file for the MCP server itself). For anything in this repo's own `backend/` that calls
Alpaca directly (outside the MCP server), follow the existing pattern in `backend/config.py`
(`os.getenv(...)` reads from `backend/.env`, same as `ZOYA_API_KEY`) — add
`ALPACA_API_KEY_ID` / `ALPACA_SECRET_KEY` there yourself when that code exists.

- [ ] Create the Alpaca API key/secret in the Alpaca dashboard.
- [ ] Set it directly in your MCP client config / `backend/.env` yourself — not shared in chat.
- [ ] Confirm `ALPACA_PAPER_TRADE` (or equivalent) is `true` before any testing.

## Track

**Update (2026-08-27):** re-checked the event page — the multi-track structure this repo's
`IDEAS.md` was written against no longer exists. There is now exactly **one track: "Options Alpha
Agents"** (main track, open to all). No track choice to make anymore; the only thing that matters
is meeting the mandatory options-trading requirement below and scoring well on the four judging
criteria. `IDEAS.md`'s track comparison (Income & Portfolio Overlay vs. Hedging & Risk Protection
vs. Options Alpha vs. Volatility & Event) is now historical context for *why* Level 1 structures
were chosen, not a live decision.

- [x] Track confirmed: Options Alpha Agents (only track — nothing to pick).

## Submission package

- [ ] Project title (clear, descriptive).
- [ ] Short description.
- [ ] Long description.
- [ ] Technology & category tags.
- [ ] Cover image — PNG or JPG, **16:9 aspect ratio**.
- [ ] Video presentation — **MP4**.
- [ ] Slide presentation — **PDF**.
- [ ] Public **GitHub repository**.
- [ ] Application URL for the hosted demo. **Correction (2026-08-21):** the actual submission
      form has no platform restriction — it's a free-text Application URL field, not a choice
      among Streamlit/Replit/Vercel. Any working, publicly reachable URL qualifies (the project
      now hosts on a self-managed VPS at `https://amanahtrader.uk`).
- [ ] Alpaca paper trading account ID (see Account setup above).
- [ ] **One-page write-up** covering: AI logic, risk gates, and Alpaca infrastructure
      implementation. (Added to the event page's requirements as of 2026-08-27 — not just the
      video/slides; this is a separate written artifact.) For this project that maps cleanly to:
      AI logic = quant signal + option strike selection (`option_strategy.py`); risk gates = the
      four-gate chain (`shariah_gate` → `option_structure_gate` → `account_shariah_gate` →
      risk limits); Alpaca infrastructure = the dual REST/MCP transport and the
      preview → approval → execute → reconcile pipeline. Draft from `CLAUDE.md`'s Architecture
      and Module ownership sections rather than starting from scratch.
- [ ] Up to 5 social media post links (X or LinkedIn), tagging **@lablabai**/lablab.ai and
      **@AlpacaHQ**/Alpaca. Optional but scored under the extra "Build in Public" challenge and the
      Social Engagement judging criterion.

## Judging criteria to keep in mind while building (from the event page, re-verified 2026-08-27)

Confirmed exact criteria text on the live page — it's four criteria now, not five; "Social
Engagement" is not a listed judging criterion (the social-post fields in the submission form may
still exist but don't score under a named criterion the way the four below do):

- **P&L Performance** — "the trading performance of the submitted agent in the Alpaca paper
  trading environment... P&L and how effectively the strategy performs through its trading
  activity." This is the weak point — see `WIN_PLAN.md` for why and what to do about it.
- **Technology Implementation** — "how effectively the project uses Alpaca's Trading API, MCP
  server, CLI, and other required technologies." Current strongest axis.
- **Creativity & Originality** — "the originality of the concept, trading strategy, agent
  behavior, and overall approach." The Shariah-gate angle carries this; the underlying options
  strategy itself is deliberately vanilla — see `WIN_PLAN.md`.
- **Presentation & Execution** — "how clearly and effectively the project communicates its idea,
  demonstrates the agent in action, and presents the reasoning behind its trading strategy and
  results." Scripts/drafts exist in `research/`; nothing is actually produced yet.

See `WIN_PLAN.md` for the ranked action plan against these four.

## Team

- [ ] Confirm team size (1–6 allowed) and, if a team, agree in advance who receives prize funds —
      prizes are paid to one individual, not split automatically by lablab.ai/Alpaca.

## Logistics

- [ ] Register on both the lablab.ai platform and the lablab.ai Discord server (both required to
      participate, per Rule Book).
- [ ] Read the Hackathon Guidelines, Getting Started Guide, and Rule Book before kickoff.
- [ ] Kickoff: Aug 28, 11:00 PM Malaysia Time. Discord Q&A: Aug 29, 12:00 AM Malaysia Time.
- [ ] Manual submission fallback exists (6 hours post-hackathon) only with prior organizer/mentor
      approval for valid technical issues — don't rely on this as a plan.
