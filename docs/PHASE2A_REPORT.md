# Phase 2A Final Report — Governance Security Gate Before Frontend

Scope: close the remaining governance/security gap from the Phase 1 report
(no authentication/authorization on SC publication mutations), perform a
final authority-path audit across Shariah/risk/execution, resolve the
`loss_per_trade_pct` finding, and document the options-risk boundary. No
frontend, Copilot, Graphiti, Neo4j, autonomous agents, or broker/live
trading work was touched, per the phase's explicit scope.

## 1. Authentication mechanism implemented

New module `backend/auth.py`. Credentials (username, PBKDF2-HMAC-SHA256
salted password hash, role) are configured via the `SC_ADMIN_AUTH_USERS`
environment variable — a JSON array of `{"username", "password_hash",
"role"}` — read from `backend/.env` (gitignored, never committed;
`.env.example` documents the format and the blank placeholder).
`python backend/auth.py hash <password>` generates a hash to paste into
`.env`. No third-party auth framework was added: this is stdlib `hashlib`
only, matching the instruction to prefer the smallest production-sensible
mechanism.

`authenticate_credentials(username, password) -> Actor` is the single
entry point. It fails closed on every unhappy path: unknown username,
wrong password, and an empty/malformed `SC_ADMIN_AUTH_USERS` all raise
`AuthenticationError` — none silently succeed. An unknown username and a
wrong password are indistinguishable to the caller (same exception, and a
dummy hash comparison always runs so a timing side-channel can't leak
which case occurred).

No fake authentication was implemented: there is no code path anywhere
that accepts a client-supplied username as truth, an `admin=true` field,
hard-coded bypass headers, or a secret embedded in a frontend. The only way
to become an `Actor` is to present a username and password that verify
against `SC_ADMIN_AUTH_USERS`.

No HTTP login/session route exists yet, because no HTTP mutation route
exists yet (see §3) — building session/token issuance around
`authenticate_credentials()` ahead of an actual HTTP consumer would be
speculative infrastructure. The function is written so that addition is a
thin wrapper later, not a redesign.

## 2. Authorization / RBAC model

Two roles, defined in `auth.ROLE_PERMISSIONS`:

| Role | Permitted actions |
|---|---|
| `reviewer` | `approve`, `reject` |
| `admin` | `approve`, `reject`, `activate`, `deactivate` |

**Decision: no separate `activator` role.** Activation is the single
highest-stakes action in this system — it is the one thing that can make a
publication authoritative for a live Shariah PASS — so it is reserved for
`admin` alongside deactivation, rather than carved out as a third role.
A `reviewer`/`approver`/`activator` three-way split was considered and
rejected as unnecessary complexity for the current single/small-team
operator model (this is a paper-trading MVP, not a multi-desk institution).
If the team grows and duties around activation specifically need
separating from general admin, `ROLE_PERMISSIONS` is the only place that
would need to change — the credential mechanism is unaffected.

Authorization is enforced entirely server-side: `auth.authorize(actor,
action)` runs inside `sc_admin_cli.py`'s command handlers before any store
mutation is called, and there is no frontend in this phase to be mistaken
for a boundary. Verified directly:

```
reviewer 'alice' -> approve sc-sac-my-2026-05-29   -> Authenticated, dry run printed
reviewer 'alice' -> activate sc-sac-my-2026-05-29  -> AUTHORIZATION FAILED: role 'reviewer' cannot perform 'activate'
admin    'bob'   -> activate sc-sac-my-2026-05-29  -> Authenticated, dry run printed
wrong password   -> approve sc-sac-my-2026-05-29   -> AUTHENTICATION FAILED for username 'alice'
unknown user     -> approve sc-sac-my-2026-05-29   -> AUTHENTICATION FAILED for username 'mallory'
```

(Run against the real database in dry-run mode only — nothing was written;
confirmed below in §13.)

## 3. Protected mutation operations

`approve`, `reject`, `activate`, `deactivate` on SC Malaysia publications
are now gated by `auth.authenticate_credentials()` +
`auth.authorize()` inside `sc_admin_cli.py`'s command handlers — a failure
in either step prints an explicit reason and exits without touching the
database (`_authenticate()` returns `None` and every `cmd_*` function
raises `SystemExit(1)` rather than proceeding).

