# Council consult: what should backend-only work spend the pre-kickoff runway on

Date: 2026-08-23. Participants: ChatGPT (openai/gpt-5-nano), Gemini (google/gemini-3-flash-preview),
Claude (coordinating AI on this project, third participant). Run via `llm-council-skill`
(`scripts/query_llms.py`), single round — both models converged closely enough that a second round
wasn't needed.

Context this was consulted on: the project owner decided that for the remaining ~4-5 days before
kickoff (Aug 27-28), only Terminal 2 (backend) keeps working — Terminal 1 (frontend/demo) and
Terminal 3 (research/compliance) are paused. The real submission demo trade can't be re-run until
the dedicated competition Alpaca account exists at kickoff itself, so the question was whether
there's genuinely valuable backend work to fill the window or whether backend should mostly hold
state stable.

Full prompt sent covered: everything verified DONE (equity + option live fills, unified screening
path, EDGAR cache shim, screening verdict log, risk-adjusted return metrics, VPS hardening fully
applied and verified with before/after probes, Shariah vault wiring fix) and everything NOT yet
done per `NEXT_STEPS.md` (kickoff-gated demo re-run, optional covered call, scholar review of the
margin-account policy, primary-source fiqh citations, no DB backup, no monitoring, the deferred
time-varying screening store).

## Round 1 — independent opinions

**ChatGPT:** Yes, real work exists. Three-item plan: (1) harden/protect the production surface —
SQLite backup+restore with a verification/restore-drill step, lightweight monitoring/alerting on
the write routes, confirm operator-key enforcement has a regression test; (2) build a self-contained
"demo re-run scaffold" — a mockable, seeded runner that exercises preview→approval→execute→reconcile
without touching the real broker, gated behind an explicit flag, so the pipeline is demonstrable
independent of the competition account; (3) polish the compliance narrative — upgrade fiqh citations
toward primary sources and add a judge-friendly appendix. Explicitly: no new live trades, no
time-varying screening store, no new test framework.

**Gemini:** Same top priority — automated SQLite backups (systemd timer, `sqlite3 .backup`, streamed
off-box, verified with an actual restore drill) — called it the highest technical risk since a VPS
failure would erase demo history with no recovery path. Second: a "scholar-ready" audit-export CLI
that aggregates the ledger, screening verdicts, and EDGAR cache evidence into one timestamped
report — reasoning that if a judge or scholar questions a specific fill or verdict, the project
should hand them a generated audit trail, not a raw database. Third: turn the manual kickoff
sequence in `VPS_RUNBOOK.md` into a tested pre-flight/transition script (verify paper account, verify
CASH/no-margin, archive old ledger data, swap `.env`, restart) so the actual kickoff transition is
scripted rather than improvised under time pressure. Fourth, lower priority: a mock-provider
regression test for the covered-call path (never a real trade) to de-risk that code without spending
paper equity. Explicit do-not-touch list: the "traffic light" re-screening design, the working
`check_us_symbol`/EDGAR cache logic, any database/framework change.

Both models independently converged on backup/restore as the #1 priority and agreed the demo-trade
re-run itself must wait for kickoff.

## Claude's opinion (synthesis, introduced after round 1)

Both models are right that backup/restore is the correctly-sequenced first move — it's cheap,
protects against the single most catastrophic failure mode (losing the whole demo trade history to
a droplet incident with `docs/deployment/VPS_RUNBOOK.md` already flagging "no backup" as an open
gap), and needs no new account or design decision to start today.

I'd diverge from ChatGPT on item 2 (the mock-broker demo-runner scaffold): this project's own
`NEXT_STEPS.md`/`CLAUDE.md` history is unusually explicit that fixture-driven confidence has bitten
it before — the `quant_no_buy_signal` bug survived the test suite specifically *because* the fixture
path substituted a mocked signal and masked what the real quant agent said. Building a second,
mocked re-run path duplicates a chain that's already been proven twice against the real broker, and
adds a parallel code path to a codebase whose stated architecture principle is "one entry point,"
enforced by a static-AST test for exactly this failure shape on the screening side. I'd skip it.

Gemini's audit-export tool is the one genuinely new, valuable idea neither the original NEXT_STEPS
list nor I had already surfaced — it fits the project's own evidence pattern (`docs/live-trade-
evidence/*.json`, three-way-verified fills) and turns "trust the database" into "here's a generated,
reviewable packet," which is useful for a scholar, a judge, or the project owner alike. I'd rank it
second, right after backup/restore.

The kickoff pre-flight/transition script (Gemini's #3) is worth doing but is lower urgency than it
looks: `VPS_RUNBOOK.md` already has a written 5-step kickoff sequence, so this is "script an
already-correct manual procedure," valuable insurance against fumbling it live, not a gap in
understanding. I'd place it third, and treat it as optional rather than mandatory if time runs out —
a careful human following the runbook by hand is an acceptable fallback; a lost demo database is not.

Neither primary-source fiqh citations nor scholar outreach belongs to backend at all — those are
Terminal 3's domain (research/compliance), which is explicitly paused per the project owner's
decision. Backend picking that up would be working outside its lane, not filling idle time
productively.

## Synthesis — where all three land

1. **SQLite backup/restore for the VPS, with a real restore drill.** Full agreement, highest
   priority, cheapest, closes the single worst-case failure mode.
2. **A compliance/audit export tool** aggregating the ledger + `shariah_screens` verdicts +
   evidence into one generated report. Gemini's idea, not previously on the project's own list;
   directly useful for scholar review and judge scrutiny alike, and matches the project's existing
   "prove it three ways" ethos rather than adding a new one.
3. **Script the already-documented kickoff transition** (pre-flight checks on the new account +
   `.env` swap + restart), matching `VPS_RUNBOOK.md`'s manual sequence. Do this if 1 and 2 land with
   room to spare; a human can execute the manual runbook directly if time is short.
4. Optional, only if time remains: a mock-provider regression test for the untested covered-call
   path — code-only, spends no paper equity, does not touch the live screening/EDGAR logic.

Explicitly out of scope for this window, by convergent agreement across all three: the deferred
time-varying screening store, any change to the now-verified `check_us_symbol`/EDGAR cache logic,
a parallel mocked demo-runner path, opening a real 100-share position to force a live covered call,
and any fiqh/citation/scholar-outreach work (belongs to the paused research terminal, not backend).
