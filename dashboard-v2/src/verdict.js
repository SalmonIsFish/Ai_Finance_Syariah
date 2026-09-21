/**
 * One place that decides how a gate verdict looks.
 *
 * The gate has THREE meaningful outcomes and they must stay visually distinct:
 *
 *   PASS    the authority confirms this is eligible
 *   REJECT  the authority says it is NOT eligible
 *   UNKNOWN no authority has ruled -- we could not confirm
 *
 * REJECT and UNKNOWN are not the same thing and must never share a colour.
 * Under the project owner's divestment ruling a NON_COMPLIANT holding carries a
 * one-month disposal duty and a purification obligation on any gain; an
 * UNCONFIRMED holding carries neither. Painting them both red invites a
 * disposal the authority never asked for. The backend goes to real lengths to
 * keep these apart (see holdings_compliance.py, shariah_gate.py); the UI has to
 * hold the same line.
 *
 * Several pages previously used an inline `status === 'PASS' ? green : red`
 * ternary, which collapsed UNKNOWN into REJECT. This module exists so that
 * shortcut has an easy alternative.
 *
 * NOTE ON THE LITERAL CLASS STRINGS: every Tailwind class below is written out
 * in full rather than built by interpolation. Tailwind scans source text for
 * class names, so a constructed string like `text-[var(--color-${tone})]` would
 * never be generated and the colour would silently fall back to inherit.
 */

const VERDICT_STYLES = {
  PASS: {
    text: "text-[var(--color-ok)]",
    badge: "bg-[var(--color-ok-bg)] text-[var(--color-ok)] border border-[var(--color-ok)]",
    icon: "text-[var(--color-ok)]",
  },
  WARN: {
    text: "text-[var(--color-warn)]",
    badge: "bg-[var(--color-warn-bg)] text-[var(--color-warn)] border border-[var(--color-warn)]",
    icon: "text-[var(--color-warn)]",
  },
  REJECT: {
    text: "text-[var(--color-bad)]",
    badge: "bg-[var(--color-bad-bg)] text-[var(--color-bad)] border border-[var(--color-bad)]",
    icon: "text-[var(--color-bad)]",
  },
  UNKNOWN: {
    text: "text-[var(--color-unknown)]",
    badge:
      "bg-[var(--color-unknown-bg)] text-[var(--color-unknown)] border border-[var(--color-unknown)]",
    icon: "text-[var(--color-unknown)]",
  },
};

/** Anything unrecognised is UNKNOWN, never PASS -- fail closed, like the gate. */
export function verdictStyle(verdict) {
  return VERDICT_STYLES[verdict] ?? VERDICT_STYLES.UNKNOWN;
}

/** Text colour for an inline status label. */
export function verdictTextClass(verdict) {
  return verdictStyle(verdict).text;
}

/** Pill/badge classes for a status chip. */
export function verdictBadgeClass(verdict) {
  return verdictStyle(verdict).badge;
}