Per the instruction not to build HTTP mutation endpoints merely for
completeness: **no HTTP route was added.** No planned UI in this phase
needs one (the frontend itself is out of scope), so the CLI remains the
only mutation surface, exactly as it was designed in Phase 1 — now with
real authentication in front of it instead of "whoever has shell access."
The read-only `/api/*` surface (`/api/universe`, `/api/universe/{ticker}`,
`/api/universe/publications`, `/api/shariah/*`, `/api/quant/*`,
`/api/risk`, `/api/evidence/*`, `/api/knowledge/*`) is unchanged and
remains public/local-safe, since none of it can mutate anything.

If a future phase adds an HTTP mutation route, the documented boundary is:
call `auth.authenticate_credentials()` against the submitted
credentials/token, `auth.authorize()` against the requested action, return
`401` on authentication failure and `403` on authorization failure, and
never read the actor identity from the request body (see §4).

## 4. Actor identity provenance

`sc_admin_cli.py`'s `--reviewer`, `--activated-by`, and `--deactivated-by`
free-text flags were **removed entirely** — there is no longer any
argument that accepts an identity string directly. Every mutating
subcommand now takes `--username` (plus a password, via interactive
`getpass` prompt by default, or `SC_ADMIN_CLI_PASSWORD` for scripted use)
and passes `actor.username` — the value that came back from
`authenticate_credentials()` — as `reviewer=`/`activated_by=`/
`deactivated_by=` to the store functions. There is no code path left that
could record a spoofed identity, because there is no parameter left that
accepts one.

`sc_malaysia_store.py`: `approved_by`, `activated_by`, `deactivated_by`
were already persisted (Phase 1). This phase adds `rejected_at` /
`rejected_by` columns and wires them through `reject_publication()` —
previously the `reviewer` argument was accepted but silently discarded,
never written to the database (a real gap found during this audit, not
mentioned in the Phase 1 report). `ensure_sc_tables()`'s migration comment
was updated to state plainly that these columns are now authenticated
actor identities as of this phase, not just "whoever typed a name at the
CLI." Verified with a new assertion in `test_sc_admin_workflow.py`:
rejecting a publication now persists `rejected_by`/`rejected_at` on the
row, not just in the function's return value.

Approval/activation/deactivation timestamps and reconciliation metadata
(`approved_at`, `activated_at`, `deactivated_at`, `deactivation_reason`,
publication ID, source hash, parser version, record counts) are untouched
and continue to be recorded exactly as in Phase 1.

## 5. Final Shariah authority-path audit

Traced `sc_malaysia_store.check_eligibility()` (the only function that can
produce PASS/REJECT/UNKNOWN) and every caller of it
(`shariah_gate.check_symbol`, `local_api.authoritative_shariah_verdict`,
`screening_api._shariah_verdict`). Confirmed, with tests:

| Scenario | Result | Test |
|---|---|---|
| Pending publication (not approved) | `UNKNOWN` / `no_approved_publication` | `test_before_activation_known_compliant_ticker_is_unknown_blocked` |
| Approved but not yet activated | `UNKNOWN` / `no_approved_publication` | `test_after_approval_only_still_unknown_blocked` |
| Rejected publication | `UNKNOWN` (via `check_eligibility`, since a rejected publication is never active) | `test_rejected_publication_cannot_be_approved_or_activated` |
| Approved + activated, ticker absent | `UNKNOWN` / `not_present_in_approved_publication` | `test_unknown_ticker_absent_from_publication` |
| Approved + activated, ticker explicitly non-compliant | `REJECT` / `authoritative_non_compliant` | `test_explicit_non_compliant_is_reject` |
| Approved + activated, ticker compliant | `PASS` / `authoritative_compliant` | `test_after_approval_and_activation_pass` |
| Legacy/stale flat JSON, even claiming `validation.status: active` and a COMPLIANT ticker | `UNKNOWN` — never PASS | `test_legacy_json_fallback`, `test_legacy_json_never_produces_pass_even_when_file_claims_active` |
| Client-supplied Shariah verdict on `/paper/approval` | Ignored; server recomputes via `authoritative_shariah_verdict` | `test_approval_shariah_bypass.py` (3 scenarios, Phase 0) |
| LLM output | Not authoritative — verified no LLM/OpenRouter import exists anywhere in `local_api.py`, `shariah_gate.py`, `sc_malaysia_store.py`, `approval_queue.py`, `paper_execution.py`, or `agents/risk_engine.py`; the only OpenRouter usage in the repo is the News panel's article summarizer, structurally disconnected from the gate chain | `test_bridge_no_llm_in_path.py` (2026-09-23). This row previously read "verified by direct grep of the decision path, not test-asserted (there is nothing to call)". Once `backend/bridge/` existed there *was* something to call, and a grep someone remembers to run is not an invariant. The test is a static AST check: no bridge module may import a model client or carry a model URL in code, nothing outside the bridge may import it, and no LLM-reachable tool may name a write or operator-gated route. |

