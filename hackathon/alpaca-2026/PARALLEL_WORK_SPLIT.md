# Parallel Work Split — three terminals

Coordination doc for three terminals working the hackathon build at once. Supersedes the
previous two-terminal version of this doc — that split (`feature/shariah-options-gate` /
`feature/alpaca-adapter`) is done; both branches are merged into `master` and deleted. Written
so a fresh session in any of the three terminals has everything it needs without replaying prior
conversation.

**2026-08-20 update:** the margin blocker below is resolved (see `NEXT_STEPS.md`), and one real
equity trade (CVX) has run end to end against the real Alpaca paper API. This revision reflects
the plan from a three-round council consult (`council_output_live_trade_and_next_steps.md`,
`council_output_round3_synthesis.md`) on what to do with the ~8 days remaining before kickoff
(Aug 28) — read the round-3 file for the reasoning; the summary is below. The task lists in this
revision supersede the ones below them where they conflict; sections not mentioned in the
2026-08-20 task lists (e.g. the original handoff contract, merge checkpoint) still apply.

## ⏸ Live status snapshot — read this first if resuming a new session

Written 2026-08-20, afternoon session (Malaysia time), because the coordinating session's usage
ran low mid-task and had to hand off. This is the actual state of all three terminals *right
now* — more current than the task lists below, which describe the plan, not necessarily what's
already landed. A fresh coordinating session should read this section first, then verify each
item against the actual repo/branches before acting on it (branches may have moved since this was
written).

**Terminal 1 (frontend/demo) — nearly done, one decision pending:**
- Deploy verified live and working: real Playwright click-through against the hosted URL
  (`https://alpaca-hackhaton-ai-finance-syariah--adibluqman117.replit.app`) — AAPL preview, real
  SEC_EDGAR COMPLIANT verdict, real `alpaca_iex` prices, Shariah Trace panel rendering from the
  real `/explain` endpoint (not the fallback), Reject flow, all screenshotted as evidence.
- Two real bugs found and fixed, both merged and live: the hidden `#optionStrike` input blocking
  form submit on Equity orders, and a `DOMContentLoaded` race condition causing a red
  "Disconnected" flash on first load (commit `d74e536`). Both confirmed fixed on the live URL by
  the project owner directly.
- Alpaca test-account (`0TCX`) keys are set as Replit Secrets and confirmed working (real IEX
  data, not fixtures). `ALPACA_MODE`, `SEC_EDGAR_USER_AGENT` also set.
