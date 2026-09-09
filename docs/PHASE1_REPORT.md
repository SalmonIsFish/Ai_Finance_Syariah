# Phase 1 Final Report — Security Hardening + Human Governance

Scope: eliminate client-supplied risk verdicts, implement the vault's weekly loss
limit, and build a human approval/activation workflow for SC Malaysia
publications. No frontend, LLM, Graphiti, Neo4j, autonomous agents, live
trading, broker integrations, quant-model redesign, or database replacement
was touched, per the phase's explicit scope.

## 1. Risk Security

**Prior vulnerability.** `/paper/approval` (`local_api.approve_paper_order`) trusted
the client-supplied preview payload for the risk verdict: it read
`preview["agent_summary"]["risk"]` (or `preview["risk"]`) verbatim and passed it
straight into `record_approval`. A client could submit a preview whose risk
block claimed `"status": "PASS"` regardless of actual position size, portfolio
exposure, daily/weekly loss, or order count — the server never recomputed
anything before approving.

**Fix.** `local_api.py` now defines
`authoritative_risk_verdict(connection, *, symbol, side, quantity, price, asset_class)`,
which:
- Recomputes projected position/exposure/sector risk via the existing
  `portfolio_risk_overlay()` (reused, not reimplemented) against the
  **current server-side portfolio state**.
- Recomputes `orders_today` as a real `COUNT(*)` against `approval_queue` for
  `APPROVED_PAPER_READY` rows since midnight UTC.
- Recomputes `daily_loss_pct` / `weekly_loss_pct` from `period_realized_pnl()`
  against server-side account equity — never from client input.
- Calls `agents.risk_engine.evaluate_risk()` with only server-derived numbers.

`approve_paper_order()` now calls both `authoritative_shariah_verdict()`
(Phase 0) and `authoritative_risk_verdict()` (Phase 1) and passes both into
`record_approval(..., verified_shariah=..., verified_risk=...)`.
`approval_queue.record_approval()` was extended with optional
`verified_shariah`/`verified_risk` params that, when supplied, override
anything in the client preview — existing callers that don't pass them are
unaffected.

**Regression tests** — `test_approval_risk_bypass.py`, 9/9 passing:

1. Forged risk PASS cannot override a genuine position-limit breach
2. Larger quantity at approval time is evaluated honestly (not the
   preview-time quantity)
3. Portfolio value/exposure is read from the server, never the client
4. Forged `daily_loss_pct`/`orders_today` in the payload are ignored (real
   seeded data used instead)
5. Switching symbols at approval is evaluated against that symbol's real
   exposure, not cached
6. Quantity alone cannot evade the exposure ceiling
7. Price is used consistently, not as a free variable to manipulate notional
8. Forged Shariah status is still independently gated (cross-check with the
   Phase 0 fix)
9. A genuinely valid order (real approved+activated SC publication, real
   risk state) still succeeds

Existing risk/approval/execution test suites (`test_risk_checks.py`,
`test_paper_execution_gates.py`, `test_approval_workflow.py`,
`test_execution_audit.py`) all still pass — no existing validation was
weakened.

## 2. Weekly Loss Limit

**Interpretation.** The vault's `risk-policy.md` specifies "Maximum weekly
realised loss: 2%" without specifying calendar-week vs rolling-7-day. I chose
**calendar week (Monday 00:00 UTC)** for consistency with the existing
daily/orders-per-day limits, which are already calendar-day-scoped
(`_start_of_today_utc`), and with the vault's calendar-anchored review
cadence. This is documented in `local_api._start_of_iso_week_utc()`'s
docstring.

**Implementation.** No new P&L source of truth was introduced.
`portfolio_store.period_realized_pnl(connection, *, since)` diffs the live
`SUM(realized_pnl)` over `paper_positions` against the most recent
`portfolio_value_snapshots` row at or before `since` (existing tables,
existing snapshot mechanism). `risk_checks.py` gained
`MAX_WEEKLY_LOSS_PCT = 2.0` and an optional `weekly_loss_pct` parameter on
`check_order()` — only applied to the `checks` dict when explicitly passed,
so no existing caller's behavior changed. `agents/risk_engine.evaluate_risk()`
and `config.Settings` (`MAX_WEEKLY_LOSS_PCT` env var, default 2.0) were
threaded through the same way. `screening_api.risk_limits()` now exposes
`max_weekly_loss_pct` alongside all other limits.

**Fail-closed behavior:** no snapshot exists before the period start →
`period_realized_pnl` returns `INSUFFICIENT_DATA` → `authoritative_risk_verdict`
treats this as `float("inf")` loss, which always exceeds any finite limit,
blocking the order rather than defaulting to 0.

