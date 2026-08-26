# Council consult — backend-only priorities for the pre-kickoff window (2026-08-26)

Follow-up to `council_output_round3_synthesis.md` (2026-08-20, the "resilience over new features"
call) and its execution: the backup/restore drill, `cutover_preflight.py`, and `audit_export.py`
all landed and were deployed to the VPS in this session (merged `master` at `6e0f877`, cron
installed, tested against the real production database). This round asked both models what
backend-only work should come next, given kickoff is 1-2 days out and frontend/research
work-streams are paused. Raw output via `llm-council-skill/llm-council/scripts/query_llms.py`
over OpenRouter (`openai/gpt-5-nano`, `google/gemini-3-flash-preview`). Both returned real
answers (10,924 and 4,804 chars respectively). Full prompt and raw responses in
`council_result_backend_next.json`-shaped output, reproduced in full below rather than in a
separate JSON file.

## Prompt sent

The full project-state summary sent to both models covered: the gate chain and two live fills on
the test account; the quant engine (S001/S002 + sector concentration) verified stable in prod
2026-08-25; VPS hardening from 2026-08-22 (fail2ban, security headers, key-gated POST endpoints);
the just-completed resilience trio; the accepted limitations (SIC-code/XBRL approximation, option
fills not tracked as positions, no live covered-call fill yet, same-disk backups, no GET auth);
and the still-open items (no dedicated hackathon account yet, stale watchlist scan, paused
frontend/research streams, an open disclosure-requirement question). It asked for a concrete
prioritized list of backend-only work for the remaining 1-2 days and into judging, and to flag any
underweighted risk.

## ChatGPT (openai/gpt-5-nano)

Prioritized: (1) close the GET-endpoint auth gap — sensitive reads (ledger, verdicts, EDGAR
evidence) are currently open to anyone who can hit the URL, a material risk during an unattended
judging window; (2) build a repeatable, logged, idempotent cutover protocol with a dry-run mode
and automatic rollback, so the actual account swap isn't improvised under time pressure; (3) push
the existing same-disk backup toward real off-box replication (rsync/scp to a second host or
object storage, with its own restore drill); (4) *cautiously* attempt a real covered-call fill,
but explicitly gated — "do not enable live option activity unless you have explicit safeguards and
a human in the loop"; (5) refresh the stale watchlist scan on a fixed cadence with a health check;
(6) consolidate everything into a judge-facing runbook with monitoring/alerting. Explicitly warned
against enabling any *new* live risk during the window without a signed-off, auditable protocol.

## Gemini (google/gemini-3-flash-preview)

Ranked by risk/impact: (A) create the dedicated account now and run the cutover script — "you
cannot judge a system that isn't connected to its final environment" — and, once live, *actively
force* a real covered-call fill (buy 100 shares of a liquid ticker, write the call) because a
judge inspecting the audit log will otherwise find only a put and the covered-call gate stays
"theoretical/unverified"; (B) automate the watchlist scan — a system with a 48h-stale watchlist
"looks dead" to a judge, and the quant engine has no fresh inputs to hand the approval gate
without it; (C) spend the ~30 minutes to rsync/scp backups off-box; (D) key-gate the GET
endpoints, framed partly as a compliance concern ("Amanah" / trust-and-stewardship) as well as a
technical one. Two additional risks raised that were not in the prompt: SEC EDGAR
rate-limiting/format-drift during judging (suggested a verdict result cache — see note below,
since this already partially exists), and a "human-in-the-loop deadlock" — if judging happens
while the operator is asleep, a system that requires someone to type `EXECUTE PAPER` will simply
never trade in front of a judge, so the audit export / pending-approvals view needs to make
"the system tried and is waiting for sign-off" visible on its own. Also recommended committing a
pre-event disclosure document immediately rather than waiting on an answer from organizers.

## Where they agree

Both models converge, independently, on the same four items: **close the GET-auth gap**, **get
backups off-box**, **actually run the cutover once the account exists (with a rehearsed
procedure, not an improvised one)**, and **refresh the stale watchlist scan**. Given two
independently-run models landed on the same four without prompting for a specific format, that
convergence itself is a useful signal.