- **Open, unresolved: SQLite does not persist across a Replit restart on the free/Autoscale
  tier.** Terminal 1 researched fixes: Replit's Object Storage is not a mounted filesystem (would
  need a real backend rewrite — out of scope, correctly declined); Replit's managed Postgres is
  the DB migration explicitly ruled out earlier. **The only real fix is Reserved VM** ($15–50/mo
  depending on spec; always-on, no idle-triggered restart, survives crashes — only an explicit
  redeploy wipes it, which is avoidable by just not republishing near judging). A free mitigation
  (GitHub Actions keep-alive ping, `/health` every 5 min) is implemented and active. A
  marker-persistence verification test was in flight (~20 min out) when this was written — check
  `docs/live-trade-evidence/` or ask Terminal 1 for the result. **Decision pending: does the
  project owner want to pay for Reserved VM for the final week, or rely on the free keep-alive
  alone?** My (the coordinating session's) recommendation, given a $5,000 prize pool and a hard
  "live demo URL" submission requirement: pay for it, as belt-and-braces on top of the keep-alive,
  once the verification result is in — but this is genuinely the project owner's call, not mine.
- Also queued, not yet started: known issue "SEC EDGAR returns `http_403` on the deployed
  instance specifically" (checked — not a User-Agent problem, `sec_edgar_screen.py` already sets
  one; almost certainly SEC blocking Replit's shared egress IP). Assigned to Terminal 2, not yet
  picked up as of this writing.

**Terminal 2 (backend) — merged into `master`; one human step outstanding:**
- Margin fix, first equity trade (CVX, 206.89 fill), `/explain`, `/option-strategy`, duplicate
  screening-path removal: all done (see main `NEXT_STEPS.md`).
- Minimal EDGAR cache shim: done (`backend/sec_edgar_cache.py`), explicitly labeled temporary,
  4 mutations caught, throttled to ~8 req/s.
- `provision_cash_account.py` defect found and fixed: a `float(None or 0)` silently zeroed the
  multiplier field on an absent/unparseable value, meaning a fresh-account run could report
  success while leaving a 4x account at 4x. Fixed to fail closed, now has its first test.
- **The `quant_no_buy_signal` systemic flaw** — **fixed and confirmed** (commit `c138a77`).
  The filter is now scoped to non-option orders; no gate was touched. Verified live on
  2026-08-20 at 10:12 ET: an AAPL cash-secured put previews `READY_FOR_APPROVAL` with quant
  reporting `NO_SIGNAL`, while a plain equity BUY on the same name is still blocked.
- **Queue 7 is resolved: it never filled, and has been cancelled and reconciled.** Checked
  read-only at 10:01 ET — status `new`, `filled_qty 0`, resting since 09:30:02 ET. Root cause
  confirmed with a live quote rather than inferred: the limit of 0.13 was the previous
  session's bid, and at 10:02 ET the contract quoted bid 0.07 / ask 0.17 / mid 0.12, so a
  sell-to-open at 0.13 sat above the bid and was never marketable. Exactly the failure
  `4a73e6b` already warns about, so no new fix was needed. Cancelled at 14:04:52Z with no
  partial fill, then reconciled — which exercised a path the CVX equity fill could not: a
  terminal **non**-fill. `lifecycle_status` mapped `canceled` to `BROKER_CANCELLED`,
  `portfolio_sync` came back `null`, and neither `paper_positions` nor `paper_fills` was
  written. Evidence: `docs/live-trade-evidence/canceled-CVX-option.json`.
- **Replacement order is staged as queue 8, and is waiting on the project owner.**
  `AAPL260826P00305000`, sell-to-open 1 contract, limit 0.69 at the live bid, 3.7% OTM, 6 DTE,
  premium 0.69/share. All three gates PASS (underlying SEC_EDGAR, structure cash_secured_put,
  account CASH). It has reached **no broker**. Submitting it requires the project owner to type
  `EXECUTE PAPER` personally — that confirmation does not carry over from queue 7.
  Note the 6 DTE expiry (2026-08-26) resolves before kickoff, leaving clean account state.

  **Run it from the `.worktrees/live-trade-backend` worktree, not from `master`:**

  ```powershell
  cd .worktrees\live-trade-backend
  ..\..\.venv\Scripts\python.exe backend\execute_paper_order.py 8
  ```

  `backend/*.db` is gitignored, so **every worktree has its own separate database**, and
  merging the branch did not move any of this state. Queue 8 exists only in the
  live-trade-backend worktree's `paper_trading.db`, alongside the queue-5 CVX position and the
  queue-7 record. In `master`'s own database, id 8 is an unrelated `0001` REJECT left over from
  the test suite — executing there would act on the wrong row. **Do not delete the
  `live-trade-backend` worktree** just because the branch is merged; the only copy of the live
  trade ledger is in it. This is the same worktree-local-database trap CLAUDE.md's
  known-limitation 4 already flags for the deployed-instance demo trade.

  **Re-price it before executing.** The 0.69 limit was the bid at 10:13 ET; by 10:20 ET the
  bid had already drifted to 0.68, which leaves the limit a cent *above* the bid and therefore
  non-marketable — queue 7's exact failure in miniature, just on a seven-minute timescale
  instead of overnight. An option bid moves continuously, so treat any staged limit as
  perishable. Re-run the preview and approval immediately before executing so the limit is the
  live bid at that moment:

  ```powershell
  ..\..\.venv\Scripts\python.exe backend\check_paper_order.py AAPL --option cash_secured_put --contracts 1
  ..\..\.venv\Scripts\python.exe backendpprove_paper_order.py AAPL
  ..\..\.venv\Scripts\python.exe backend\execute_paper_order.py <new queue id>
  ```

  That produces a fresh queue id; execute that one and leave queue 8 unexecuted, exactly as
  queue 6 was left. Market closes 16:00 ET / 20:00Z.
- CVX had **no eligible contract at all** on 2026-08-20 — 46 rejected as outside the 2–7% OTM
  band — so the replacement moved to AAPL. The band was deliberately **not** widened to
  manufacture a trade.
- `feature/live-trade-backend` is **merged into `master`** (merge commit `41d694d`), after the
  full local suite passed 40/40 on the merged result. Ruff is clean on every file the branch
  touched; the pre-existing `E402`/`F401` findings in `test_local_api_smoke.py` and
  `test_moomoo.py` were left alone per CLAUDE.md.
- `provision_cash_account.py` re-verified for kickoff: dry run against `0TCX` reports
  `max_margin_multiplier=1 no_shorting=True`, `type=CASH`, "nothing to change". The script is
  dry-run by default (`--apply` required to write) and one-directional. Its test covers the
  kickoff case directly — a fresh 4x account produces the right patch, a rerun is a no-op, and
  an absent/null/unparseable multiplier fails closed rather than silently leaving 4x at 4x.

**Terminal 3 (research) — done, one optional low-priority item left:**
- `compliance-logic.md`, `social-posts-draft.md`, `citation-strengthening-memo.md`: all written,
  committed on `research/hackathon-notes` (commit `b76543d`), and verified — both quoted passages
  checked against primary sources (one confirmed accurate, one corrected to real wording), weak
  Scribd/Course Hero citations swapped for better ones. Confirmed `margin-account-policy.md` and
  `submission-copy-draft.md` already state the options position as unreviewed.
- Follow-up task assigned (update `demo-video-script-draft.md` and `submission-copy-draft.md`
  with the real CVX fill data and, if it exists by then, the option-order result) — **status
  unconfirmed, not yet reported back as of this writing.**
- Optional, low-priority, only if time allows: apply the citation upgrades identified in
  `citation-strengthening-memo.md`'s checklist to `fiqh-primary-sources.md` itself (the memo only
  identifies opportunities, doesn't apply them).

**Immediate next steps for whoever resumes:**
1. **Queue 7 is done** (cancelled unfilled, reconciled, documented). The open item is now
   queue 8: the project owner must type `EXECUTE PAPER` for
   `backend\execute_paper_order.py 8` before the 2026-08-26 expiry — ideally the same day it
   was priced, since its limit is a live-quote bid that goes stale overnight exactly as queue
   7's did. If it is no longer the same session, re-run the preview rather than executing a
   stale queue 8.
2. Get the Reserved VM decision from the project owner and act on it.
3. Chase the two unconfirmed reports above (Terminal 2's quant-signal fix, Terminal 3's asset
   updates).
4. Everything else in the task lists below should be re-verified against current branch state
   before assuming it's still accurate — this snapshot will go stale fast.

### 2026-08-22 update — supersedes the 2026-08-20 snapshot above where they conflict

- **SSH into the VPS is resolved.** User is `amanah`, key `~/.ssh/amanahtrader_vps`, confirmed
  working directly: `ssh -i ~/.ssh/amanahtrader_vps amanah@159.65.220.83`. Root is intentionally
  locked at the OS account level (a password reset does not enable it — that's expected, not a
  problem to fix). This was Terminal 2's item-1 blocker; item 1.1's host audit can proceed now.
  Routine `apt update && apt upgrade` (not `dist-upgrade`, no reboot) was run manually by the
  project owner directly on the box — if a kernel update / restart-required flag showed up, it
  was left pending rather than rebooted, so check for that before assuming it's fully current.
- **Terminal 1's dashboard redesign is committed, not merged.** `3b220e9` on `dashboard-redesign`
  (fast-forwarded onto master first, so it includes everything through `86333f6`): ink-navy/brass
  theme, sidebar nav, serif/mono/sans type system, and a new officer-desk component (Quant
  Manager / Shariah Compliance Officer / Risk Manager / Trader) — full desk on Agent Summary and
  replayed in Execution Audit, compact pill on Investment Committee, nothing added to
  Ticket/Orders/Portfolio. Verified live via Playwright against a real `local_api` instance (not
  mocked), light mode checked too. Every pre-existing element id/class was independently diffed
  and confirmed intact. Not yet merged to master — pending the project owner's sequencing call
  against whatever Terminal 2 lands next.
- **Two submission-stage docs exist and are committed correctly now, not floating on master.**
  `SOCIAL_POSTING_CADENCE.md` and `SUBMISSION_PACKAGE_ASSEMBLY.md` were originally written
  untracked directly on `master` (outside the assigned worktree, at real risk of being lost to a
  `git clean`) and have since been moved and committed at `fcdd473` on `research/hackathon-notes`
  in `.worktrees/hackathon-research`. Per the cadence doc: Post 1's window was Aug 20-22
  (closing/closed), Post 2 (methodology) opened Aug 22 — both are drafts only, actually posting
  is a project-owner action no terminal can do.
- **Terminal 2's items 2-4 (screening audit log, provisioning kickoff check, risk-adjusted
  return metrics) and the CORS fix: status not yet reported back as of this writing.** Re-verify
  against `feature/live-trade-backend`'s actual state before assuming any of it landed.
