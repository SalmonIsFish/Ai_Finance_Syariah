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
  formatPctValue,
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

test("freshnessFromAsOfDate treats exactly staleDays as fresh and staleDays+1 as stale (boundary)", () => {
  assert.equal(freshnessFromAsOfDate("2026-09-03", "2026-09-08").isStale, false); // exactly 5 days old
  assert.equal(freshnessFromAsOfDate("2026-09-02", "2026-09-08").isStale, true); // 6 days old
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

test("formatPctValue formats an already-percent-scaled number without multiplying, and falls back to an em dash", () => {
  assert.equal(formatPctValue(5), "5.0%");
  assert.equal(formatPctValue(2.5), "2.5%");
  assert.equal(formatPctValue(null), "—");
  assert.equal(formatPctValue(undefined), "—");
  assert.equal(formatPctValue(NaN), "—");
});