No new code was needed here — Phase 0/1 already closed every bypass; this
was verification only, per the instruction to trace the actual code paths
rather than assume prior claims hold.

## 6. Final risk authority-path audit

`local_api.authoritative_risk_verdict()` (Phase 1, extended this phase for
`loss_per_trade_pct` — see §8) recomputes every dimension from
server-controlled state:

- **Portfolio state / exposure**: `portfolio_risk_overlay()` reads live
  `paper_positions` via `portfolio_snapshot_with_exposure()` — never a
  client-submitted percentage.
- **Quantity / price**: taken from the `/paper/approval` request's
  `preview.quantity`/`preview.price`, but re-fed into the same
  `portfolio_risk_overlay()` the preview step uses, so a client cannot
  submit one quantity to `/paper/preview` and a different, larger one to
  `/paper/approval` without it being evaluated honestly against real
  portfolio state at approval time (`test_2_larger_quantity_at_approval_is_evaluated_honestly`).
- **Order count**: `_orders_today_count()` — a real `COUNT(*)` against
  `approval_queue` for `APPROVED_PAPER_READY` rows since midnight UTC, not
  the client's claimed `orders_today`.
- **Daily / weekly realised loss**: `period_realized_pnl()` against
  `paper_positions`/`portfolio_value_snapshots`, not the client's claim;
  fails closed to `float('inf')` when a baseline snapshot is missing.

All nine required client-spoofing scenarios are covered by
`test_approval_risk_bypass.py` (10 tests total — 9 from Phase 1 plus a new
one for `loss_per_trade_pct`, see §8): forged PASS vs. a genuine
position-limit breach, larger quantity at approval, portfolio value read
from the server, forged daily-loss/orders-today, symbol switching, an
oversized quantity alone, price/quantity together, cross-check against the
Shariah fix, a genuinely valid order still succeeding, and the new
loss-per-trade ceiling. All 10 pass.

## 7. Execution authority-path audit

Traced `local_api.execute_paper_order` → `paper_execution.execute_paper_order`.
`PaperExecutionRequest` (the only body FastAPI binds for
`/paper/execute/{queue_id}`) has exactly one field, `confirmation_phrase` —
there is no schema field for `approved`, `shariah`, or `risk` at all, so
Pydantic silently drops any such fields a client sends before the handler
ever sees them. Execution is gated entirely by the server-stored
`approval_queue` row, looked up by `queue_id`:

