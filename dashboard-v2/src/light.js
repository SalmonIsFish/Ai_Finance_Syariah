/**
 * GREEN / YELLOW / RED from the Shariah monitor (backend traffic_light.py).
 *
 * The backend decides the colour; this only draws it. Anything that is not
 * exactly one of the three is drawn as "unknown" -- never as green -- so a
 * malformed or future value can not read as compliant.
 */
const STYLES = {
  GREEN: {
    label: "Green",
    meaning: "Compliant",
    badge: "bg-[var(--color-ok-bg)] text-[var(--color-ok)] border-[var(--color-ok)]",
  },
  YELLOW: {
    label: "Yellow",
    meaning: "Under review",
    badge: "bg-[var(--color-warn-bg)] text-[var(--color-warn)] border-[var(--color-warn)]",
  },
  RED: {
    label: "Red",
    meaning: "Not compliant",
    badge: "bg-[var(--color-bad-bg)] text-[var(--color-bad)] border-[var(--color-bad)]",
  },
};

const UNKNOWN = {
  label: "Unknown",
  meaning: "Not screened",
  badge: "bg-[var(--color-unknown-bg)] text-[var(--color-unknown)] border-[var(--color-unknown)]",
};

export function lightStyle(light) {
  return Object.prototype.hasOwnProperty.call(STYLES, light) ? STYLES[light] : UNKNOWN;
}

/** Sort order for a holdings table: what needs attention first. */
export function lightRank(light) {
  return { RED: 0, YELLOW: 1, GREEN: 2 }[light] ?? 1;
}

/** The first reason, in words, for a one-line "why" column. */
export function primaryReason(trafficLight) {
  const reasons = trafficLight?.reasons;
  if (!Array.isArray(reasons) || reasons.length === 0) return null;
  return reasons[0]?.detail || reasons[0]?.code || null;
}
