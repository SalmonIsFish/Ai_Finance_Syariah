# Phase 2B — Malaysian Shariah Quant Screening Dashboard

Status: approved (design confirmed by user 2026-09-08, expanding on inspection findings below)

## Objective

Build a read-only, professional screening dashboard on top of the existing
deterministic backend that answers: *which Bursa Malaysia securities are
currently Shariah-eligible, quantitatively attractive, and within the
current risk framework?* — while keeping Shariah eligibility, quant signal,
attractiveness, and risk status visually and architecturally separate. No
combined "investment score" is ever computed or displayed.

## Hard scope boundary

No order submission/cancellation, no execution, no broker integration, no
portfolio mutation, no SC publication approve/reject/activate/deactivate,
no LLM/Copilot, no autonomous agents. The frontend is presentation-only;
every Shariah/risk/quant/publication decision stays server-side. The
existing authenticated admin CLI (`sc_admin_cli.py`) remains the only
mutation path.

## Inspection findings

- **Existing frontend**: `dashboard/index.html`, a single ~4,100-line
  vanilla HTML/CSS/JS file with no build step and no npm/package.json
  anywhere in the repo. It's the legacy US/Alpaca/Moomoo *operator console*
  (approval queue, execution audit, "officer desk" mutation UI), wired to
  legacy routes (`/paper/*`, `/execution-audit`, `/investment-committee`,
  etc.), not `/api/*`. Per `CLAUDE.md` it's retained, not extended. It does
  define a usable design-token system (dark ink-navy + brass accent,
  PASS/WARN/BAD status colors, light/dark theme toggle via
  `documentElement.dataset.theme` + localStorage) worth adapting.
  **`dashboard/index.html` will not be modified, refactored, or migrated.**
