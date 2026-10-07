/**
 * Display formatting with one rule: a value we do not have is shown as "—".
 *
 * The dashboard used to write `Number(x || 0).toFixed(2)`, which turns a failed
 * broker call into "$0.00" -- a confident, wrong number, and on an equity tile
 * the most alarming one possible. A missing figure is not a zero. Every helper
 * here returns "—" for null, undefined, "" and anything non-finite, so a gap in
 * the data stays visibly a gap.
 *
 * Money delegates the currency choice to market.formatPrice, which already
 * refuses to guess a "$" for an unknown market.
 */
import { formatPrice } from "./market";

export const MISSING = "—";

export function isPresent(value) {
  if (value === null || value === undefined || value === "") return false;
  return Number.isFinite(Number(value));
}

export function fmtMoney(value, marketCode) {
  return isPresent(value) ? formatPrice(value, marketCode) : MISSING;
}

export function fmtPct(value, digits = 2) {
  return isPresent(value) ? `${Number(value).toFixed(digits)}%` : MISSING;
}

export function fmtNum(value, digits) {
  if (!isPresent(value)) return MISSING;
  return digits === undefined ? String(Number(value)) : Number(value).toFixed(digits);
}