1. `approval_status == "APPROVED_PAPER_READY"` (server-set at approval time)
2. `shariah_status == "PASS"` and `risk_status == "PASS"` — both columns
   populated from `verified_shariah`/`verified_risk` at `/paper/approval`
   time (Phase 0/1's `record_approval(..., verified_shariah=,
   verified_risk=)`), never from the client
3. `validate_approval_payload_for_execution()` — an internal consistency
   check on the payload snapshot captured at approval time (symbol/side/
   quantity/price/notional match across `preview`/`approval`/`candidate`)

One nuance found and verified safe, not a vulnerability: this consistency
check also re-checks `payload.preview.agent_summary.shariah/risk.status ==
PASS`, which reads the *client's original preview claim* stored at
approval time, not the authoritative recomputed value. This is
**subordinate, not primary** — by the time this check runs,
`approval.shariah_status`/`approval.risk_status` (the authoritative
columns) have already gated `SHARIAH_GATE_FAILED`/`RISK_GATE_FAILED`
earlier in the same function, so this check can only make execution
*stricter*, never bypass anything: an authoritative PASS plus a pessimistic
client claim can produce a spurious `APPROVAL_AUDIT_FAILED`, but a forged
optimistic client claim can never produce an execution that the
authoritative columns would have blocked.

New tests, `test_execution_spoofing.py` (2 tests): executing a
genuinely-REJECTed queue_id with a request body forging `approved: true,
shariah: {status: PASS}, risk: {status: PASS}, approval_status:
APPROVED_PAPER_READY` still returns `NOT_APPROVED`; executing a
`queue_id` that was never created at all (no server-side approval exists,
forged or otherwise) returns `NOT_FOUND`. Both pass, alongside the
pre-existing `test_paper_execution_gates.py` gate tests
(`SHARIAH_GATE_FAILED`, `RISK_GATE_FAILED`, `APPROVAL_AUDIT_FAILED`,
`NOT_APPROVED`) and `test_local_api_smoke.py`'s confirmation-phrase tests,
all reconfirmed passing.

## 8. Resolution of `loss_per_trade_pct`

The vault's `risk-policy.md` line 21 specifies "Maximum loss per trade
(% of portfolio): `0.5`" — this **is** an active, specified limit (not
absent), so the "document and remove" branch does not apply.

No stop-loss/exit-distance model exists anywhere in this codebase to
compute a precise prospective per-trade downside: S001's documented exit
rule (SMA-50 cross, or 90% of the highest close since entry) is a
next-session *technical* exit, not a fixed distance from the entry price,
and `agents/quant_agent.py` implements entry signals only — there is no
automated position-management/stop-execution engine at all. Inventing a
stop-distance assumption not backed by actual system behavior would be
guessing, which the instructions rule out.

**Implementation**: for a BUY, `loss_per_trade_pct` is now the candidate
order's own notional (`portfolio_risk_overlay()`'s `order_notional`,
already computed for the exposure checks — no new data source) as a
percentage of server-side account equity. This is the only bound on a
single trade's downside that is actually *true* given this policy's own
constraints (long-only, no margin, no leverage, no short selling —
risk-policy.md's "Required checks"): a long, unlevered position cannot
lose more than what was paid for it. It is a conservative worst-case
bound, not a precise estimate, and documented as such in
`authoritative_risk_verdict()`'s docstring. A SELL only reduces exposure
and creates no new downside, so it is exempt (`0.0`), matching how
sector-concentration and other BUY-only overlay checks already treat SELL.
Equity unavailable or invalid candidate fields fail closed to
`float('inf')` rather than silently passing.

Blast radius was checked before committing to this: only 7 test files
actually exercise `/paper/approval` end-to-end (the rest seed
`approval_queue` directly, bypassing `authoritative_risk_verdict`
entirely). Exactly one test broke —
`test_approval_risk_bypass.py`'s `test_2` used a $100 notional against a
$10,000 test account (1%, over the new 0.5%/$50 ceiling) where the test's
actual intent was "a small order," not "any specific size" — fixed by
reducing to $40 notional, preserving the original assertion. A new
dedicated test, `test_10_loss_per_trade_ceiling_is_enforced_from_server_notional`,
proves $40 (0.4%) passes and $60 (0.6%) is rejected — both comfortably
inside the 5% position / 25% exposure limits, isolating loss-per-trade as
the specific cause.

## 9. Options-risk finding

`portfolio_risk_overlay()`'s position/exposure/sector math assumes
`quantity * price` is an equity share notional — meaningless for an
options contract, where quantity is a contract count and price is
per-contract premium. `authoritative_risk_verdict()` has always
(Phase 1) skipped this overlay for `asset_class == "option"`
(CLAUDE.md known limitation #4); this phase additionally confirmed
`loss_per_trade_pct` stays `0.0` for options for the identical reason —
building an options-specific loss/exposure model was judged out of scope
("do not redesign options risk unless the fix is trivial and clearly
safe" — it is not trivial here, since it needs a genuinely different
notional model, not a parameter tweak).

An option's actual risk boundaries are enforced by
`option_structure_gate`/`account_shariah_gate` independently — collateral
sufficiency (covered call shares held, cash-secured put collateral, etc.),
not equity notional limits. This is a pre-existing gap (no
options-specific portfolio-concentration overlay existed at preview time
either), not newly introduced.

New test, `test_options_gap_boundary.py` (3 tests): an option order with
quantity/price parameters that would clearly breach every equity limit if
misread as equity exposure is not rejected by the equity overlay (proving
the boundary is real, not accidentally still enforced by luck); evaluating
that option order has no effect on a separately-evaluated equity
candidate's own result (proving no state leaks between the two paths); and
`authoritative_shariah_verdict` takes no `asset_class` parameter at all,
so the options risk exemption cannot be used to smuggle a ticker past
Shariah screening. All 3 pass.

## 10. Security regression tests

New files this phase:

- **`test_admin_auth.py`** (6 tests): unauthenticated/unknown-user login
  rejected; wrong password rejected; a valid admin permitted every action;
  a valid reviewer permitted approve/reject but rejected for
  activate/deactivate; actor identity always matches the credentials
  presented, never a spoofed username (a username/password mismatch
  across two real registered users fails to authenticate, proving there
  is no path from "knows someone else's password" to "becomes that
  identity" or vice versa); missing/malformed `SC_ADMIN_AUTH_USERS` fails
  closed.
- **`test_execution_spoofing.py`** (2 tests): forged
  `approved`/`shariah`/`risk` fields in an execute request body cannot
  override a genuinely REJECTed server-side approval; executing a
  nonexistent `queue_id` fails as `NOT_FOUND` regardless of what the body
  claims.
- **`test_options_gap_boundary.py`** (3 tests): see §9.
- **`test_approval_risk_bypass.py`**: extended from 9 to 10 tests (new
  `loss_per_trade` ceiling test, see §8); the 9 Phase 1 tests were
  re-verified unchanged in intent (one fixture value adjusted, see §8).

Actor-spoofing at the CLI layer is covered structurally, not just by
test: the `--reviewer`/`--activated-by`/`--deactivated-by` flags no longer
exist in `sc_admin_cli.py`'s argument parser, so
`sc_admin_cli.py approve ... --reviewer "fake-reviewer"` is now a CLI
usage error (`unrecognized arguments`), not a code path that would need a
runtime check to reject it. Manually verified against the real database
in dry-run mode (§13).

Shariah-spoofing and risk-spoofing scenarios (submitting
`shariah.status=PASS`/`risk.status=PASS`/`daily_loss_pct=0` etc. for a
pending/inactive/unknown/non-compliant publication or a genuinely
limit-breaching order) are covered by the Phase 0 (`test_approval_shariah_bypass.py`)
and Phase 1 (`test_approval_risk_bypass.py`) suites; both were re-run this
phase and confirmed still passing with no weakening.

## 11. Full test-suite result

66 `test_*.py` files exist in `backend/`. One, **`test_moomoo.py`, is not
an automated test** — it is an 11-line standalone script with no
assertions and no `main()` that opens a real TCP connection to a local
Moomoo OpenD gateway on `127.0.0.1:11111`. In this environment (no gateway
running) it **hangs indefinitely** rather than failing, which stalled the
first full-suite run this phase until the hung process was found (via
`Get-CimInstance Win32_Process`) and killed. This is a correction to the
Phase 1 report, which incorrectly stated this file "doesn't exist as a
separate file" — it does exist, it was simply never actually run in that
phase's suite loop by chance of shell glob behavior. It is not a security
finding (no gate logic lives in it) but is flagged in §14 as a real
housekeeping defect: a file named `test_*.py` that hangs instead of
running is a trap for the next person who runs `for f in test_*.py`.

Excluding that one file, the remaining 65 all pass:

```
PASS=65 FAIL=0
```

This includes every pre-existing suite plus this phase's 4 new/extended
files (`test_admin_auth.py`, `test_execution_spoofing.py`,
`test_options_gap_boundary.py`, and the extended
`test_approval_risk_bypass.py`), and reconfirms the Phase 1 suite
(`test_sc_admin_workflow.py`, `test_weekly_loss.py`, `test_sc_malaysia_store.py`,
`test_shariah_gate_my.py`, `test_end_to_end_decision.py`, etc.) with zero
regressions from this phase's `loss_per_trade_pct` and
`rejected_at`/`rejected_by` changes.

## 12. Ruff result

Clean on every production file touched this phase:
`backend/auth.py`, `backend/sc_admin_cli.py`, `backend/local_api.py`,
`backend/sc_malaysia_store.py`. The only flags anywhere are the
established "env var set before import" `E402` pattern in test files that
need environment variables set before importing the modules under test —
the same pattern already present and accepted throughout the existing
suite (`test_approval_risk_bypass.py`, `test_execution_spoofing.py`), not
a new issue.

## 13. Exact state of `sc-sac-my-2026-05-29`

**`pending`** — not approved, not activated, not rejected. Confirmed just
now via `sc_admin_cli.py list`/`show`:

```
sc-sac-my-2026-05-29   status=pending   official=886  parsed=886
approved_at: null    approved_by: null
activated_at: null   activated_by: null
rejected_at: null    rejected_by: null (new columns from this phase)
```

It was touched only by dry-run CLI invocations during this phase's manual
verification (an `approve` dry run as a `reviewer`, an `activate` dry run
rejected for role, an `activate` dry run as `admin`, two failed-login
attempts) — every one printed "DRY RUN -- nothing written" or an
authentication/authorization failure, and none wrote to the database. A
`list` after all of it still shows `pending`. This phase did not approve
or activate it, per the explicit instruction.

## 14. Remaining security/governance findings

- **Medium** — `reject_publication()`'s `reviewer` argument was accepted
  but never persisted before this phase (found during the actor-provenance
  audit, not previously reported). **Fixed this phase**: `rejected_at`/
  `rejected_by` columns added and wired through.
- **Medium** — No HTTP mutation route exists, so the CLI remains gated
  only by OS-level access to the machine plus the new
  `SC_ADMIN_AUTH_USERS` credentials. This is correct for the current
  scope (no UI consumes it yet) but is the concrete prerequisite before
  any web-based admin surface: an HTTP route would need session/token
  issuance built around `auth.authenticate_credentials()`, not merely
  reusing the CLI's interactive-prompt pattern.
- **Low** — `SC_ADMIN_AUTH_USERS` is a static, env-configured credential
  list, not a database-backed user table. Adding, rotating, or revoking a
  credential requires editing `.env` and restarting the process. Fine for
  the current single/small-team scale; would need a real user store if
  the reviewer/admin roster grows past a handful of people or needs
  self-service rotation.
- **Low** — `SC_ADMIN_CLI_PASSWORD` (the scripted/headless alternative to
  the interactive prompt) is documented as less secure than the prompt
  (can appear in shell history or process environment listings depending
  on how it's set) but exists because some legitimate automation may need
  it; it is opt-in, not the default.
- **Informational** — `test_moomoo.py` is dead code masquerading as a test
  file (see §11); it hangs rather than fails, and silently corrupts a
  naive `for f in test_*.py` loop's results by hanging the whole run
  rather than reporting a failure. Recommend renaming it to something
  that doesn't match the `test_*.py` glob (e.g. `check_moomoo_live.py`,
  matching the naming convention already used for other manual
  connectivity scripts) or deleting it, in a future housekeeping pass —
  out of scope to touch in a security-hardening phase without being asked.
- **Informational** — `validate_approval_payload_for_execution()`'s
  subordinate check against the client's original preview claim (§7) is
  safe but slightly confusing to read cold; a future cleanup could note
  in its docstring that it is defense-in-depth beneath the authoritative
  `shariah_status`/`risk_status` columns, not an independent gate.

## 15. READY FOR FRONTEND

**`READY FOR FRONTEND`**

Justification, tied to what was actually verified rather than "tests
pass":

- The client can no longer be authoritative for Shariah PASS, risk PASS,
  position sizing, account eligibility, or final trade eligibility —
  traced end-to-end this phase, not assumed from the Phase 1 report.
- SC publication mutations (approve/reject/activate/deactivate) now
  require real authentication and role-appropriate authorization,
  enforced server-side, with actor identity always derived from
  credentials, never from a client-supplied string — verified against the
  real CLI and real database in dry-run mode.
- Execution cannot be forged into happening via request-body claims; it
  depends entirely on server-stored, authoritatively-derived approval
  state — verified with new spoofing tests, not just inferred from
  reading the code.
- The two remaining numeric risk gaps flagged in the Phase 1 report
  (`loss_per_trade_pct`, options-risk boundary) are now either properly
  implemented (loss-per-trade) or explicitly documented as a bounded,
  isolated, tested pre-existing gap (options) — neither is a silent
  unknown anymore.
- `sc-sac-my-2026-05-29` remains `pending`, exactly as instructed.
- The full suite (65 real test files) passes with zero regressions, and
  ruff is clean on every touched production file.

Nothing found this phase blocks starting frontend/dashboard work against
the existing read-only `/api/*` surface. The Medium findings in §14 are
about the *next* layer (an HTTP admin surface, if the frontend's design
calls for one) — they do not weaken anything currently protecting the
trading gate chain, and are the concrete list of what needs to exist
before that specific future surface is built, not a blocker to starting
frontend work itself.
