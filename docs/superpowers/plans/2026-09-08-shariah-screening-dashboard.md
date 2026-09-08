# Phase 2B — Malaysian Shariah Screening Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only, no-build-step vanilla-JS Malaysian Shariah screening dashboard at `dashboard/screening/` that consumes the existing `/api/*` read-only backend, with one small additive backend change (a `shariah_status` filter on `GET /api/universe`) needed to make REJECT securities visible.

**Architecture:** Two static HTML pages (`index.html` universe screen, `security.html` detail page) share three JS modules — `api.js` (fetch client), `logic.js` (pure, unit-tested business/display logic), `render.js` (DOM painting) — and one stylesheet of design tokens adapted from the legacy `dashboard/index.html` palette. The legacy dashboard is never modified. The backend gains one optional, backward-compatible query parameter; every other endpoint is unchanged.

**Tech Stack:** Plain HTML/CSS, vanilla JS (ES modules, no bundler), Python 3.11 / FastAPI (existing), Node's built-in `node:test` for frontend unit tests, no new dependencies anywhere.

**Spec:** `docs/superpowers/specs/2026-09-08-shariah-screening-dashboard-design.md`

## Global Constraints

- Read-only phase: no order submission/cancellation/execution, no portfolio mutation, no SC publication approve/reject/activate/deactivate, no LLM/Copilot, no autonomous agents. Verify at the end that no mutation route exists.
- `dashboard/index.html` (the legacy US/Alpaca/Moomoo operator console) must not be modified, refactored, or migrated. `git diff` on it must be empty at the end.
- No React/Vue/Angular/Vite/webpack/npm dependencies, no build system. Plain HTML/CSS/ES modules only.
- Every Shariah status shown (PASS/REJECT/UNKNOWN) must come verbatim from a backend response. No frontend code may derive, override, or invert a Shariah verdict. UNKNOWN must never be phrased or rendered as equivalent to REJECT/non-compliant.
- Quant signal, attractiveness, and risk are always displayed as separate values. No function or UI element may combine Shariah status with quant/attractiveness/risk into one score.
- No hardcoded ticker/issuer/compliance data in JS. All security data comes from `/api/*` responses fetched at runtime.
- API failures and missing values must render an explicit error/empty/unknown state — never default to PASS, BUY, or an eligible-looking state.
- The real publication `sc-sac-my-2026-05-29` must remain `human_review_status = pending`, `approved_at = NULL`, `activated_at = NULL` throughout and after this work. Never call `sc_malaysia_store.approve_publication` / `activate_publication` against it.
- Only one backend API change is in scope: the additive `shariah_status` query parameter on `GET /api/universe` (Task 1). No other endpoint changes.

---

### Task 1: Backend — additive `shariah_status` filter on `GET /api/universe`

**Files:**
- Modify: `backend/screening_api.py:52-77` (`universe_list`)
- Modify: `backend/local_api.py:2063-2069` (`api_universe` route)
- Test: `backend/test_screening_api_universe_status.py` (new)

**Interfaces:**
- Produces: `screening_api.universe_list(connection, *, limit=200, offset=0, shariah_status="PASS")` — raises `ValueError` for any `shariah_status` not in `{"PASS", "REJECT", "ALL"}` (case-insensitive). Returns the existing response shape (`active_publication`, `count`, `offset`, `limit`, `securities`) plus a new `shariah_status_filter` key, and every security dict in `securities` now additionally carries `"verdict"`: `"PASS"` or `"REJECT"` (derived server-side from `shariah_status == "COMPLIANT"`, never left for the frontend to compute).
- Produces: `GET /api/universe?shariah_status=PASS|REJECT|ALL` — omitting the parameter preserves today's exact behavior (COMPLIANT-only). An invalid value returns HTTP 400.
- Consumes: `sc_malaysia_store.get_active_publication`, `sc_malaysia_store.publication_securities` (unchanged, already implemented).

- [ ] **Step 1: Write the failing test file**

Create `backend/test_screening_api_universe_status.py`:

```python
"""Tests for the additive shariah_status filter on GET /api/universe.

Covers the Phase 2B dashboard requirement that bulk REJECT securities be
visible/filterable without breaking the existing COMPLIANT-only default
that other callers (including test_screening_api.py) depend on.
"""

import os
import sqlite3
import tempfile
from pathlib import Path

fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text('{"validation": {"status": "active"}, "records": []}', encoding="utf-8")
os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"

from fastapi.testclient import TestClient

import local_api
import sc_malaysia_store
from local_api import app


DB_PATH = Path(fixture_dir.name) / "paper_trading.db"


def _reset_db() -> sqlite3.Connection:
    if DB_PATH.exists():
        DB_PATH.unlink()
    local_api.DB_PATH = DB_PATH
    sc_malaysia_store.DB_PATH = DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _seed_mixed_publication(conn: sqlite3.Connection) -> None:
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": "sc-sac-my-test-mixed",
            "publication_date": "2026-02-01",
            "source_document_hash": "test",
            "official_record_count": 3,
            "extractable_record_count": 3,
            "parsed_record_count": 3,
            "parser_version": "test-fixture-v1",
            "human_review_status": "pending",
        },
    )
    sc_malaysia_store.insert_securities(
        conn,
        "sc-sac-my-test-mixed",
        [
            {"ticker": "1155", "issuer_name": "Compliant Bank Bhd", "shariah_status": "COMPLIANT"},
            {"ticker": "1295", "issuer_name": "Compliant Bhd Two", "shariah_status": "COMPLIANT"},
            {"ticker": "8888", "issuer_name": "Non Compliant Bhd", "shariah_status": "NON_COMPLIANT"},
        ],
    )
    sc_malaysia_store.approve_publication(conn, "sc-sac-my-test-mixed")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-test-mixed")


def test_1_default_omitted_param_matches_original_compliant_only_behavior():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    response = client.get("/api/universe")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 2
    assert {s["ticker"] for s in body["securities"]} == {"1155", "1295"}
    assert all(s["verdict"] == "PASS" for s in body["securities"])
    print("PASS: 1 — omitting shariah_status preserves the original COMPLIANT-only default")


def test_2_explicit_pass_filter_matches_default():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=PASS").json()
    assert body["count"] == 2
    assert all(s["verdict"] == "PASS" for s in body["securities"])
    print("PASS: 2 — explicit shariah_status=PASS matches the default")


def test_3_reject_filter_surfaces_non_compliant_rows():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=REJECT").json()
    assert body["count"] == 1
    assert body["securities"][0]["ticker"] == "8888"
    assert body["securities"][0]["verdict"] == "REJECT"
    print("PASS: 3 — shariah_status=REJECT surfaces the previously-hidden NON_COMPLIANT row")


def test_4_all_filter_returns_every_row():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=ALL").json()
    assert body["count"] == 3
    tickers = {s["ticker"] for s in body["securities"]}
    assert tickers == {"1155", "1295", "8888"}
    print("PASS: 4 — shariah_status=ALL returns both compliant and non-compliant rows")


def test_5_case_insensitive():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    body = client.get("/api/universe?shariah_status=reject").json()
    assert body["count"] == 1
    print("PASS: 5 — shariah_status matching is case-insensitive")


def test_6_invalid_value_fails_predictably():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    response = client.get("/api/universe?shariah_status=MAYBE")
    assert response.status_code == 400, response.text
    print("PASS: 6 — an invalid shariah_status value returns HTTP 400, not a silent fallback")


def test_7_no_active_publication_stays_empty_regardless_of_filter():
    conn = _reset_db()
    conn.close()

    client = TestClient(app)
    for status in ("PASS", "REJECT", "ALL"):
        body = client.get(f"/api/universe?shariah_status={status}").json()
        assert body["active_publication"] is None
        assert body["count"] == 0
        assert body["securities"] == []
    print("PASS: 7 — no active publication returns the explicit empty shape for every filter value")


def test_8_screen_ticker_endpoint_unchanged():
    conn = _reset_db()
    _seed_mixed_publication(conn)
    conn.close()

    client = TestClient(app)
    response = client.get("/api/screen/1155")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["shariah"]["status"] == "PASS"
    assert "eligibility" in body
    print("PASS: 8 — /api/screen/{ticker} is unaffected by the universe filter change")


def test_9_no_mutation_route_introduced():
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET", "HEAD"}, f"{path} accepts {methods} -- must stay read-only"
    print("PASS: 9 — every /api/* route remains GET-only after this change")


def main():
    test_1_default_omitted_param_matches_original_compliant_only_behavior()
    test_2_explicit_pass_filter_matches_default()
    test_3_reject_filter_surfaces_non_compliant_rows()
    test_4_all_filter_returns_every_row()
    test_5_case_insensitive()
    test_6_invalid_value_fails_predictably()
    test_7_no_active_publication_stays_empty_regardless_of_filter()
    test_8_screen_ticker_endpoint_unchanged()
    test_9_no_mutation_route_introduced()
    print("\nAll universe shariah_status filter tests passed.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it to confirm it fails**

Run (from `backend/`): `../.venv/Scripts/python test_screening_api_universe_status.py`
Expected: `TypeError: universe_list() got an unexpected keyword argument 'shariah_status'` (raised inside `api_universe`, surfaced as a 500) — or, if FastAPI swallows it into a 422 because `shariah_status` isn't a declared route parameter yet, a failing assertion on `response.status_code == 200`. Either way, the test suite must not pass yet.

- [ ] **Step 3: Implement the `screening_api.universe_list` change**

Replace the existing `universe_list` function (`backend/screening_api.py:52-77`) with:

```python
_UNIVERSE_STATUS_FILTERS = {"PASS", "REJECT", "ALL"}