- **A "Usage discipline" section was added** (below, under Tooling) after a `/usage` review
  showed `superpowers:brainstorming` and long unscoped sessions as the two biggest cost drivers
  across terminals — read it if a session's usage is running high.

### 2026-08-26 update — supersedes prior snapshots above where they conflict

Written 2026-08-26, with kickoff (Aug 27–28) 1–2 days out. Terminal 1 and Terminal 3 have been
paused since roughly 2026-08-22 with no confirmed reports since; treat everything under their
sections below as **unverified**, not done, until they actually report back.

- **Quant engine work landed and is verified stable in production**, not by any terminal in this
  split but by the coordinating session directly: S001 narration diagnostics, the S002
  pullback-in-uptrend strategy, and a sector-concentration risk cap (fourth risk dimension,
  15% limit), merged at `3e62183` on 2026-08-24 and confirmed working live on `amanahtrader.uk`
  on 2026-08-25 (AAPL test order cleared quant → Shariah → risk gates; sector exposure read
  0.31% vs. the 15% cap). The `/news` timeout/bounding fix (`d39ba21`) is also confirmed stable
  in prod. Full detail in memory `production_verification_2026-08-25` — don't re-run that sweep
  absent new commits or a fresh bug report.
- **The council's Aug 23 resilience roadmap is still unbuilt.** `council_output_round3_synthesis.md`
  recommended, for the pre-kickoff window: automated SQLite backup + restore drill, a
  provision_cash_account.py pre-flight/cutover script for the kickoff account switch, and an
  audit-export CLI bundling ledger + Shariah verdicts + EDGAR evidence for judges/scholars. As of
  this writing none of the three exist in `backend/` — this is now Terminal 2's assignment,
  handed off 2026-08-26 in a fresh worktree (`.worktrees/prod-resilience`, branch
  `feature/prod-resilience`; the old `live-trade-backend` worktree is left alone — its `.db` is
  the only copy of the live-trade ledger). Explicit don'ts carried over from the council consult:
  no time-varying re-screening, no changes to `check_us_symbol` or `sec_edgar_cache.py`, no
  gate-chain or execution-path changes — this is defensive tooling around the proven system.