**Regression tests** — `test_weekly_loss.py`, 11/11 passing: no trades (0%,
not unknown), profitable week (0%, not negative), loss below/exactly-at/above
2% (boundary inclusive), week boundary correctly excludes the prior week's
loss, multiple trades accumulate, a partial close realizes only its own
share, missing baseline data fails closed (not zero), omitting the parameter
entirely doesn't affect existing callers, default limit is exactly 2.0%.

## 3. Human Approval / Activation Workflow

**Workflow.** `sc_malaysia_store.py` gained: `approve_publication(reviewer=...)`
(records `approved_at`/`approved_by`; blocks a rejected publication),
`reject_publication(reason, reviewer)`, a rewritten
`activate_publication(activated_by=...)` enforcing all 10 required safety
checks, and a new `deactivate_publication(reason, deactivated_by)`. These are
exposed only via a new CLI, `backend/sc_admin_cli.py` — no HTTP mutation
route was added.

**Approval state recorded:** `approved_at`, `approved_by` (free-text reviewer
identifier), `human_review_notes`, plus the existing `publication_id`,
`source_document_hash`, `parser_version`, and the reconciliation counts.
**Activation state recorded (separately):** `activated_at`, `activated_by`,
distinct from approval.
**Deactivation:** non-destructive — sets
`deactivated_at`/`deactivation_reason`/`deactivated_by`; the publication row
and all its security rows remain queryable, never deleted.

**No fake authentication.** `reviewer`/`activated_by`/`deactivated_by` are
free-text audit fields, explicitly documented in `sc_admin_cli.py`'s module
docstring as **not** an identity/auth system — a Phase 1+ requirement before
any mutation is exposed over HTTP. All mutating CLI commands (`approve`,
`reject`, `activate`, `deactivate`) default to dry-run and require an
explicit `--apply` flag.

**Activation safety — all 10 conditions enforced, each with its own
machine-readable `reason`:** `publication_not_found`, `publication_rejected`,
`needs_reconciliation`, `publication_not_approved`,
`publication_already_superseded`, `no_security_records`,
`record_counts_do_not_reconcile`, `source_document_hash_missing`,
`parser_version_missing`, plus successful supersession of a prior active
publication (auditable via `deactivation_reason = "superseded_by:<new_id>"`).

**Multiple publications:** activating a second publication auto-deactivates
the first, explicitly and auditably
(`test_activating_second_publication_deactivates_first_auditably`); the
superseded publication's data remains queryable.