def universe_list(
    connection: sqlite3.Connection,
    *,
    limit: int = 200,
    offset: int = 0,
    shariah_status: str = "PASS",
) -> dict:
    """Securities in the currently active, approved SC publication.

    Empty (not an error) when nothing is active -- this reads only the
    approved+activated publication, never the legacy JSON fallback and
    never an unapproved 'pending' or 'needs_reconciliation' publication.

    ``shariah_status`` filters which authoritative rows come back: PASS
    (default, preserves the original COMPLIANT-only behavior), REJECT
    (NON_COMPLIANT rows), or ALL (both). Every returned row carries a
    normalized ``verdict`` of "PASS" or "REJECT" alongside the raw
    ``shariah_status`` so callers never interpret the raw SC enum
    themselves. Raises ValueError for anything else -- callers must fail
    predictably, not silently fall back to a default.
    """
    normalized_filter = shariah_status.strip().upper()
    if normalized_filter not in _UNIVERSE_STATUS_FILTERS:
        raise ValueError(f"invalid shariah_status: {shariah_status!r}")

    pub = sc_malaysia_store.get_active_publication(connection)
    if pub is None:
        return {"active_publication": None, "count": 0, "securities": []}

    securities = sc_malaysia_store.publication_securities(connection, pub["id"])
    tagged = []
    for s in securities:
        row = dict(s)
        row["verdict"] = "PASS" if row["shariah_status"] == "COMPLIANT" else "REJECT"
        tagged.append(row)

    if normalized_filter == "PASS":
        matched = [r for r in tagged if r["verdict"] == "PASS"]
    elif normalized_filter == "REJECT":
        matched = [r for r in tagged if r["verdict"] == "REJECT"]
    else:
        matched = tagged

    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    return {
        "active_publication": {
            "id": pub["id"],
            "publication_date": pub["publication_date"],
            "activated_at": pub["activated_at"],
        },
        "count": len(matched),
        "offset": offset,
        "limit": limit,
        "shariah_status_filter": normalized_filter,
        "securities": matched[offset : offset + limit],
    }
