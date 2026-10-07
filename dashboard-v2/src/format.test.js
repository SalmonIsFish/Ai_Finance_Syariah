// Run: npm test  (node --test, no dependencies)
import { test } from "node:test";
import assert from "node:assert/strict";
import { fmtMoney, fmtPct, fmtNum, fmtQty, fmtDateTime, fmtDate, fmtTime, MISSING } from "./format.js";

test("a missing or non-finite value is never rendered as a number", () => {
  for (const v of [null, undefined, "", NaN, Infinity, -Infinity, "abc"]) {
    assert.equal(fmtMoney(v, "US"), MISSING, `money ${v}`);
    assert.equal(fmtPct(v), MISSING, `pct ${v}`);
    assert.equal(fmtNum(v, 2), MISSING, `num ${v}`);
    assert.equal(fmtQty(v), MISSING, `qty ${v}`);
  }
});

test("zero is a real value, not a missing one", () => {
  assert.equal(fmtMoney(0, "US"), "$0.00");
  assert.equal(fmtPct(0), "0.00%");
});

test("money is grouped and carries the market's currency", () => {
  assert.equal(fmtMoney(100105.08, "US"), "$100,105.08");
  assert.equal(fmtMoney(6.5, "MY"), "RM 6.50");
  // An unknown market gets no guessed currency.
  assert.equal(fmtMoney(1234.5, "UNKNOWN"), "1,234.50");
  assert.equal(fmtMoney("231.5", "US"), "$231.50");
});

test("a negative amount puts a true minus before the currency", () => {
  assert.equal(fmtMoney(-12.34, "US"), "−$12.34");
  assert.equal(fmtMoney(-6.5, "MY"), "−RM 6.50");
  assert.equal(fmtPct(-1.5), "−1.50%");
});

test("signed adds + to gains only", () => {
  assert.equal(fmtMoney(3.23, "US", { signed: true }), "+$3.23");
  assert.equal(fmtMoney(0, "US", { signed: true }), "$0.00");
  assert.equal(fmtPct(0.46, 2, { signed: true }), "+0.46%");
});

test("quantities keep fractional shares and group thousands", () => {
  assert.equal(fmtQty(1), "1");
  assert.equal(fmtQty(1500), "1,500");
  assert.equal(fmtQty(0.1234), "0.1234");
  assert.equal(fmtQty(-1), "−1");
});

test("timestamps are shown in Malaysia time and say so", () => {
  // 07:24:19 UTC is 15:24:19 in Kuala Lumpur (UTC+8, no DST).
  assert.equal(fmtDateTime("2026-10-07T07:24:19.123+00:00"), "07 Oct 2026, 15:24:19 MYT");
  assert.equal(fmtTime("2026-10-07T07:24:19Z"), "15:24:19 MYT");
  assert.equal(fmtDateTime("not a date"), MISSING);
  assert.equal(fmtDateTime(null), MISSING);
});

test("a bare calendar date is never shifted by a time zone", () => {
  assert.equal(fmtDate("2026-05-29"), "29 May 2026");
  // An instant late on 6 Oct UTC is already 7 Oct in Kuala Lumpur.
  assert.equal(fmtDate("2026-10-06T20:00:00Z"), "07 Oct 2026");
  assert.equal(fmtDate(undefined), MISSING);
});