- **Pre-event disclosure timeline added and committed.** `PRE_EVENT_TIMELINE.md` (commit
  `7a45d36`), drafted proactively per Discord guidance from lablab.ai on pre-event repo work.
  Whether disclosure is actually required by the organizers is still an **open question** as of
  2026-08-25 — see the file's own "Open question" section.
- **Still open, unresolved, carried forward from the 2026-08-20/22 snapshots — re-verify before
  acting on any of it:** Terminal 1's Reserved VM decision (SQLite persistence across Replit
  restarts — may be moot now that hosting moved to the VPS per Phase 3 of the disclosure
  timeline, but confirm rather than assume); the `dashboard-redesign` branch's merge status
  (`3b220e9`, committed but not merged as of Aug 22); and Terminal 3's two follow-up items
  (demo-video-script/submission-copy update with real fill data, optional citation-upgrade pass).

### 2026-08-26 (later same day) — resilience roadmap done and deployed

The three-item resilience roadmap above is **built, tested, merged (`master` at `01e9090`), pushed
to origin, and live on the VPS** — not a plan anymore. `backend/backup_database.py` /
`restore_database.py` (hourly cron, checksummed manifests, proven restore drill),
`cutover_preflight.py` (read-only go/no-go, smoke-tested against the real `0TCX` account), and
`audit_export.py` (ledger + Shariah verdicts + EDGAR ratio evidence, plus a Pending Approvals
section per the human-in-the-loop-deadlock risk below). Full detail and reasoning in
`council_output_backend_next_priorities.md`. Two follow-on items also shipped: the watchlist scan
is now cron-refreshed every 30 minutes (was the 25.5h-stale item flagged above — resolved, not
just noted), and `audit_export.py` surfaces any order stuck awaiting `EXECUTE PAPER` so an
unattended judging window doesn't read as the system being broken.

**Deliberately not built, and not an oversight:** off-box backup replication. Reasoned through
explicitly (see the council doc) — it protects a low-probability tail risk (total droplet loss)
for a roughly one-week window, at the cost of a new paid external service under time pressure, and
the one thing that would actually matter if the droplet were lost (the two live trade fills) is
already independently safe in `docs/live-trade-evidence/` via git. Also not touched: GET-endpoint
authentication — checking the actual nginx vhost showed this was already a deliberate design
decision (`# Writes... need the operator key; reads never do` — judges need public reads for the
live demo), not a gap either the council or this session's first pass correctly weighed.

**Still blocked, not skipped:** the cutover rehearsal needs the dedicated hackathon Alpaca account
to exist first (not created as of this writing); a real covered-call fill needs the project owner's
explicit go-ahead and, ideally, to happen after cutover so the demo-worthy fill lives in the
account judges actually see. Both are queued, neither is something a backend session should do
unprompted.

## Why this split, and why this sequencing

The key fact driving the current sequencing: judging requires a **brand-new dedicated Alpaca
paper account** created at/around kickoff (Aug 27–28) — the current test account (`0TCX`) is
disqualifying if reused for judging. That means nothing that requires the *real* competition
account can happen yet, but everything else — proving the live option order, proving the hosting
deploy path, drafting compliance/outreach/social content — can and should happen now, in
parallel, against the test account or as standalone documents, then get re-pointed/re-run against
the competition account once it exists. See `council_output_round3_synthesis.md` for the full
reasoning and the two AI models' agreement on this point.