**Database:** extended the existing `sc_publications` table with 5 new
columns via the established `PRAGMA table_info` + `ALTER TABLE` migration
pattern (matching `approval_queue.py`'s style) rather than creating new
tables. No approval-history table was added — a single row's
approve/activate/deactivate timestamps and actors are sufficient audit trail
for this phase; a separate history table would only be warranted if multiple
approval attempts per publication needed independent tracking, which isn't a
current requirement.

**Verified against the real database** with `sc_admin_cli.py` (dry-run):
`list` shows `sc-sac-my-2026-05-29 status=pending official=886 parsed=886`;
`show` displays full metadata and a reconciling summary;
`securities --ticker 1155` correctly reports "not found" (Maybank genuinely
absent from this list); `securities --ticker 7113` correctly returns Top
Glove as COMPLIANT. Dry-run `approve` and `activate` printed what would
happen and wrote nothing — confirmed by a follow-up `list` still showing
`status=pending`.

## 4. SC Publication Current State

**`sc-sac-my-2026-05-29` is `pending`** — not approved, not activated.
Confirmed via `sc_admin_cli.py list`/`show`:

```
sc-sac-my-2026-05-29   status=pending   official=886  parsed=886
approved_at: null   approved_by: null
activated_at: null  activated_by: null
```

It was **not** touched by this phase's work, per the explicit instruction. It
remains ready for human review whenever you choose to run
`sc_admin_cli.py approve ... --apply` and `activate ... --apply`.

## 5. Authority Invariants — Demonstrated

`test_sc_admin_workflow.py` proves the exact required sequence, using a
freshly-seeded compliant ticker "1155" in a staged publication:

| State | Ticker 1155 result | Test |
|---|---|---|
| Before activation (not even approved) | `UNKNOWN` / `no_approved_publication` → BLOCKED | `test_before_activation_known_compliant_ticker_is_unknown_blocked` |
| After approval only (not yet activated) | `UNKNOWN` / `no_approved_publication` → BLOCKED | `test_after_approval_only_still_unknown_blocked` |
| After approval **and** activation | `PASS` / `authoritative_compliant` | `test_after_approval_and_activation_pass` |

Plus: an unknown ticker not in the publication → `UNKNOWN`/BLOCKED; a ticker
explicitly marked non-compliant → `REJECT`/BLOCKED. Approval alone is never
sufficient — confirmed by direct assertion in the second test.

## 6. Tests — Complete Results

Full backend suite, 62 files (excludes only `test_moomoo.py`, which doesn't
exist as a separate file — `test_moomoo_paper_adapter.py`/
`test_moomoo_status.py` are included and passing):

```
PASS=62 FAIL=0
```

New Phase 1 test files: `test_approval_risk_bypass.py` (9),
`test_weekly_loss.py` (11), `test_sc_admin_workflow.py` (14) — all passing.

Two pre-existing test files (`test_end_to_end_decision.py`,
`test_shariah_gate_my.py`) needed a one-line update each to pass explicit
`official_record_count`/`extractable_record_count` to
`ingest_universe_json()`, since the newly-hardened `activate_publication()`
now requires these to reconcile. Background test run `b9teoyeko` is the run
that originally caught both regressions (its `FAIL` lines for these two
files reflect that pre-fix state). Both were diagnosed as the same root
cause, fixed, and reconfirmed passing individually and in a full clean
re-run (`b29zn7tb7`: `PASS=62 FAIL=0`). This is a **stricter check catching
under-specified test fixtures**, not a weakening of anything — neither
file's assertions changed.

`ruff check` is clean on every production file touched this phase; the only
flag was the pre-existing "env var set before import" pattern in test files,
consistent with the codebase's established convention.

## 7. Remaining Findings

- **Medium** — Admin mutation commands (`approve`/`reject`/`activate`/
  `deactivate`) are gated only by filesystem/shell access to the CLI, not by
  any identity system. This is intentional per explicit instruction (no fake
  auth) but means anyone with server/repo access can currently run them.
  Must be closed with real authn/authz before any HTTP exposure.
- **Medium** — `authoritative_risk_verdict()`'s `loss_per_trade_pct` is fixed
  at `0.0` because no authoritative per-trade realized-loss source currently
  exists server-side (the client-supplied value was removed as part of
  closing the bypass, rather than trusted). This means the per-trade-loss
  risk check is currently a no-op in the re-derivation path. Worth a
  dedicated data source in a later phase if this limit needs to be enforced
  against real per-trade loss.
- **Low** — Options (`asset_class == "option"`) skip the portfolio equity
  overlay in `authoritative_risk_verdict()`, mirroring a pre-existing gap in
  `portfolio_risk_overlay()` itself (not introduced by this phase).
- **Low** — `period_realized_pnl()` has a documented shortcut: if current
  cumulative `realized_pnl` is exactly `0`, it returns `OK`/`0` without
  checking for a baseline snapshot, which is correct for "no realized P&L
  yet" but would also (harmlessly) report 0% for the edge case where
  realized P&L round-trips back to exactly 0 within the period.
- **Informational** — The `sc-sac-my-2026-05-29` publication's securities
  table holds 905 rows (887 COMPLIANT + 18 NON_COMPLIANT) versus the
  reconciled `official/extractable/parsed_record_count` of 886. Verified
  this is intentional (Phase 0C): the 886 figure reconciles only the main
  compliant list; the extra rows are one additional-instrument entry and 18
  entries from SC's separate "newly non-compliant" reclassification table,
  both explicitly and separately tracked in `sc_malaysia_import.py`. Not a
  bug, but worth knowing before assuming "886" means "total row count."
- **Informational** — Running any `test_*.py` file directly on a Windows
  console with the default cp1252 code page will raise `UnicodeEncodeError`
  on the arrow characters in some print statements; this is a
  terminal-encoding artifact (fixed by `PYTHONIOENCODING=utf-8`), not an
  application defect — no assertions are affected.

## 8. Phase 2 Recommendation

**Ready to proceed** with Frontend + Dashboard, consuming the existing
read-only `/api/*` endpoints (universe, screening, shariah verdicts, quant,
risk limits, evidence, knowledge search) — these were designed read-only and
public/local-safe from the start.

**Blocked until addressed** before any mutation surface (approve/reject/
activate/deactivate, or anything that submits/cancels orders) is wired into
a UI reachable by anyone other than a trusted operator at a terminal:

1. A real authentication/authorization system — even a minimal one (single
   admin session token, or a basic login) — must exist before these
   CLI-only operations become HTTP endpoints. This was explicitly deferred
   per instruction and is the single largest Phase 2 prerequisite.
2. A decision on RBAC granularity (is there one admin role, or reviewer vs.
   activator separation?) should be made before building the approval UI,
   since the data model already supports recording distinct
   `approved_by`/`activated_by`/`deactivated_by` identities.

**Copilot/LLM:** no LLM currently sits in the decision path — safe to add an
explanatory-only LLM layer in Phase 2 per the original architecture
(`CLAUDE.md`: "An LLM may explain a decision but never make, approve, or
bypass one"), as long as it only reads evidence/decision records and never
writes to `sc_malaysia_store`, `risk_checks`, or `approval_queue`.

No changes were made to `sc-sac-my-2026-05-29`'s status — it remains
`pending`, awaiting an explicit approve/activate decision.