```

- [ ] **Step 4: Implement the route change**

Replace the existing route (`backend/local_api.py:2063-2069`):

```python
@app.get("/api/universe")
def api_universe(limit: int = 200, offset: int = 0, shariah_status: str = "PASS") -> dict:
    connection = db()
    try:
        return screening_api.universe_list(
            connection, limit=limit, offset=offset, shariah_status=shariah_status
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()
```

(`HTTPException` is already imported at the top of `backend/local_api.py`.)

- [ ] **Step 5: Run the new tests to confirm they pass**

Run: `../.venv/Scripts/python test_screening_api_universe_status.py`
Expected: 9 `PASS:` lines and `All universe shariah_status filter tests passed.`

- [ ] **Step 6: Run the existing screening API tests to confirm no regression**

Run: `../.venv/Scripts/python test_screening_api.py`
Expected: 10 `PASS:` lines and `All screening API tests passed.` (This is the test that already asserts `/api/universe` returns `count == 0` with no active publication — must still pass unchanged.)

- [ ] **Step 7: Ruff-check the two modified files**

Run: `../.venv/Scripts/python -m ruff check screening_api.py local_api.py test_screening_api_universe_status.py`
Expected: no new findings introduced by this change (pre-existing findings elsewhere in the repo are out of scope).

- [ ] **Step 8: Commit**

```bash
git add backend/screening_api.py backend/local_api.py backend/test_screening_api_universe_status.py
git commit -m "Add additive shariah_status filter to GET /api/universe

REJECT securities were silently dropped from bulk listing (the active
publication has 18 NON_COMPLIANT rows out of 905). Default behavior is
unchanged; PASS/REJECT/ALL are opt-in via a query param, invalid values
fail with 400.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfPiJ5h8bmJNBprhdG229a"
```

---

### Task 2: Frontend pure logic — `logic.js`

**Files:**
- Create: `dashboard/screening/logic.js`
- Create: `dashboard/screening/tests/logic.test.js`
- Create: `dashboard/screening/package.json`

**Why `package.json` is needed (not a build system):** these files use
`export`/`import` syntax. Browsers handle that fine via
`<script type="module">` regardless of file extension, but Node's module
loader treats a bare `.js` file as CommonJS unless the nearest
`package.json` says `"type": "module"` — without it, `node --test` fails
immediately with `SyntaxError: Unexpected token 'export'`. This file has
no `dependencies`, needs no `npm install`, and adds no build step; it's a
one-line marker so Node's built-in test runner (which the approved spec
explicitly calls for) can parse ES module syntax. `.mjs` extensions were
considered instead, but `python -m http.server` (used for manual/CI
verification) does not reliably serve `.mjs` with a JavaScript MIME type
on all Python versions, which breaks strict `type="module"` loading in
the browser — the `package.json` marker avoids that trap entirely.

**Interfaces:**
- Produces (all pure functions, no DOM/fetch access, imported by `api.js`, `render.js`, `index.html`, and `security.html` in later tasks):
  - `buildUniverseQuery({shariahStatus, limit, offset})` → query string
  - `statusBadge(status)` → `{label, className, explanation}`
  - `tradingStatusFromShariah(status)` → `{label: "BLOCKED", reason}` or `null` (only `null` for `"PASS"`)
  - `formatDate(value)`, `formatPct(value)`, `formatScore(value)` → display strings, `"—"` on missing/invalid input
  - `matchesSearch(security, query)` → boolean
  - `filterBySelects(security, {sector, market, verdict})` → boolean
  - `filterByQuant(quantState, {signal, minAttractiveness, maxAttractiveness})` → boolean
  - `publicationBannerState(activePublication)` → `{state: "none", headline, detail}` or `{state: "active", id, publicationDate, activatedAt}`
  - `freshnessFromAsOfDate(asOfDate, todayIso, staleDays=5)` → `{label, isStale}`
  - `safeQuantState(raw, error)` → `{status: "ok"|"error", signal, strategyId, asOfDate, attractiveness, components}`
  - `distinctValues(securities, field)` → sorted array of unique truthy values

- [ ] **Step 1: Write `dashboard/screening/package.json`**

```json
{
  "name": "amanah-trader-screening-dashboard",
  "private": true,
  "type": "module"
}
```

- [ ] **Step 2: Write `dashboard/screening/logic.js`**

```js
// logic.js
// Pure, framework-free functions: query building, status formatting,
// filtering, search, and safety fallbacks. Nothing here calls fetch or
// touches the DOM, and nothing here re-derives a Shariah PASS/REJECT/
// UNKNOWN verdict -- every status value comes from the backend response
// verbatim. Unit tested by tests/logic.test.js via `node --test`.

export function buildUniverseQuery({ shariahStatus = "PASS", limit = 1000, offset = 0 } = {}) {
  return new URLSearchParams({
    shariah_status: shariahStatus,
    limit: String(limit),
    offset: String(offset),
  }).toString();
}

const SHARIAH_BADGES = {
  PASS: {
    label: "PASS",
    className: "badge badge--ok",
    explanation:
      "An approved and activated SC Malaysia publication establishes this security as Shariah-compliant.",
  },
  REJECT: {
    label: "REJECT",
    className: "badge badge--bad",
    explanation:
      "An approved and activated SC Malaysia publication explicitly establishes this security as non-compliant.",
  },
  UNKNOWN: {
    label: "UNKNOWN",
    className: "badge badge--warn",
    explanation:
      "The security is not currently established as eligible by an approved and activated authoritative publication. This is not the same as non-compliant.",
  },
};

export function statusBadge(status) {
  return (
    SHARIAH_BADGES[status] || {
      label: "UNKNOWN",
      className: "badge badge--warn",
      explanation: "No authoritative Shariah status was returned. Treated as UNKNOWN and blocked.",
    }
  );
}

export function tradingStatusFromShariah(status) {
  if (status === "PASS") {
    return null;
  }
  if (status === "REJECT") {
    return { label: "BLOCKED", reason: "Authoritative source establishes non-compliance." };
  }
  if (status === "UNKNOWN") {
    return { label: "BLOCKED", reason: "No authoritative PASS is currently established." };
  }
  return { label: "BLOCKED", reason: "Shariah status could not be confirmed." };
}

export function formatDate(value) {
  return value ? value : "—";
}

export function formatPct(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

export function formatScore(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
}

export function matchesSearch(security, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return true;
  const ticker = (security.ticker || "").toLowerCase();
  const issuer = (security.issuer_name || "").toLowerCase();
  return ticker.includes(q) || issuer.includes(q);
}

export function filterBySelects(security, filters) {
  const { sector, market, verdict } = filters || {};
  if (sector && sector !== "ALL" && security.sector !== sector) return false;
  if (market && market !== "ALL" && security.board !== market) return false;
  if (verdict && verdict !== "ALL" && security.verdict !== verdict) return false;
  return true;
}

export function filterByQuant(quantState, filters) {
  const { signal, minAttractiveness, maxAttractiveness } = filters || {};
  const signalActive = signal && signal !== "ALL";
  const rangeActive = typeof minAttractiveness === "number" || typeof maxAttractiveness === "number";
  if (!signalActive && !rangeActive) return true;
  if (!quantState || quantState.status !== "ok") return false;
  if (signalActive && quantState.signal !== signal) return false;
  if (typeof minAttractiveness === "number" && (quantState.attractiveness ?? -Infinity) < minAttractiveness) {
    return false;
  }
  if (typeof maxAttractiveness === "number" && (quantState.attractiveness ?? Infinity) > maxAttractiveness) {
    return false;
  }
  return true;
}

export function publicationBannerState(activePublication) {
  if (!activePublication) {
    return {
      state: "none",
      headline: "NO ACTIVE SHARIAH PUBLICATION",
      detail:
        "The current SC Malaysia publication has not been approved and activated. Authoritative PASS cannot currently be established.",
    };
  }
  return {
    state: "active",
    id: activePublication.id,
    publicationDate: activePublication.publication_date,
    activatedAt: activePublication.activated_at,
  };
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

export function freshnessFromAsOfDate(asOfDate, todayIso, staleDays = 5) {
  if (!asOfDate || !todayIso) {
    return { label: "no data", isStale: true };
  }
  const asOf = new Date(`${asOfDate}T00:00:00Z`);
  const today = new Date(`${todayIso}T00:00:00Z`);
  const days = Math.round((today.getTime() - asOf.getTime()) / MS_PER_DAY);
  if (Number.isNaN(days)) {
    return { label: "no data", isStale: true };
  }
  if (days <= 0) {
    return { label: "as of today", isStale: false };
  }
  const isStale = days > staleDays;
  return { label: `${days}d old${isStale ? " — STALE" : ""}`, isStale };
}

export function safeQuantState(raw, error) {
  if (error || !raw || !raw.quant) {
    return { status: "error", signal: null, strategyId: null, asOfDate: null, attractiveness: null, components: null };
  }
  return {
    status: "ok",
    signal: raw.quant.signal ?? null,
    strategyId: raw.quant.strategy_id ?? null,
    asOfDate: raw.quant.as_of_date ?? null,
    attractiveness: raw.attractiveness?.attractiveness ?? null,
    components: raw.attractiveness?.components ?? null,
  };
}

export function distinctValues(securities, field) {
  const values = new Set();
  for (const s of securities) {
    if (s[field]) values.add(s[field]);
  }
  return Array.from(values).sort();
}
```

- [ ] **Step 3: Write `dashboard/screening/tests/logic.test.js`**

```js
import test from "node:test";
import assert from "node:assert/strict";
import {
  buildUniverseQuery,
  statusBadge,
  tradingStatusFromShariah,
  matchesSearch,
  filterBySelects,
  filterByQuant,
  publicationBannerState,
  freshnessFromAsOfDate,
  safeQuantState,
  distinctValues,
  formatPct,
  formatScore,
} from "../logic.js";

test("buildUniverseQuery defaults to the backward-compatible PASS filter", () => {
  const query = buildUniverseQuery();
  assert.match(query, /shariah_status=PASS/);
});

test("buildUniverseQuery carries through an explicit filter", () => {
  const query = buildUniverseQuery({ shariahStatus: "ALL", limit: 500, offset: 10 });
  assert.match(query, /shariah_status=ALL/);
  assert.match(query, /limit=500/);
  assert.match(query, /offset=10/);
});

test("statusBadge passes PASS/REJECT/UNKNOWN through unchanged", () => {
  assert.equal(statusBadge("PASS").label, "PASS");
  assert.equal(statusBadge("REJECT").label, "REJECT");
  assert.equal(statusBadge("UNKNOWN").label, "UNKNOWN");
});

test("statusBadge never turns REJECT into UNKNOWN or vice versa", () => {
  assert.notEqual(statusBadge("REJECT").label, "UNKNOWN");
  assert.notEqual(statusBadge("UNKNOWN").label, "REJECT");
});

test("statusBadge fails closed to UNKNOWN on a missing/unexpected status", () => {
  assert.equal(statusBadge(undefined).label, "UNKNOWN");
  assert.equal(statusBadge("").label, "UNKNOWN");
  assert.equal(statusBadge("garbage").label, "UNKNOWN");
});

test("tradingStatusFromShariah blocks REJECT and UNKNOWN but not PASS", () => {
  assert.equal(tradingStatusFromShariah("PASS"), null);
  assert.equal(tradingStatusFromShariah("REJECT").label, "BLOCKED");
  assert.equal(tradingStatusFromShariah("UNKNOWN").label, "BLOCKED");
});

test("tradingStatusFromShariah fails closed to BLOCKED on a missing status", () => {
  assert.equal(tradingStatusFromShariah(undefined).label, "BLOCKED");
});

test("search matches ticker and issuer name case-insensitively", () => {
  const sec = { ticker: "1155", issuer_name: "Malayan Banking Bhd" };
  assert.equal(matchesSearch(sec, "1155"), true);
  assert.equal(matchesSearch(sec, "maybank"), false);
  assert.equal(matchesSearch(sec, "malayan"), true);
  assert.equal(matchesSearch(sec, ""), true);
});

test("filterBySelects filters by sector, market, and verdict independently", () => {
  const sec = { sector: "Healthcare", board: "MAIN MARKET", verdict: "PASS" };
  assert.equal(filterBySelects(sec, { sector: "Healthcare" }), true);
  assert.equal(filterBySelects(sec, { sector: "Finance" }), false);
  assert.equal(filterBySelects(sec, { market: "MAIN MARKET" }), true);
  assert.equal(filterBySelects(sec, { market: "ACE MARKET" }), false);
  assert.equal(filterBySelects(sec, { verdict: "REJECT" }), false);
  assert.equal(filterBySelects(sec, { verdict: "ALL" }), true);
});

test("filterByQuant excludes rows whose quant has not loaded yet when a quant filter is active", () => {
  assert.equal(filterByQuant(null, { signal: "BUY" }), false);
  assert.equal(filterByQuant({ status: "error" }, { signal: "BUY" }), false);
  assert.equal(
    filterByQuant({ status: "ok", signal: "BUY", attractiveness: 0.7 }, { signal: "BUY" }),
    true,
  );
});

test("filterByQuant is a no-op when no quant filter is active, even before quant loads", () => {
  assert.equal(filterByQuant(null, {}), true);
});

test("publicationBannerState reports the explicit no-active-publication state", () => {
  const state = publicationBannerState(null);
  assert.equal(state.state, "none");
  assert.match(state.headline, /NO ACTIVE/);
});

test("publicationBannerState reports the active publication's identity", () => {
  const state = publicationBannerState({
    id: "sc-sac-my-2026-05-29",
    publication_date: "2026-05-29",
    activated_at: "2026-06-01T00:00:00Z",
  });
  assert.equal(state.state, "active");
  assert.equal(state.id, "sc-sac-my-2026-05-29");
});

test("freshnessFromAsOfDate flags data older than the stale threshold", () => {
  const fresh = freshnessFromAsOfDate("2026-09-07", "2026-09-08");
  assert.equal(fresh.isStale, false);
  const stale = freshnessFromAsOfDate("2026-08-01", "2026-09-08");
  assert.equal(stale.isStale, true);
});

test("freshnessFromAsOfDate treats a missing as-of date as stale, not fresh", () => {
  assert.equal(freshnessFromAsOfDate(null, "2026-09-08").isStale, true);
});

test("safeQuantState never turns a fetch error into an optimistic BUY/eligible value", () => {
  const errored = safeQuantState(null, new Error("network down"));
  assert.equal(errored.status, "error");
  assert.equal(errored.signal, null);
  assert.equal(errored.attractiveness, null);
});

test("safeQuantState normalizes a successful /api/quant response", () => {
  const ok = safeQuantState(
    {
      ticker: "1155",
      quant: { signal: "BUY", strategy_id: "S001", as_of_date: "2026-09-07" },
      attractiveness: {
        attractiveness: 0.62,
        components: { technical: 0.7, volume: 0.5, risk_headroom: 0.5 },
      },
    },
    null,
  );
  assert.equal(ok.status, "ok");
  assert.equal(ok.signal, "BUY");
  assert.equal(ok.attractiveness, 0.62);
});

test("distinctValues derives filter options from real loaded data, not a hardcoded list", () => {
  const securities = [{ sector: "Healthcare" }, { sector: "Finance" }, { sector: "Healthcare" }, { sector: null }];
  assert.deepEqual(distinctValues(securities, "sector"), ["Finance", "Healthcare"]);
});

test("formatPct and formatScore render missing values as an em dash, never 0 or a false status", () => {
  assert.equal(formatPct(null), "—");
  assert.equal(formatPct(undefined), "—");
  assert.equal(formatScore(NaN), "—");
});
```

- [ ] **Step 4: Run the tests**

Run (from repo root): `node --test dashboard/screening/tests/logic.test.js`
Expected: all tests pass, 0 failures. If it instead fails with `SyntaxError: Unexpected token 'export'`, `package.json` from Step 1 is missing or misplaced (it must sit at `dashboard/screening/package.json`, the nearest ancestor of both `logic.js` and `tests/logic.test.js`). (This is also the first run — there's no separate "confirm it fails first" step here since `logic.js` and its test are written together as one deliverable; each `test()` block is still a real assertion against real behavior, not a placeholder.)

- [ ] **Step 5: Commit**

```bash
git add dashboard/screening/package.json dashboard/screening/logic.js dashboard/screening/tests/logic.test.js
git commit -m "Add pure screening-dashboard logic module with node:test coverage

Status formatting, filters, search, freshness, and safe fallbacks for
the Phase 2B dashboard -- unit tested independently of the DOM/fetch
layers built in later tasks.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfPiJ5h8bmJNBprhdG229a"
```

---

### Task 3: Frontend shared plumbing and universe page

**Files:**
- Create: `dashboard/screening/tokens.css`
- Create: `dashboard/screening/render.js`
- Create: `dashboard/screening/api.js`
- Create: `dashboard/screening/index.html`

**Interfaces:**
- Consumes: everything exported by `logic.js` (Task 2).
- Produces:
  - `api.js`: `apiBase()`, `fetchUniverse({shariahStatus, limit, offset})`, `fetchQuant(ticker)`, `fetchScreen(ticker)`, `fetchPublicationDetail(publicationId)`, `fetchRiskLimits()`, `fetchEvidence(ticker, limit)` — each returns a `Promise` of parsed JSON, throws on non-2xx.
  - `render.js`: `initTheme()`, `wireThemeToggle()`, `setTheme(theme)`, `createBadge(badgeInfo)`, `createStateMessage(kind, text)`, `createPublicationBanner(bannerState)`, `createUniverseTable(securities)` → `{table, rowsByTicker}`, `updateRowQuant(row, quantState, freshness)`, `createSection(title, bodyNode)`, `createKeyValueList(pairs)`, `createTradingStatusBanner(tradingStatus)`, `createDecisionCard(decision)` — used here and by Task 4.
  - `index.html`: the universe screen at `dashboard/screening/index.html`, linking to `security.html?ticker=<ticker>` (built in Task 4 — the link is written now, the target page doesn't exist until Task 4 runs, which is fine since this task's manual verification only exercises the universe page itself).

- [ ] **Step 1: Write `dashboard/screening/tokens.css`**

```css
:root {
  color-scheme: dark;
  --bg: #0b0f1a;
  --bg-soft: #0f1526;
  --panel: #131a2c;
  --panel-2: #182036;
  --text: #eef1f8;
  --muted: #99a3bd;
  --subtle: #6b7794;
  --border: #263153;
  --border-strong: #35447a;
  --accent: #b8925a;
  --accent-2: #d1ab7c;
  --button-text: #1b1206;
  --ok: #5fae86;
  --ok-bg: rgba(95, 174, 134, 0.14);
  --warn: #e3b341;
  --warn-bg: rgba(227, 179, 65, 0.14);
  --bad: #e0685c;
  --bad-bg: rgba(224, 104, 92, 0.14);
  --radius: 8px;
  --font-sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --font-mono: ui-monospace, "Cascadia Code", "SF Mono", Menlo, Consolas, "Roboto Mono", monospace;
}

:root[data-theme="light"] {
  color-scheme: light;
  --bg: #f4f6f9;
  --bg-soft: #eef1f6;
  --panel: #ffffff;
  --panel-2: #f6f8fb;
  --text: #16202f;
  --muted: #5b6779;
  --subtle: #808da0;
  --border: #d8dee8;
  --border-strong: #b9c3d3;
  --accent: #7c5b2e;
  --accent-2: #61451f;
  --button-text: #ffffff;
  --ok: #2f8f63;
  --ok-bg: rgba(47, 143, 99, 0.1);
  --warn: #c07f1f;
  --warn-bg: rgba(192, 127, 31, 0.12);
  --bad: #c2483c;
  --bad-bg: rgba(194, 72, 60, 0.1);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  font-family: var(--font-sans);
  font-size: 14px;
  background: var(--bg);
  color: var(--text);
}

a {
  color: var(--accent-2);
}

:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35em;
  padding: 0.15em 0.6em;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.02em;
  border: 1px solid transparent;
}

.badge--ok {
  color: var(--ok);
  background: var(--ok-bg);
  border-color: var(--ok);
}

.badge--warn {
  color: var(--warn);
  background: var(--warn-bg);
  border-color: var(--warn);
}

.badge--bad {
  color: var(--bad);
  background: var(--bad-bg);
  border-color: var(--bad);
}

.badge--ok::before {
  content: "✓ ";
}

.badge--warn::before {
  content: "? ";
}

.badge--bad::before {
  content: "✕ ";
}

.state-message {
  padding: 1rem 1.25rem;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--panel);
  color: var(--muted);
}

.state-message--error {
  color: var(--bad);
  background: var(--bad-bg);
  border-color: var(--bad);
}

.state-message--none {
  color: var(--warn);
  background: var(--warn-bg);
  border-color: var(--warn);
}

button[data-theme-toggle] {
  font: inherit;
  padding: 0.4em 0.9em;
  border-radius: var(--radius);
  border: 1px solid var(--border-strong);
  background: var(--panel-2);
  color: var(--text);
  cursor: pointer;
}
```

- [ ] **Step 2: Write `dashboard/screening/render.js`**

```js
// render.js
// DOM-painting helpers shared by index.html and security.html. Pure
// business logic (status derivation, filtering, formatting) lives in
// logic.js and is passed in here already computed -- this file only
// builds and updates DOM nodes.

import { formatDate, formatScore } from "./logic.js";

const THEME_KEY = "amanah-theme"; // shared with the legacy dashboard/index.html

export function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const toggle = document.querySelector("[data-theme-toggle]");
  if (toggle) {
    toggle.textContent = theme === "dark" ? "Light Mode" : "Dark Mode";
    toggle.setAttribute("aria-pressed", String(theme === "dark"));
  }
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // best-effort persistence only
  }
}