## Converged plan (2026-08-20 → Aug 26, on the test account `0TCX`)

1. **Terminal 2**: get one live option order (covered call or cash-secured put) through the full
   real chain, on a high-liquidity underlying, confirming an actual `filled` status — this is the
   single hardest remaining technical unknown and needs the most debug runway. Treat it strictly
   as a debugging proxy for the competition-account run, not the real thing — labeled test mode,
   documented stop/pause criteria if it reveals a systemic flaw.
2. **Terminal 1**: stand up the Replit deployment against the test account, purely to prove
   deploy mechanics (auth, static dashboard serving, SQLite persistence across a restart) —
   decoupled from which Alpaca account is wired to it.
3. **Terminal 3**: draft the "Compliance Logic" doc (economic-equivalence argument for why a
   covered call / cash-secured put isn't Gharar/Maysir) now, not contingent on a scholar
   replying; draft scholar-outreach material and build-in-public social post copy; keep
   strengthening fiqh citations toward primary sources.
4. **Terminal 2** (secondary): minimal flat-file/TTL EDGAR cache, explicitly scoped as
   *temporary* — sized only to survive SEC rate limits during repeated demo-recording takes, not
   the full SQLite screening-store design from `NEXT_STEPS.md`. That fuller design is deferred
   past submission.

**Aug 27–28 (kickoff):** create the competition account, rerun `provision_cash_account.py`
against it, re-point the already-proven Replit deployment at the new credentials.

**Aug 28 → Sep 4:** re-run the now-debugged equity and option trades against the competition
account through the deployed instance (a local-only trade is invisible to any deployment), record
the demo video only once a real option fill exists, finalize slides/social posts, submit.

## Terminal 1 (Sonnet) — Frontend / demo

Worktree: `.worktrees/shariah-trace-ui`, branch `feature/shariah-trace-ui`.

**Already done, do not redo:** the stale Moomoo-era AAPL position is cleared (backup at
`backend/paper_trading.db.bak-stale-aapl-cleanup`); `GET /stock/{symbol}/explain` shipped from
Terminal 2. Verify the Shariah Trace panel actually renders from that real endpoint rather than
the client-side fallback it was originally built against (`council_output_post_merge.md` flagged
a shape mismatch there) — fix it if it's still on the fallback.

**Findings from the first pass (2026-08-20, confirmed live):** the Replit deployment already
existed (`https://alpaca-hackhaton-ai-finance-syariah--adibluqman117.replit.app`, connected to
this GitHub repo) — no need to stand up a new one. A real bug was found and fixed: the "Run
Agents" button silently no-opped on Equity orders because a hidden `#optionStrike` input
(`min="0.01"`, default `0.00`) blocked HTML5 form submission even though it wasn't visible; fixed
by disabling the field when hidden (`dashboard/index.html`, +6 lines), verified against the live
deployed API, pushed to `feature/shariah-trace-ui` but not yet merged. The Shariah Trace panel
was confirmed working from the real `/explain` endpoint (not the fallback — that concern is
resolved) and fails closed correctly. Two real risks surfaced, addressed below. `ALPACA_API_KEY_ID`/
`ALPACA_SECRET_KEY` are not yet set as Replit Secrets — the project owner adds those directly,
not any terminal.

Owns now (2026-08-20), next pass:
1. **Merge `feature/shariah-trace-ui` into `master` and redeploy.** Run the local test suite
   first per the merge-checkpoint convention below, even though this diff is frontend-only.
2. **SQLite does not persist across a Replit restart** — confirmed directly (wrote a marker,
   paused/resumed, marker gone). This matters more than "time the trade close to the deadline"
   sounds like it fixes: the final trade was already going to land Aug 28→Sep 4, but a judge may
   open the live URL hours or days after that, and if the free-tier container has gone idle and
   restarted in between, the position silently vanishes — a worse failure than almost anything
   else on the list, since "live demo URL" is a hard submission requirement. Before accepting
   that as a timing risk to manage, spend ~15 minutes checking whether Replit offers persistent
   storage short of a full Reserved VM (many platforms do, even on lighter tiers) — given the
   hackathon's prize pool, a modest paid tier is very likely worth it if that's what's needed.
   Report back cost/feasibility rather than deciding unilaterally, since it may cost money. If a
   persistence fix is available and cheap, add it. Either way, add a simple keep-alive ping
   (a scheduled request to the live URL every few minutes during the final days) as cheap
   defense-in-depth against idle-based sleep, independent of whether the storage question
   resolves cleanly. Do **not** migrate off SQLite to an external database (e.g. Postgres) to
   solve this — that's backend code Terminal 2 owns, and real engineering risk this close to the
   deadline for a problem that almost certainly has a cheaper infra-level fix.
3. **SEC EDGAR returns `http_403` on this deployment specifically** (works fine locally, per
   `NEXT_STEPS.md`). Checked: `sec_edgar_screen.py` already sets a descriptive default
   `User-Agent`, so this is not a header problem — almost certainly SEC blocking Replit's
   shared/rotating egress IP range. This is queued as a known issue for Terminal 2 (it owns
   `sec_edgar_screen.py`), not blocking Terminal 1's work — flag it, don't fix it here.
- Once redeployed, load the live Replit URL itself and click through preview → the Shariah Trace
  panel, not just localhost — a mismatch that only shows up on the hosted build is worse to find
  on Sep 3 than now.

Does **not** touch: `backend/local_api.py`, any gate module, or `option_strategy.py`.

## Terminal 2 (Opus) — Backend

Worktree: `.worktrees/live-trade-backend`, branch `feature/live-trade-backend`.

**Already done, do not redo:** the margin blocker is resolved (`provision_cash_account.py`
applied, `account_shariah_gate` verified passing on the CASH-equivalent posture); one real equity
trade (CVX) ran end to end against the real Alpaca paper API; `GET /stock/{symbol}/explain` and
`GET /stock/{symbol}/option-strategy` both shipped; the `us_strategy.py` duplicate `zoya_compliance`
path is removed (`test_single_screening_path.py` enforces this with a static AST check now).

Owns now (2026-08-20), in this order:
1. **Get one real Level 1 option order (covered call or cash-secured put) through the full live
   chain** — preview → approve → `EXECUTE PAPER` → fill → reconcile — against the test account
   (`0TCX`), on a high-liquidity underlying (e.g. SPY, AAPL). Confirm an actual `filled` status,
   not just a successful submission — Alpaca's paper options book can be thin and an unfilled
   order will look broken on camera later. This is the single hardest remaining technical
   unknown (option symbology, live-chain strike/liquidity selection, MCP quirks) and the item
   most likely to reveal a design problem, so it goes first. Treat it as a debugging proxy for
   the eventual competition-account run, not the real thing — keep it in a clearly labeled test
   mode, and write down explicit stop/pause criteria if it surfaces a systemic flaw.
2. **Minimal EDGAR cache — explicitly scoped as temporary.** A flat-file/TTL disk cache for
   `sec_edgar_screen` responses, sized only to survive SEC's own ~10 req/s guidance during
   repeated demo-recording takes — not the full append-only `shariah_screens` SQLite design
   from `NEXT_STEPS.md`. Label it in code/comments as a temporary shim to be replaced post-
   submission; do not let it grow into the full design under time pressure.
3. Have `provision_cash_account.py` ready and tested so it can be rerun immediately against the
   brand-new competition account at kickoff (Aug 27–28) — it is one-directional by design and
   already proven once; just confirm it still runs clean against a fresh account.

Does **not** touch: `dashboard/index.html`/JS, or anything under `hackathon/alpaca-2026/` docs.

## Terminal 3 (Haiku or Sonnet) — Research

Worktree: `.worktrees/hackathon-research`, branch `research/hackathon-notes`. Produces reference
documents only — never edits `backend/` or `dashboard/`.

**Already done, do not redo:** initial fiqh primary-source research
(`research/fiqh-primary-sources.md`), the Alpaca cash-account research that fed Terminal 2's now-
resolved margin blocker (`research/alpaca-cash-account.md`), and the submission-logistics doc
(`research/submission-logistics.md`) all exist. This round found: **no primary Islamic source
(not AAOIFI, not Usmani, not IIFA's 2019 resolution) specifically permits any of the four allowed
option structures** — the project rests on a genuine minority position. That finding is what
drives the new top task below.

Decisions from the 2026-08-20 planning discussion (answers to Terminal 3's own clarifying
questions before starting):

- **`compliance-logic.md` audience: adversarial, not sympathetic.** Write for a scholar/judge who
  currently believes options are haram and needs to be walked through why this specific
  implementation differs. Steelman the "options are Gharar/Maysir, full stop" objection first,
  then answer it — don't write for a reader already inclined to agree. Structure: a short
  plain-language summary up front (~300–500 words, judge-skimmable), then the steelmanned
  objection, then the two structure-specific arguments with primary sourcing (AAOIFI, IIFA
  resolutions, Sami Al-Suwailem, classical contract law — Khayar al-Shart and also check **Wa'd**,
  the unilateral-promise framing the project's original pitch leaned on), then an explicit "what
  this does not claim" limitations section, then full citations.
- **Scholar outreach — not Terminal 3's task.** The project owner has a personal contact (an
  ustaz) and will reach out directly, outside these terminals, if time allows. **Do not draft a
  scholar-outreach-draft.md or identify institutional contacts.** Instead, make sure
  `docs/shariah-policy/margin-account-policy.md` and the submission copy plainly state the
  options-structure position is **unreviewed** if no review happens in time — see
  `NEXT_STEPS.md` next-step #4, which already allows this as a valid outcome. Don't let silence
  on this read as an oversight; state it.
- **Social posts — split into two, don't combine the hook and the fiqh-risk disclosure.** Post 1
  (now): the real CVX fill as a pure technical-achievement hook ("first Shariah-compliant trade
  via MCP, no margin, no interest, full SEC-level screening — options are next") — no
  minority-position caveat, since options haven't traded live yet and a caveat about a risk that
  doesn't apply to what's being celebrated just muddies it. Post 2 (later, once
  `compliance-logic.md` exists and ideally after the live option trade lands): a dedicated post on
  the methodology itself, transparently framed as an explored minority position — this is the
  differentiator content and deserves its own spotlight, not a buried disclaimer in the
  celebration post.

Owns now (2026-08-20):
1. **`compliance-logic.md`** — per the audience/structure decision above.
2. **`social-posts-draft.md`** — both posts, per the split decision above. Draft only; a human
   posts it, since social engagement scoring rewards posting *early*, not late.
3. Keep strengthening fiqh citations from secondary summaries toward primary sources in parallel,
   as time allows — the original task, now secondary to the compliance-logic doc.

Output location: new files under `hackathon/alpaca-2026/research/` (e.g.
`compliance-logic.md`, `social-posts-draft.md`) — do not edit `SHARIAH_GATE_NOTES.md` directly;
Terminal 2 or a later session incorporates findings into it after review.

## Handoff contract (the one place terminals meet)

**Historical — the endpoint below already shipped**, kept for the contract-first convention it
demonstrates. The new Terminal 1/Terminal 2 interface for 2026-08-20 is the Replit deployment
itself: Terminal 1 owns getting it live, Terminal 2's job is only to make sure `local_api.py`
still reads config the same way in that environment (no code changes expected, but don't
assume — if Terminal 1 reports a deploy-only failure, it's diagnosed together, not silently
patched around on either side).

Terminal 1 and Terminal 2 meet at exactly one interface: the `GET /stock/{symbol}/explain`
response shape (verdict, rule fired, fiqh basis, citation) that Terminal 2 builds and Terminal 1
renders. Before Terminal 1 builds the panel against a guessed shape, agree the JSON contract
first — even a one-message exchange of the intended field names avoids a rebuild. Once that
contract is fixed, both terminals can build independently.

**Known collision risk:** if the `/explain` endpoint needs `local_api.py` route wiring (it
will), that file stays Terminal 2's exclusively — Terminal 1 never edits it.

Terminal 3 has no collision risk with either — it only writes new files under
`hackathon/alpaca-2026/research/`.

## Tooling — plugins and MCP servers to actually use

Added 2026-08-20. Plugins are installed on this machine but went almost entirely unused across
every terminal this session — everything got done through raw Bash/git/manual verification
instead. Some of that manual work is exactly what a plugin exists to do better. This section is
concrete about which plugin, for which situation, so "I could look that up" turns into actually
looking it up.

**Terminal 1 (frontend):**
- **`playwright` MCP — use this on the live Replit URL specifically, not just localhost.** The
  2026-08-20 task is proving the *hosted* deploy works, so the thing worth screenshotting and
  console-checking is the actual `*.replit.app`/`*.replit.dev` URL after a deploy, ideally after
  forcing a container restart to check SQLite persistence — not the dev server. Multiple reports
  last session said "tested in a real browser" without independent verification; don't repeat
  that, show what it actually rendered.
- **`frontend-design` skill** — load it before any real visual/UX decision on the dashboard (the
  trace panel, the portfolio charts), not just for layout code.
- **`astral` (ruff)** — applies automatically via the PostToolUse hook now; no action needed, but
  `/ruff` is available for an explicit full-file pass if wanted.

**Terminal 2 (backend):**
- **`context7` MCP — use this before trusting memory on an API's behavior, especially now.** The
  live option order is exactly the kind of task that produced the quant-agent and margin-account
  bugs last session (assuming API behavior instead of checking). Before writing code against
  Alpaca's option endpoints, OCC symbology, or the `alpaca_mcp` tool surface, pull current docs
  with `context7` rather than relying on training-data recall — option symbol construction is a
  likely place to get something subtly wrong.
- **`superpowers:systematic-debugging`** — load it explicitly at the start of the option-order
  work, not partway through after something fails. This task is expected to need real debugging
  (liquidity, fills, MCP quirks); use the skill deliberately rather than arriving at it by
  accident.
- **`superpowers:test-driven-development`** — matches this repo's own convention (write the test,
  mutation-check it, then implement) already documented in `CLAUDE.md`; applies to the EDGAR
  cache work too.
- **`pyright-lsp` + `astral` (ruff)** — automatic now; a red squiggle or hook-caught lint issue is
  worth reading, not clicking past.
- **`security-guidance` skill** — load it before touching anything credential- or
  account-provisioning-adjacent (`provision_cash_account.py`-style scripts, `.env` handling,
  re-running the provisioning script against the future competition account).

**Terminal 3 (research):** `WebFetch`/`WebSearch` for both the compliance-logic doc (finding
primary AAOIFI/IIFA/SAC text to ground the economic-equivalence argument, not just paraphrase it)
and for identifying realistically reachable scholar-outreach contacts. `context7` doesn't apply
(no code library docs involved in fiqh research).

**Manager / coordinator (next session in this role):**
- **`code-review` skill** — use it for structured review of what a terminal reports, instead of
  ad hoc manual diff-reading every time. This session's manual verification pattern (independently
  re-running claims against the real broker, re-checking citations) was the right instinct; the
  skill formalizes it and catches things a manual pass might miss.
- **`security-guidance` skill** — load it specifically before signing off on anything touching the
  Alpaca account, credentials, or broker-facing code.
- **`superpowers:verification-before-completion`** — matches the standing rule already in effect
  this session (verify before accepting any terminal's claim as done); load it explicitly rather
  than reinventing the same discipline each time.

## Usage discipline — keep every terminal cheap

Added 2026-08-22, after a `/usage` review on the coordinator session showed
`superpowers:brainstorming` (plus the subagents it spawned) accounting for roughly half of one
session's cost, and 59% of that session's spend coming from turns run above 150k tokens of
context. None of this means work less carefully — it means spend the same care with fewer
wasted tokens. Check `/usage` before starting a long task if the session bar is already high;
the 5-hour session window is the tighter, faster-moving constraint most of the time, the weekly
one usually has more headroom.

**All terminals:**
- Reserve `superpowers:brainstorming` for decisions with real ambiguity — new structure, unclear
  scope, a genuine design fork. For a bounded change with an obvious approach, state the plan in
  2-3 sentences and get a yes; don't run the full skill ceremony for something describable in one
  message.
- Don't spawn a subagent/fork for anything 1-2 direct tool calls (Read/Grep/Bash) can answer.
  Reserve forking for genuinely large, independent research that would otherwise flood the main
  session with tool output not worth keeping.
- Keep a session scoped to one topic. `/clear` when moving to a new, unrelated task rather than
  letting one session accumulate several in a row — long context is disproportionately expensive
  even with prompt caching.
- `/compact` mid-task on a long single-topic session to keep the *next* turns cheap. It does not
  refund tokens already spent, so it's a forward-looking move, not a way to lower what a usage
  meter already shows — don't compact expecting the bar to drop.
- Don't cut real verification to save usage. Browser/Playwright checks on UI changes, re-running
  the test suite, independently confirming another terminal's claim before accepting it, `context7`
  lookups before trusting API behavior — these are the legitimate cost of this project's own
  standards (CLAUDE.md's testing rule, the verify-before-accepting coordination pattern that has
  already caught real problems this week). The waste to cut is ceremony and redundant exploration,
  not verification.

**Per role:**
- **Terminal 1 (frontend):** the required Playwright browser verification on real UI changes is
  legitimate spend — keep it. Don't re-run `frontend-design`/`figma-design-to-code` on a decision
  that's already locked; only invoke them for a genuinely new open design question.
- **Terminal 2 (backend):** `context7` lookups before trusting an API's behavior are legitimate
  spend — this is literally how the quant-agent and margin-account bugs were caught. Don't cut
  those.
- **Terminal 3 (research):** keep `WebFetch`/`WebSearch` scoped to the specific claim being
  checked rather than broad exploratory searches.
- **Manager/coordinator:** independent verification (`git status`/`diff`, re-reading a file) before
  accepting a terminal's report is cheap and should stay. Re-running a full brainstorming pass on
  a decision a worker terminal will independently re-derive anyway when it actually builds the
  thing is the real waste to cut — coordinate and decide, don't re-explore what another terminal
  is already exploring.

## Merge checkpoint

Whichever terminal finishes a milestone first merges into `master` (or a shared integration
branch if more overlap emerges than expected). Before every merge: run the full local test suite
(18 suites, see `CLAUDE.md`) and review whatever landed in `backend/local_api.py`, since that is
the one file with any real concurrency risk. The other terminals rebase on top rather than
continuing to diverge.
