// Run: npm test  (node --test, no dependencies)
import { test } from "node:test";
import assert from "node:assert/strict";
import { lightStyle, lightRank, primaryReason } from "./light.js";

test("the three colours are drawn as themselves", () => {
  assert.equal(lightStyle("GREEN").meaning, "Compliant");
  assert.equal(lightStyle("YELLOW").meaning, "Under review");
  assert.equal(lightStyle("RED").meaning, "Not compliant");
});

test("anything else is unknown, never green", () => {
  for (const v of [undefined, null, "", "green", "PASS", "toString", "__proto__"]) {
    assert.equal(lightStyle(v).label, "Unknown", String(v));
  }
});

test("attention sorts first", () => {
  assert.ok(lightRank("RED") < lightRank("YELLOW"));
  assert.ok(lightRank("YELLOW") < lightRank("GREEN"));
  assert.ok(lightRank("???") < lightRank("GREEN"));
});

test("primary reason reads the first reason's words", () => {
  assert.equal(primaryReason({ reasons: [{ code: "near_limit_debt", detail: "debt 31%" }] }), "debt 31%");
  assert.equal(primaryReason({ reasons: [{ code: "data_unavailable" }] }), "data_unavailable");
  assert.equal(primaryReason({ reasons: [] }), null);
  assert.equal(primaryReason(null), null);
});