export function initTheme() {
  let stored = null;
  try {
    stored = window.localStorage.getItem(THEME_KEY);
  } catch {
    // localStorage unavailable -- default to dark, same as the legacy dashboard
  }
  setTheme(stored === "light" ? "light" : "dark");
}

export function wireThemeToggle() {
  const toggle = document.querySelector("[data-theme-toggle]");
  if (!toggle) return;
  toggle.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    setTheme(current === "dark" ? "light" : "dark");
  });
}

export function createBadge(badgeInfo) {
  const span = document.createElement("span");
  span.className = badgeInfo.className;
  span.textContent = badgeInfo.label;
  span.title = badgeInfo.explanation;
  return span;
}

export function createStateMessage(kind, text) {
  const div = document.createElement("div");
  div.className = `state-message state-message--${kind}`;
  div.setAttribute("role", kind === "error" ? "alert" : "status");
  div.textContent = text;
  return div;
}

export function createPublicationBanner(bannerState) {
  const section = document.createElement("section");
  section.className = "publication-banner";
  section.setAttribute("aria-label", "Shariah authority");

  const heading = document.createElement("h2");
  heading.textContent = "Shariah Authority";
  section.appendChild(heading);

  if (bannerState.state === "none") {
    section.classList.add("publication-banner--none");
    const status = document.createElement("p");
    status.className = "publication-banner__status";
    status.textContent = bannerState.headline;
    const detail = document.createElement("p");
    detail.className = "publication-banner__detail";
    detail.textContent = bannerState.detail;
    section.append(status, detail);
    return section;
  }

  section.classList.add("publication-banner--active");
  const status = document.createElement("p");
  status.className = "publication-banner__status";
  status.textContent = "ACTIVE";
  const id = document.createElement("p");
  id.textContent = `Publication: ${bannerState.id}`;
  const date = document.createElement("p");
  date.textContent = `Publication date: ${formatDate(bannerState.publicationDate)}`;
  const activated = document.createElement("p");
  activated.textContent = `Activated: ${formatDate(bannerState.activatedAt)}`;
  section.append(status, id, date, activated);
  return section;
}