## Where they disagree, and Claude's read

**The covered-call fill.** Gemini says force it now, as a demo-proof point. ChatGPT says only
attempt it with an explicit human-in-the-loop guard and a willingness to not do it at all. Claude's
own view, added here as the deciding factor: this is the one item on either list that means placing
a *new real order* against a live broker account, which is a materially different kind of action
than the other three (all defensive/read-only engineering). It should not happen automatically as
part of "backend work" — it needs the project owner's explicit go-ahead, ideally *after* the
cutover to the dedicated account (so the demo-worthy fill lives in the account judges actually
see), and only once the account genuinely holds 100 shares of the underlying. Recommend treating
it as priority 5, not priority 1: valuable, but gated behind a human decision and behind the
account existing at all, not something to build code for the way the other four items are.

**Gemini's disclosure-doc suggestion is stale.** `PRE_EVENT_TIMELINE.md` was already drafted and
committed (`7a45d36`) before this consult ran — Gemini wasn't told that explicitly in the prompt
(only that a document "was drafted"), so its "commit it now" advice is already satisfied. Worth
noting as a reminder that a model's recommendation is only as good as the state it was given.

**Gemini's EDGAR result-cache suggestion partially already exists.** `sec_edgar_cache.py` already
serves a 24h TTL cache of raw SEC responses (not verdicts) precisely to survive rate-limiting/
transient failures — this was in `CLAUDE.md`'s known limitations, which was in the prompt.
Gemini's suggestion is really "the existing cache doesn't cover a *cold* screen of a symbol never
seen before," which is a real residual gap, but the framing as a wholly new cache overstates what's
missing. Also: `CLAUDE.md`'s explicit constraint from the 2026-08-23 consult was "no changes to
`sec_edgar_cache.py`, no time-varying re-screening" — any fix here has to respect that boundary
(e.g., pre-warming the cache for the watchlist's symbol list ahead of judging, rather than changing
the cache's own logic).

**The human-in-the-loop deadlock is the most interesting new point.** Neither the original 2026-08-23
consult nor this session's own state summary raised it. It doesn't require weakening the
confirmation gate (that would violate `CLAUDE.md`'s explicit safety rules) — the fix is purely
about *visibility*: making sure a judge looking at the audit export or the dashboard can see a
`READY_FOR_APPROVAL` order sitting in the queue and understand that a human confirmation step is
the system working as designed, not the system being broken or asleep.

## Recommended backend-only priority order

1. **Key-gate the GET endpoints** the same way the mutating ones already are (or add a narrower
   read scope). Smallest, most mechanical change on the list, closes a real information-exposure
   gap, no broker interaction.
2. **Off-box backup replication.** Extends the just-shipped backup work with the one gap it
   explicitly left open; low effort (rsync/scp + a checksum-verified restore drill against the
   off-box copy), high value against the "unattended over the judging window" requirement.
3. **Pre-warm the watchlist scan cadence** so it's never >24h stale going into judging — this is
   what gives the quant engine (S001/S002) something to actually show a judge.
4. **Rehearse the cutover once the dedicated account exists** — not just run `cutover_preflight.py`
   once, but treat it as a drill: run it, read the report, decide, and have the exact
   `provision_cash_account.py --no-shorting --apply` command ready rather than improvised.
5. **Surface pending/awaiting-approval orders more clearly** in `audit_export.py` and/or a
   dashboard-adjacent endpoint, so the human-confirmation gate reads as "working as intended" to
   an unattended observer rather than "broken." (Backend-only: this is an API/export change, not a
   UI build.)
6. **The covered-call live fill** — hold for the project owner's explicit go-ahead, after the
   cutover, only once the account holds the needed 100 shares.

Not recommended for this window: anything touching `check_us_symbol`/`sec_edgar_cache.py`
internals (explicitly off-limits per the 2026-08-23 consult), re-litigating the disclosure
question with organizers (already committed, not backend work), or picking the frontend/research
streams back up (explicitly out of scope per this session's constraint).