- **Backend `/api/*` contract**: already implemented in `screening_api.py`
  / `local_api.py` exactly matching the endpoint list below, all
  unauthenticated GET, read-only, delegating to the same deterministic
  domain modules the trading gate chain uses (`shariah_gate`,
  `sc_malaysia_store`, `agents.quant_agent`, `confidence`, `evidence`,
  `vault_indexer`). `CORSMiddleware` already defaults
  (`DEFAULT_ALLOWED_ORIGINS`) to `null` (file://) plus local static-server
  origins (`:5500`, `:8000`) — i.e. the backend already assumes a
  no-build-step static frontend.
- **No auth on `/api/*`**: `auth.py` exists but is only used by
  `sc_admin_cli.py` for mutating CLI commands; no `Depends()` auth is wired
  into any `/api/*` route. Consistent with spec §12/§22 — the dashboard
  must run without admin privileges and can't reach mutation routes because
  none exist for it to call.
- **Gap found and confirmed**: `screening_api.universe_list()` filters to
  `shariah_status == "COMPLIANT"` unconditionally. The real pending
  publication `sc-sac-my-2026-05-29` has 905 rows: 887 `COMPLIANT`, 18
  `NON_COMPLIANT` — so today's endpoint silently drops all REJECT rows from
  bulk listing. This conflicts with the requirement that REJECT be visible
  and filterable in the universe table. Fixed via an additive, optional
  query parameter (see API Changes below).
- **Governance state confirmed**: `sc-sac-my-2026-05-29` is
  `human_review_status = pending`, `activated_at = NULL`. `get_active_publication()`
  therefore returns `None`, and `/api/universe` currently returns
  `{"active_publication": null, "count": 0, "securities": []}`. This is the
  real, live "no active publication" state the dashboard's empty-state UX
  must handle — not a fixture-only concern.
- **No per-ticker risk verdict exists in the backend.** `/api/risk` returns
  only global configured policy limits (`risk_limits()` reads `Settings`
  directly); a real PASS/REJECT risk verdict requires order-specific inputs
  (position size, existing exposure) only available at `/paper/preview`
  time, which this phase does not call. The universe table's "risk status"
  column/filter will therefore show policy limits are configured but no
  per-security eligibility verdict — never fabricated as ELIGIBLE/BLOCKED.

## Architecture

- **Stack**: plain HTML + CSS + vanilla JS (ES modules). No React/Vue/
  Angular/Vite/webpack/npm dependencies, no build system — matches the
  repo's existing no-build model and the CORS defaults above.
- **Location**: `dashboard/screening/`, fully separate from
  `dashboard/index.html`.
- **Files**:
  ```
  dashboard/screening/
  ├── index.html       # universe screen (search, filters, table)
  ├── security.html     # detail page, ?ticker=1155
  ├── api.js             # fetch client — one function per /api/* endpoint, no retries/fallback-to-optimistic-state
  ├── logic.js            # pure functions: status/badge derivation, filtering, search matching, formatting — unit tested
  ├── render.js            # DOM painting, reads logic.js output, no business logic of its own
  ├── tokens.css            # design tokens adapted from dashboard/index.html's palette
  ├── screening.css          # layout/component styles built on tokens.css
  └── tests/
      └── logic.test.js       # node:test, run via `node --test`
  ```
- **Testing philosophy**: no JS framework exists in this repo, and the
  backend's own convention is plain `test_*.py` scripts with `main()`
  printing `PASS: ...` rather than pytest. Mirrored here: business/display
  logic (status derivation, PASS/REJECT/UNKNOWN handling, filters, search,
  the "never combine scores" invariant, governance empty-state logic) lives
  in pure functions in `logic.js`, tested with Node's built-in `node:test`
  (ships with Node — no npm install). `render.js`/DOM wiring is verified
  manually in a real browser per `CLAUDE.md`'s UI-testing rule, not
  simulated with jsdom.

## API changes (additive, backward-compatible)

`GET /api/universe` gains an optional `shariah_status` query parameter:

- Omitted (default): **unchanged** existing behavior — COMPLIANT-only, so
  no existing caller breaks.
- `shariah_status=PASS`: same as default, explicit form.
- `shariah_status=REJECT`: securities with `shariah_status == NON_COMPLIANT`
  in the active publication.
- `shariah_status=ALL`: both, each tagged with its derived PASS/REJECT.
- Any other value: `400` with a clear error body (fail predictably, not
  silently).
- Matching is case-insensitive on the query value.
- When there is no active publication, the response is unchanged regardless
  of `shariah_status`: `{"active_publication": null, "count": 0, "securities": []}`.
  UNKNOWN is never a value stored in `sc_security_status` rows or returned
  in bulk — it is derived per-ticker only, by `/api/universe/{ticker}` (via
  `sc_malaysia_store.check_eligibility`) when a ticker has no row in the
  active publication, or when no publication is active at all. No endpoint
  manufactures a bulk "UNKNOWN list."

No other endpoint changes. No mutation routes are added anywhere.

## UX rules

- **Shariah status** is always one of PASS / REJECT / UNKNOWN, sourced
  verbatim from the backend, never inferred or overridden client-side.
  UNKNOWN reads as "no authoritative basis to establish PASS — trading
  blocked," never as "non-compliant." REJECT reads as "authoritative source
  establishes non-compliance." Status is never color-only — always paired
  with text/icon.
- **Quant signal / attractiveness / risk** render as separate blocks, never
  combined into a Shariah score or an "investment score." A BUY quant
  signal never visually dominates a Shariah UNKNOWN/REJECT block — trading
  status (BLOCKED vs eligible-to-consider) is always shown as its own line
  driven by Shariah status alone.
- **Publication authority banner**, shown globally (both pages): the active
  publication's id/date when one exists, or an explicit "NO ACTIVE SHARIAH
  PUBLICATION — the current SC Malaysia publication has not been approved
  and activated; authoritative PASS cannot currently be established" state
  when `active_publication` is null. This is not hidden or downplayed.
- **States**: loading, empty (filters matched nothing), no-active-publication
  (governance state, distinct from "empty"), API error (shown as an actual
  error, never defaulted to an optimistic PASS/BUY/eligible value), and
  stale-data (surfaced when `data_freshness`/`cache_age_hours` indicates it,
  from `evaluate_quant`'s freshness fields).
- **Evidence/provenance**: Shariah block shows publication id, date, source
  hash; quant block shows strategy id, market-data source, as-of date; risk
  block shows the policy values and that they are configured limits (not a
  per-trade verdict). All sourced from `/api/evidence/{ticker}`,
  `/api/shariah/publication/{id}`, and the `screen_ticker`/`quant` payload
  — nothing fabricated.
- **Accessibility**: semantic HTML, keyboard navigation, visible focus
  states, labels, responsive layout, status conveyed by text/icon in
  addition to color.
- **Visual design**: adapt the legacy dashboard's ink-navy/brass palette
  and PASS/WARN/BAD tokens; institutional research-terminal feel; no
  gamified BUY/SELL buttons, no gradients/animation for their own sake.

## Testing plan

**Frontend (`node --test dashboard/screening/tests/logic.test.js`)**:
status passthrough (PASS/REJECT/UNKNOWN never transformed into each other),
score-separation invariant (no function in `logic.js` combines Shariah with
quant/attractiveness/risk into one value), filter functions (status,
sector, market, signal, attractiveness range), search matching (ticker,
issuer name), governance-empty-state derivation (`active_publication: null`
→ explicit no-active-publication state, not silently `0 securities`),
error/missing-value safety (a failed fetch or missing field never resolves
to PASS/BUY/eligible in the rendered state).

**Backend (new `backend/test_screening_api_universe_status.py`, matching
existing plain-script convention)**: default call unchanged; `PASS` filter;
`REJECT` filter surfaces `NON_COMPLIANT` rows; `ALL` returns both;
case-insensitive; invalid value fails predictably; no active publication
still returns the explicit empty shape regardless of the parameter;
`/api/screen/{ticker}` unchanged; confirm no mutation route exists on the
FastAPI app that wasn't there before.

## Security

No credentials, API keys, or admin tokens anywhere in `dashboard/screening/`.
No code path calls approve/reject/activate/deactivate/execute/cancel. The
dashboard runs fully unauthenticated against read-only GET endpoints.

## Final verification (before reporting done)

Full backend test suite; new frontend `node:test` run; `ruff check`/format
on any touched Python; app boot check; manual browser walkthrough (universe
load, search, filters, detail page, PASS/REJECT/UNKNOWN rendering,
no-active-publication state, theme toggle, responsive layout, simulated API
error); confirm `dashboard/index.html` diff is empty; confirm
`sc-sac-my-2026-05-29` still has `human_review_status = pending`,
`approved_at = NULL`, `activated_at = NULL`.

## Out of scope / explicitly not built

Copilot/LLM explanation layer (Phase 2C), SC publication approval/
activation UI, order execution, portfolio mutation, Graphiti/Neo4j.