const TABLE_COLUMNS = [
  "Ticker",
  "Issuer",
  "Market",
  "Sector",
  "Shariah",
  "Publication date",
  "Quant signal",
  "Attractiveness",
  "Risk",
  "As of",
];

export function createUniverseTable(securities) {
  const table = document.createElement("table");
  table.className = "universe-table";

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  for (const col of TABLE_COLUMNS) {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = col;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  const rowsByTicker = new Map();

  for (const security of securities) {
    const tr = document.createElement("tr");
    tr.dataset.ticker = security.ticker;

    const tickerCell = document.createElement("th");
    tickerCell.scope = "row";
    const link = document.createElement("a");
    link.href = `./security.html?ticker=${encodeURIComponent(security.ticker)}`;
    link.textContent = security.ticker;
    tickerCell.appendChild(link);

    const issuerCell = document.createElement("td");
    issuerCell.textContent = security.issuer_name || "—";

    const marketCell = document.createElement("td");
    marketCell.textContent = security.board || "—";

    const sectorCell = document.createElement("td");
    sectorCell.textContent = security.sector || "—";

    const shariahCell = document.createElement("td");
    shariahCell.appendChild(
      createBadge({
        label: security.verdict,
        className: security.verdict === "PASS" ? "badge badge--ok" : "badge badge--bad",
        explanation:
          security.verdict === "PASS"
            ? "Authoritative compliant record in the active SC publication."
            : "Authoritative non-compliant record in the active SC publication.",
      }),
    );

    const pubDateCell = document.createElement("td");
    pubDateCell.textContent = formatDate(security.publicationDate);

    const signalCell = document.createElement("td");
    signalCell.textContent = "loading…";
    signalCell.dataset.role = "quant-signal";

    const attractivenessCell = document.createElement("td");
    attractivenessCell.textContent = "loading…";
    attractivenessCell.dataset.role = "attractiveness";

    const riskCell = document.createElement("td");
    riskCell.textContent = "No per-trade risk context";
    riskCell.title =
      "A risk verdict requires order-specific inputs (position size, existing exposure) only available when previewing an order, which this read-only dashboard does not do.";

    const asOfCell = document.createElement("td");
    asOfCell.textContent = "loading…";
    asOfCell.dataset.role = "as-of";

    tr.append(
      tickerCell,
      issuerCell,
      marketCell,
      sectorCell,
      shariahCell,
      pubDateCell,
      signalCell,
      attractivenessCell,
      riskCell,
      asOfCell,
    );
    tbody.appendChild(tr);
    rowsByTicker.set(security.ticker, tr);
  }

  table.appendChild(tbody);
  return { table, rowsByTicker };
}

export function updateRowQuant(row, quantState, freshness) {
  const signalCell = row.querySelector('[data-role="quant-signal"]');
  const attractivenessCell = row.querySelector('[data-role="attractiveness"]');
  const asOfCell = row.querySelector('[data-role="as-of"]');

  if (quantState.status !== "ok") {
    signalCell.textContent = "—";
    attractivenessCell.textContent = "—";
    asOfCell.textContent = "—";
    return;
  }

  signalCell.textContent = quantState.signal || "—";
  attractivenessCell.textContent = formatScore(quantState.attractiveness);
  asOfCell.textContent = `${formatDate(quantState.asOfDate)}${freshness.isStale ? " (stale)" : ""}`;
  if (freshness.isStale) {
    asOfCell.classList.add("is-stale");
  }
}

export function createSection(title, bodyNode) {
  const section = document.createElement("section");
  section.className = "detail-section";
  const heading = document.createElement("h2");
  heading.textContent = title;
  section.append(heading, bodyNode);
  return section;
}

export function createKeyValueList(pairs) {
  const dl = document.createElement("dl");
  dl.className = "kv-list";
  for (const [label, value] of pairs) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value === null || value === undefined || value === "" ? "—" : String(value);
    dl.append(dt, dd);
  }
  return dl;
}

export function createTradingStatusBanner(tradingStatus) {
  if (!tradingStatus) return null;
  const div = document.createElement("div");
  div.className = "trading-status-banner";
  div.setAttribute("role", "alert");
  const label = document.createElement("p");
  label.className = "trading-status-banner__label";
  label.textContent = `TRADING STATUS: ${tradingStatus.label}`;
  const reason = document.createElement("p");
  reason.textContent = `Reason: ${tradingStatus.reason}`;
  div.append(label, reason);
  return div;
}

export function createDecisionCard(decision) {
  const card = document.createElement("article");
  card.className = "decision-card";
  const pairs = [
    ["Decision", decision.decision ?? decision.final_decision],
    ["Reason", decision.decision_reason],
    ["Timestamp", decision.timestamp],
    ["Shariah status", decision.shariah?.status],
    ["Quant signal", decision.quant?.signal],
    ["Risk status", decision.risk?.status],
  ];
  card.appendChild(createKeyValueList(pairs));
  return card;
}
```

- [ ] **Step 3: Write `dashboard/screening/api.js`**

```js
// api.js
// Thin fetch client for the read-only Malaysian screening API. Each
// function maps to exactly one backend endpoint and returns parsed JSON.
// No retries, no fallback to an optimistic default on failure -- callers
// see the real error and render an explicit error state (logic.js's
// safeQuantState is where a per-row failure gets normalized for display).

import { buildUniverseQuery } from "./logic.js";

const DEFAULT_BASE = "http://127.0.0.1:8000";

export function apiBase() {
  try {
    const store = globalThis.localStorage;
    const stored = store && store.getItem("amanah-screening-api-base");
    if (stored) return stored.replace(/\/+$/, "");
  } catch {
    // localStorage unavailable -- fall through to the default
  }
  return DEFAULT_BASE;
}

