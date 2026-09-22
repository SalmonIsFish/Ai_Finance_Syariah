/**
 * Translate whatever an API happens to call a Shariah status into the three
 * verdicts the UI paints.
 *
 * WHY THIS EXISTS
 *
 * Two endpoints describe the same three states in two different vocabularies:
 *
 *   /api/universe         verdict: "PASS" | "REJECT"        (already normalised)
 *   /shariah/screens      status:  "COMPLIANT" | "NON_COMPLIANT" | "UNKNOWN"
 *
 * `verdictStyle` in ./verdict.js keys on PASS / WARN / REJECT / UNKNOWN and
 * fails closed to UNKNOWN on anything it does not recognise. That is the right
 * default, and it is exactly why the raw US log must be translated before it
 * reaches styling: "COMPLIANT" is not a key, so every compliant US company
 * would be painted UNKNOWN -- "we could not confirm" -- when in fact the screen
 * confirmed it.
 *
 * The obvious inline fix is worse. A `status === "COMPLIANT" ? ok : bad`
 * ternary paints UNKNOWN red, the colour reserved for an authority saying NO.
 * Under the owner's divestment ruling a REJECT holding carries a one-month
 * disposal duty and purification on any gain; an UNKNOWN holding carries
 * neither. Painting them alike invites a disposal nobody asked for.
 *
 * So: one translation, fail-closed in the same direction as the gate, and
 * never a local ternary at a call site.
 */

const STATUS_TO_VERDICT = {
  PASS: "PASS",
  COMPLIANT: "PASS",
  REJECT: "REJECT",
  NON_COMPLIANT: "REJECT",
  WARN: "WARN",
};

/**
 * Anything unrecognised -- including null, undefined and a status we have
 * never seen -- becomes UNKNOWN. Never PASS: an unreadable verdict must not
 * look like permission.
 */
export function normalizeVerdict(status) {
  if (typeof status !== "string") return "UNKNOWN";
  return STATUS_TO_VERDICT[status.trim().toUpperCase()] ?? "UNKNOWN";
}

/** Human-facing wording for a normalised verdict. */
const VERDICT_LABELS = {
  PASS: "Compliant",
  REJECT: "Not compliant",
  WARN: "Caution",
  UNKNOWN: "Unconfirmed",
};

export function verdictLabel(status) {
  return VERDICT_LABELS[normalizeVerdict(status)];
}