async function getJson(path) {
  const response = await fetch(`${apiBase()}${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  return response.json();
}

export function fetchUniverse(options) {
  return getJson(`/api/universe?${buildUniverseQuery(options)}`);
}

export function fetchQuant(ticker) {
  return getJson(`/api/quant/${encodeURIComponent(ticker)}`);
}

export function fetchScreen(ticker) {
  return getJson(`/api/screen/${encodeURIComponent(ticker)}`);
}

export function fetchPublicationDetail(publicationId) {
  return getJson(`/api/shariah/publication/${encodeURIComponent(publicationId)}`);
}

export function fetchRiskLimits() {
  return getJson(`/api/risk`);
}

export function fetchEvidence(ticker, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  return getJson(`/api/evidence/${encodeURIComponent(ticker)}?${params.toString()}`);
}
```

- [ ] **Step 4: Write `dashboard/screening/index.html`**

```html
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shariah Screening — Amanah Trader</title>
  <link rel="stylesheet" href="./tokens.css">
  <style>
    .page { max-width: 1200px; margin: 0 auto; padding: 1.5rem; }
    header.app-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1.5rem; gap: 1rem; flex-wrap: wrap; }
    header.app-header h1 { font-size: 20px; margin: 0; }
    .publication-banner { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); padding: 1rem 1.25rem; margin-bottom: 1.5rem; }
    .publication-banner h2 { margin: 0 0 0.5rem; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
    .publication-banner p { margin: 0.15rem 0; }
    .publication-banner--none .publication-banner__status { color: var(--warn); font-weight: 700; }
    .publication-banner--active .publication-banner__status { color: var(--ok); font-weight: 700; }
    .controls { display: flex; flex-wrap: wrap; gap: 0.75rem; align-items: end; margin-bottom: 1rem; background: var(--panel); border: 1px solid var(--border); border-radius: var(--radius); padding: 1rem; }
    .controls label { display: flex; flex-direction: column; gap: 0.3rem; font-size: 12px; color: var(--muted); }
    .controls input, .controls select { font: inherit; padding: 0.4em 0.6em; border-radius: 6px; border: 1px solid var(--border-strong); background: var(--bg-soft); color: var(--text); }
    .table-wrap { overflow-x: auto; border: 1px solid var(--border); border-radius: var(--radius); }
    table.universe-table { border-collapse: collapse; width: 100%; font-size: 13px; }
    table.universe-table th, table.universe-table td { padding: 0.5em 0.75em; text-align: left; border-bottom: 1px solid var(--border); white-space: nowrap; }
    table.universe-table thead th { background: var(--panel-2); position: sticky; top: 0; }
    table.universe-table tbody tr:hover { background: var(--panel-2); }
    td.is-stale { color: var(--warn); }
    .result-note { margin: 0.75rem 0; color: var(--muted); font-size: 12px; }
    .quant-filter-hint { font-size: 11px; color: var(--subtle); max-width: 220px; }
  </style>
</head>
<body>
  <div class="page">
    <header class="app-header">
      <h1>Malaysian Shariah Screening</h1>
      <button type="button" data-theme-toggle aria-pressed="true">Light Mode</button>
    </header>

    <div id="publication-banner"></div>

    <form class="controls" id="controls" aria-label="Search and filters">
      <label>
        Search
        <input type="search" id="search" placeholder="Ticker or issuer name" aria-label="Search by ticker or issuer name">
      </label>
      <label>
        Shariah status
        <select id="shariah-status">
          <option value="PASS" selected>PASS (authoritative universe)</option>
          <option value="REJECT">REJECT</option>
          <option value="ALL">ALL (PASS + REJECT)</option>
        </select>
      </label>
      <label>
        Sector
        <select id="sector"><option value="ALL">All sectors</option></select>
      </label>
      <label>
        Market
        <select id="market"><option value="ALL">All markets</option></select>
      </label>
      <label>
        Quant signal
        <select id="signal">
          <option value="ALL">Any</option>
          <option value="BUY">BUY</option>
          <option value="NO_SIGNAL">NO_SIGNAL</option>
        </select>
      </label>
      <label>
        Min attractiveness
        <input type="number" id="min-attractiveness" min="0" max="1" step="0.05" placeholder="0.00">
      </label>
      <p class="quant-filter-hint">Quant/attractiveness filters apply only to rows currently shown on this page, since quant signals load per row, not in bulk.</p>
    </form>

    <div id="state"></div>
    <p class="result-note" id="result-note" hidden></p>
    <div class="table-wrap" id="table-wrap"></div>
  </div>

  <script type="module">
    import { fetchUniverse, fetchQuant } from "./api.js";
    import {
      publicationBannerState, matchesSearch, filterBySelects, filterByQuant,
      distinctValues, safeQuantState, freshnessFromAsOfDate,
    } from "./logic.js";
    import {
      initTheme, wireThemeToggle, createPublicationBanner, createStateMessage,
      createUniverseTable, updateRowQuant,
    } from "./render.js";

    const MAX_VISIBLE_ROWS = 50;

    const stateEl = document.getElementById("state");
    const tableWrap = document.getElementById("table-wrap");
    const resultNote = document.getElementById("result-note");
    const bannerEl = document.getElementById("publication-banner");
    const controls = document.getElementById("controls");
    const searchInput = document.getElementById("search");
    const shariahSelect = document.getElementById("shariah-status");
    const sectorSelect = document.getElementById("sector");
    const marketSelect = document.getElementById("market");
    const signalSelect = document.getElementById("signal");
    const minAttractivenessInput = document.getElementById("min-attractiveness");

    let allSecurities = [];
    const quantCache = new Map();

    initTheme();
    wireThemeToggle();

    function setState(node) {
      stateEl.replaceChildren();
      if (node) stateEl.appendChild(node);
    }

    function populateFilterOptions() {
      for (const value of distinctValues(allSecurities, "sector")) {
        sectorSelect.appendChild(new Option(value, value));
      }
      for (const value of distinctValues(allSecurities, "board")) {
        marketSelect.appendChild(new Option(value, value));
      }
    }

    function currentFilters() {
      const min = minAttractivenessInput.value === "" ? undefined : Number(minAttractivenessInput.value);
      return {
        sector: sectorSelect.value,
        market: marketSelect.value,
        signal: signalSelect.value,
        minAttractiveness: min,
      };
    }

    async function loadQuantFor(row, ticker) {
      let quantState = quantCache.get(ticker);
      if (!quantState) {
        try {
          const raw = await fetchQuant(ticker);
          quantState = safeQuantState(raw, null);
        } catch (error) {
          quantState = safeQuantState(null, error);
        }
        quantCache.set(ticker, quantState);
      }
      const freshness = quantState.status === "ok"
        ? freshnessFromAsOfDate(quantState.asOfDate, new Date().toISOString().slice(0, 10))
        : { label: "—", isStale: false };
      updateRowQuant(row, quantState, freshness);
      return quantState;
    }

    function renderTable() {
      const filters = currentFilters();
      const query = searchInput.value;

      const matched = allSecurities.filter((s) => matchesSearch(s, query) && filterBySelects(s, filters));
      const visible = matched.slice(0, MAX_VISIBLE_ROWS);

      resultNote.hidden = matched.length <= MAX_VISIBLE_ROWS;
      resultNote.textContent = `Showing ${visible.length} of ${matched.length} matches — refine your search or filters to narrow further.`;

      if (visible.length === 0) {
        tableWrap.replaceChildren();
        setState(createStateMessage("empty", "No securities match the current search and filters."));
        return;
      }
      setState(null);

      const { table, rowsByTicker } = createUniverseTable(visible);
      tableWrap.replaceChildren(table);

      const quantActive = filters.signal !== "ALL" || typeof filters.minAttractiveness === "number";

      for (const security of visible) {
        const row = rowsByTicker.get(security.ticker);
        loadQuantFor(row, security.ticker).then((quantState) => {
          if (quantActive && !filterByQuant(quantState, filters)) {
            row.remove();
          }
        });
      }
    }

    async function loadUniverse() {
      setState(createStateMessage("loading", "Loading the Shariah universe…"));
      tableWrap.replaceChildren();
      resultNote.hidden = true;

      let response;
      try {
        response = await fetchUniverse({ shariahStatus: shariahSelect.value, limit: 1000, offset: 0 });
      } catch (error) {
        setState(createStateMessage("error", `Could not load the universe: ${error.message}`));
        return;
      }

      bannerEl.replaceChildren(createPublicationBanner(publicationBannerState(response.active_publication)));

      if (!response.active_publication) {
        setState(createStateMessage("none", "No securities can be shown: there is no approved and activated SC Malaysia publication right now."));
        return;
      }

      allSecurities = response.securities.map((s) => ({
        ...s,
        publicationDate: response.active_publication.publication_date,
      }));
      quantCache.clear();
      sectorSelect.replaceChildren(new Option("All sectors", "ALL"));
      marketSelect.replaceChildren(new Option("All markets", "ALL"));
      populateFilterOptions();

      if (allSecurities.length === 0) {
        setState(createStateMessage("empty", "The active publication contains no securities matching this filter."));
        return;
      }

      renderTable();
    }

    let searchDebounce;
    searchInput.addEventListener("input", () => {
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(renderTable, 150);
    });
    for (const el of [sectorSelect, marketSelect, signalSelect, minAttractivenessInput]) {
      el.addEventListener("change", renderTable);
    }
    shariahSelect.addEventListener("change", loadUniverse);
    controls.addEventListener("submit", (e) => e.preventDefault());

    loadUniverse();
  </script>
</body>
</html>
```

- [ ] **Step 5: Re-run the Task 2 unit tests to confirm nothing in `logic.js` broke**

Run: `node --test dashboard/screening/tests/logic.test.js`
Expected: all tests still pass (this task only added files that import `logic.js`; it must not have needed to change it).

- [ ] **Step 6: Manual verification**

Start the backend from `backend/`: `../.venv/Scripts/python -m uvicorn local_api:app --host 127.0.0.1 --port 8000`
Serve the static files from the repo root in a second terminal: `python -m http.server 5500`
Open `http://127.0.0.1:5500/dashboard/screening/index.html` in a browser.
Expected, given the real current data (publication `sc-sac-my-2026-05-29` is `pending`): the banner reads "NO ACTIVE SHARIAH PUBLICATION" and the state area explains why — not an empty table with no explanation. Toggle light/dark mode and confirm it persists on reload. No console errors other than the expected 0-result state.

- [ ] **Step 7: Commit**

```bash
git add dashboard/screening/tokens.css dashboard/screening/render.js dashboard/screening/api.js dashboard/screening/index.html
git commit -m "Add the Malaysian Shariah screening universe page

Static, no-build dashboard/screening/index.html plus its shared
render.js/api.js/tokens.css. Publication-state, loading, empty, and
error states are all explicit; quant/attractiveness load lazily per
visible row since /api/universe carries neither.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfPiJ5h8bmJNBprhdG229a"
```

---

### Task 4: Frontend security detail page

**Files:**
- Create: `dashboard/screening/security.html`

**Interfaces:**
- Consumes: `fetchScreen`, `fetchPublicationDetail`, `fetchRiskLimits`, `fetchEvidence` from `api.js`; `statusBadge`, `tradingStatusFromShariah`, `formatDate`, `formatPct`, `formatScore` from `logic.js`; `initTheme`, `wireThemeToggle`, `createBadge`, `createStateMessage`, `createSection`, `createKeyValueList`, `createTradingStatusBanner`, `createDecisionCard` from `render.js` (all produced in Tasks 2-3).
- Produces: the page linked from every table row in `index.html` (`./security.html?ticker=<ticker>`).

- [ ] **Step 1: Write `dashboard/screening/security.html`**

```html
<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Security Detail — Amanah Trader</title>
  <link rel="stylesheet" href="./tokens.css">
  <style>
    .page { max-width: 900px; margin: 0 auto; padding: 1.5rem; }
    header.app-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1rem; gap: 1rem; flex-wrap: wrap; }
    header.app-header h1 { font-size: 20px; margin: 0; }
    a.back-link { display: inline-block; margin-bottom: 1rem; }
    .identity { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); padding: 1rem 1.25rem; margin-bottom: 1rem; }
    .identity h2 { margin: 0 0 0.25rem; font-size: 22px; }
    .identity p { margin: 0.15rem 0; color: var(--muted); }
    .trading-status-banner { border: 1px solid var(--bad); background: var(--bad-bg); color: var(--bad); border-radius: var(--radius); padding: 1rem 1.25rem; margin-bottom: 1rem; font-weight: 600; }
    .trading-status-banner p { margin: 0.15rem 0; }
    .detail-section { border: 1px solid var(--border); border-radius: var(--radius); background: var(--panel); padding: 1rem 1.25rem; margin-bottom: 1rem; }
    .detail-section h2 { margin: 0 0 0.75rem; font-size: 13px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }
    dl.kv-list { display: grid; grid-template-columns: max-content 1fr; gap: 0.35rem 1rem; margin: 0; }
    dl.kv-list dt { color: var(--muted); }
    dl.kv-list dd { margin: 0; font-family: var(--font-mono); }
    .decision-card { border-top: 1px solid var(--border); padding-top: 0.75rem; margin-top: 0.75rem; }
    .decision-card:first-child { border-top: none; padding-top: 0; margin-top: 0; }
  </style>
</head>
<body>
  <div class="page">
    <header class="app-header">
      <h1>Security Detail</h1>
      <button type="button" data-theme-toggle aria-pressed="true">Light Mode</button>
    </header>
    <a class="back-link" href="./index.html">&larr; Back to universe</a>

    <div id="state"></div>
    <div id="content" hidden>
      <div class="identity" id="identity"></div>
      <div id="trading-status"></div>
      <div id="shariah-section"></div>
      <div id="quant-section"></div>
      <div id="risk-section"></div>
      <div id="evidence-section"></div>
    </div>
  </div>

  <script type="module">
    import { fetchScreen, fetchPublicationDetail, fetchRiskLimits, fetchEvidence } from "./api.js";
    import { statusBadge, tradingStatusFromShariah, formatDate, formatPct, formatScore } from "./logic.js";
    import {
      initTheme, wireThemeToggle, createBadge, createStateMessage, createSection,
      createKeyValueList, createTradingStatusBanner, createDecisionCard,
    } from "./render.js";

    initTheme();
    wireThemeToggle();

    const stateEl = document.getElementById("state");
    const contentEl = document.getElementById("content");

    function setState(node) {
      stateEl.replaceChildren();
      if (node) {
        stateEl.appendChild(node);
        contentEl.hidden = true;
      } else {
        contentEl.hidden = false;
      }
    }

    function renderIdentity(screen) {
      const el = document.getElementById("identity");
      el.replaceChildren();
      const heading = document.createElement("h2");
      heading.textContent = `${screen.shariah.issuer_name || screen.ticker} (${screen.ticker})`;
      const market = document.createElement("p");
      market.textContent = `Market: ${screen.shariah.board || "—"}  ·  Sector: ${screen.shariah.sector || "—"}`;
      el.append(heading, market);
    }

    function renderShariah(screen, publicationDetail) {
      const body = document.createElement("div");
      const badgeRow = document.createElement("p");
      badgeRow.appendChild(createBadge(statusBadge(screen.shariah.status)));
      body.appendChild(badgeRow);
      const pairs = [
        ["Reason code", screen.shariah.reason_code],
        ["Publication ID", screen.shariah.publication_id],
        ["Publication date", formatDate(screen.shariah.publication_date)],
        ["Source document hash", screen.shariah.source_document_hash],
      ];
      if (publicationDetail) {
        pairs.push(
          ["Publication review status", publicationDetail.publication?.human_review_status],
          ["Securities in publication", publicationDetail.security_count],
          ["Compliant in publication", publicationDetail.compliant_count],
          ["Non-compliant in publication", publicationDetail.non_compliant_count],
        );
      }
      body.appendChild(createKeyValueList(pairs));
      if (screen.shariah.status === "UNKNOWN") {
        const note = document.createElement("p");
        note.textContent = "The security is not currently established as eligible by an approved and activated authoritative publication. This is not the same as non-compliant.";
        body.appendChild(note);
      }
      document.getElementById("shariah-section").replaceChildren(createSection("Shariah", body));
    }

    function renderQuant(screen) {
      const body = createKeyValueList([
        ["Signal", screen.quant.signal],
        ["Reason code", screen.quant.reason_code],
        ["Strategy ID", screen.quant.strategy_id],
        ["As-of date", formatDate(screen.quant.as_of_date)],
        ["Price", screen.quant.price],
        ["Price source", screen.quant.price_source],
        ["Attractiveness", formatScore(screen.attractiveness.attractiveness)],
        ["Technical component", formatScore(screen.attractiveness.components.technical)],
        ["Volume component", formatScore(screen.attractiveness.components.volume)],
        ["Risk-headroom component", formatScore(screen.attractiveness.components.risk_headroom)],
      ]);
      document.getElementById("quant-section").replaceChildren(createSection("Quant", body));
    }

    function renderRisk(riskLimits) {
      const limits = riskLimits.limits;
      const body = createKeyValueList([
        ["Max position %", formatPct(limits.max_position_pct)],
        ["Max total exposure %", formatPct(limits.max_total_exposure_pct)],
        ["Max sector exposure %", formatPct(limits.max_sector_exposure_pct)],
        ["Max loss per trade %", formatPct(limits.max_loss_per_trade_pct)],
        ["Max daily loss %", formatPct(limits.max_daily_loss_pct)],
        ["Max weekly loss %", formatPct(limits.max_weekly_loss_pct)],
        ["Max orders per day", limits.max_orders_per_day],
      ]);
      const note = document.createElement("p");
      note.textContent = "These are the currently configured policy limits, not a per-trade verdict for this security. A risk PASS/REJECT requires order-specific inputs (position size, existing exposure) only available when previewing an order.";
      const wrapper = document.createElement("div");
      wrapper.append(body, note);
      document.getElementById("risk-section").replaceChildren(createSection("Risk", wrapper));
    }

    function renderEvidence(evidence) {
      const wrapper = document.createElement("div");
      if (evidence.decisions.length === 0) {
        wrapper.appendChild(createStateMessage("empty", "No recorded decisions for this ticker yet."));
      } else {
        for (const decision of evidence.decisions) {
          wrapper.appendChild(createDecisionCard(decision));
        }
      }
      document.getElementById("evidence-section").replaceChildren(createSection("Evidence", wrapper));
    }

    async function load() {
      const params = new URLSearchParams(window.location.search);
      const ticker = params.get("ticker");
      if (!ticker) {
        setState(createStateMessage("error", "No ticker specified. Use security.html?ticker=1155."));
        return;
      }

      setState(createStateMessage("loading", `Loading ${ticker}…`));

      let screen;
      try {
        screen = await fetchScreen(ticker);
      } catch (error) {
        setState(createStateMessage("error", `Could not load ${ticker}: ${error.message}`));
        return;
      }

      setState(null);
      renderIdentity(screen);

      const tradingStatus = tradingStatusFromShariah(screen.shariah.status);
      document.getElementById("trading-status").replaceChildren(
        ...(tradingStatus ? [createTradingStatusBanner(tradingStatus)] : []),
      );

      let publicationDetail = null;
      if (screen.shariah.publication_id) {
        try {
          publicationDetail = await fetchPublicationDetail(screen.shariah.publication_id);
        } catch {
          publicationDetail = null; // best-effort supplementary provenance, not required to render the page
        }
      }
      renderShariah(screen, publicationDetail);
      renderQuant(screen);

      try {
        const riskLimits = await fetchRiskLimits();
        renderRisk(riskLimits);
      } catch (error) {
        document.getElementById("risk-section").replaceChildren(createStateMessage("error", `Could not load risk limits: ${error.message}`));
      }

      try {
        const evidence = await fetchEvidence(ticker);
        renderEvidence(evidence);
      } catch (error) {
        document.getElementById("evidence-section").replaceChildren(createStateMessage("error", `Could not load evidence: ${error.message}`));
      }
    }

    load();
  </script>
</body>
</html>
```

- [ ] **Step 2: Manual verification**

With the backend and static server from Task 3, Step 6 still running, open:
- `http://127.0.0.1:5500/dashboard/screening/security.html` (no `?ticker=`) → expect the explicit "No ticker specified" error state.
- `http://127.0.0.1:5500/dashboard/screening/security.html?ticker=1155` → expect the page to load, Shariah section to show `UNKNOWN` (no active publication exists right now) with the "not the same as non-compliant" note, and a red "TRADING STATUS: BLOCKED — No authoritative PASS is currently established." banner above the sections. Quant and Risk sections render from real backend data (quant will show whatever `evaluate_quant` computes from live/cached market data for 1155; Risk shows the configured limits). Evidence section shows the empty state (no decisions have been recorded in this environment).
- Click "← Back to universe" and confirm it returns to `index.html`.
- From `index.html`, click a ticker link (switch the Shariah status filter to `ALL` first if the universe is empty) and confirm it navigates to the matching `security.html?ticker=...`.

- [ ] **Step 3: Commit**

```bash
git add dashboard/screening/security.html
git commit -m "Add the Malaysian Shariah screening security detail page

Identity, Shariah, Quant, Risk, and Evidence sections per ticker, with
an explicit BLOCKED banner whenever Shariah status isn't PASS so a
quant BUY signal can never visually override a Shariah block.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01HfPiJ5h8bmJNBprhdG229a"
```

---

### Task 5: Final verification, security review, and report

**Files:** none created; this task only runs checks and produces the final report as a chat message (not a new doc file, per Global Constraints' no-ceremony spirit — the spec and this plan are already the durable record).

**Interfaces:** none — this task consumes the finished state of Tasks 1-4.

- [ ] **Step 1: Run the full backend test suite**

From `backend/`, run every `test_*.py` except `test_moomoo.py` (documented in `docs/PHASE2A_REPORT.md` as a known hang — a live SDK dependency, unrelated to this work):

```bash
for f in test_*.py; do
  if [ "$f" = "test_moomoo.py" ]; then continue; fi
  echo "== $f =="
  timeout 60 ../.venv/Scripts/python "$f" || echo "FAILED: $f"
done
```

Expected: every file prints its `PASS:` lines with no `FAILED:` lines, including `test_screening_api.py` and the new `test_screening_api_universe_status.py`.

- [ ] **Step 2: Run the frontend unit tests**

Run: `node --test dashboard/screening/tests/logic.test.js`
Expected: all tests pass.

- [ ] **Step 3: Ruff check**

Run: `../.venv/Scripts/python -m ruff check backend`
Expected: no new findings in the files this plan touched (`screening_api.py`, `local_api.py`, `test_screening_api_universe_status.py`); pre-existing repo-wide findings are out of scope (see `pyproject.toml`'s comment on this).

- [ ] **Step 4: Application boot check**

```bash
cd backend
../.venv/Scripts/python -m uvicorn local_api:app --host 127.0.0.1 --port 8000 &
sleep 2
curl -s http://127.0.0.1:8000/health
curl -s "http://127.0.0.1:8000/api/universe?shariah_status=ALL"
kill %1
```

Expected: `/health` returns `{"status": "ok", ...}`; `/api/universe?shariah_status=ALL` returns HTTP 200 with `"active_publication": null, "count": 0, "securities": []` (the real publication is still pending).

- [ ] **Step 5: Manual browser walkthrough (repeat/extend Task 3 Step 6 and Task 4 Step 2)**

Confirm: universe loads; search narrows results; Shariah status/sector/market/signal/min-attractiveness filters all narrow results; switching Shariah status to `REJECT` or `ALL` shows non-compliant rows once a publication is active (see Step 7 below for a temporary local-only activation to verify this, then revert); detail page renders all five sections; PASS/REJECT/UNKNOWN render with distinct visual treatment (not color-only — icon glyphs `✓ / ✕ / ?` plus text label); the no-active-publication state explains itself; light/dark toggle works and persists; resizing the browser to a narrow width keeps the page usable (table scrolls horizontally in its own container, not the whole page); triggering an API error (stop the backend, reload) shows the explicit error state, not an optimistic PASS/BUY.

- [ ] **Step 6: Confirm the legacy dashboard is untouched**

Run: `git diff --stat -- dashboard/index.html`
Expected: no output (empty diff).

- [ ] **Step 7: Confirm the real publication's governance state, using a throwaway local DB copy to verify the REJECT/ALL filters against real mixed data (not the production DB)**

```bash
cd backend
../.venv/Scripts/python -c "
import sc_malaysia_store as s
conn = s.connect_default()
pub = [p for p in s.list_publications(conn) if p['id'] == 'sc-sac-my-2026-05-29'][0]
assert pub['human_review_status'] == 'pending', pub
assert pub['approved_at'] is None, pub
assert pub['activated_at'] is None, pub
print('CONFIRMED: sc-sac-my-2026-05-29 remains pending / not approved / not activated')
"
```

Expected: prints the `CONFIRMED:` line. If this ever fails, stop and investigate immediately — nothing in this plan should have touched this row, and Step 5's optional local verification must never run against `backend/sc_malaysia.db` / `backend/paper_trading.db` directly (use a copied/temp DB, exactly as `test_screening_api_universe_status.py` does with its own `DB_PATH`).

- [ ] **Step 8: Confirm no mutation surface exists**

Run:
```bash
cd backend
../.venv/Scripts/python -c "
from local_api import app
for route in app.routes:
    path = getattr(route, 'path', '')
    methods = getattr(route, 'methods', set()) or set()
    if path.startswith('/api/') and not methods <= {'GET', 'HEAD'}:
        raise SystemExit(f'MUTATION ROUTE FOUND: {path} {methods}')
print('CONFIRMED: every /api/* route is GET-only')
"
grep -rn "approve\|activate\|reject\|deactivate\|execute\|cancel" dashboard/screening/*.js dashboard/screening/*.html || echo "CONFIRMED: no mutation verbs referenced in the dashboard source"
```

Expected: both `CONFIRMED:` lines print (the `grep` is expected to find nothing — its own `|| echo` only fires when that's true; if it does find a match, inspect it, since this dashboard must never reference those operations).

- [ ] **Step 9: Confirm no secrets are present**

Run: `grep -rniE "api[_-]?key|secret|password|token|bearer" dashboard/screening/*.js dashboard/screening/*.html`
Expected: no matches (empty output). The only "token" concept in this codebase is CSS design tokens in `tokens.css`, which contains no credential-shaped strings — confirm by inspection if the grep is too broad.

- [ ] **Step 10: Write the final report**

Post a chat message (not a new file) covering, in order: frontend architecture; files created; the one API change (with its exact contract); dashboard capabilities; security model; Shariah UX; quant UX; risk UX; evidence/provenance UX; publication-state UX; tests added; backend test result (Step 1); frontend test result (Step 2); lint/type/syntax result (Step 3); browser verification result (Step 5); confirmation `dashboard/index.html` is untouched (Step 6); confirmation no mutation endpoints exist (Step 8); confirmation no secrets are exposed (Step 9); confirmation `sc-sac-my-2026-05-29` remains pending (Step 7); remaining findings (e.g., the evidence store is currently empty in this environment since nothing has written to it yet — the empty state is legitimate, not a bug); and end with exactly one of `READY FOR COPILOT` or `NOT READY FOR COPILOT`, justified by whether Steps 1-9 all actually passed — not merely by whether the UI renders.

---

## Self-Review Notes

- **Spec coverage**: every numbered section of the approved spec maps to a task above — hard scope boundary (Global Constraints + Task 5 Steps 6-9), inspection/no-modify-legacy (Task 3/4 file placement + Task 5 Step 6), plain-JS/no-build stack (all frontend tasks), file structure (matches exactly), authority model (Global Constraints + `logic.js`'s pass-through-only design), universe screen (Task 3), Shariah status handling (`logic.js` `statusBadge`/`tradingStatusFromShariah` + both pages), publication-state UX (`publicationBannerState` + banner rendering), the `/api/universe` REJECT gap (Task 1), no-active-publication handling (Task 1 Step 3's unchanged-empty-shape guarantee + frontend banner), security detail page (Task 4), decision presentation for PASS/UNKNOWN/REJECT (Task 4's trading-status banner), quant section (Task 4 `renderQuant`), risk section (Task 4 `renderRisk` + the honest "no per-trade context" table label from Task 3), evidence/provenance (Task 4 `renderEvidence`/`fetchEvidence`/`fetchPublicationDetail`), error/state handling (both pages' explicit loading/empty/error/none states), accessibility (semantic `<table>`/`<dl>`, `scope`, `aria-label`/`role`, `:focus-visible`, icon+text badges), visual design (tokens.css adapted from the legacy palette), testing (Task 2 + Task 1), backend API tests (Task 1), no fake data (Global Constraints + real-fetch-only design throughout), security review (Task 5 Steps 8-9), backend architecture preserved (only the one additive change), real publication state (Task 5 Step 7), final verification/report (Task 5).
- **Placeholder scan**: no TBD/TODO markers; every step has runnable code or an exact command.
- **Type/name consistency checked**: `verdict` field name matches between Task 1's backend output, `logic.js`'s `filterBySelects`, and `render.js`'s `createUniverseTable`; `safeQuantState`'s return shape (`status/signal/strategyId/asOfDate/attractiveness/components`) matches exactly between `logic.js`, `render.js`'s `updateRowQuant`, and `index.html`'s `loadQuantFor`; `fetchScreen`'s consumed shape in `security.html` matches `screening_api.screen_ticker`'s real return (`shariah.issuer_name/board/sector`, `quant.*`, `attractiveness.components.*`, verified against the actual source during design).
