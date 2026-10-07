/**
 * Display formatting with one rule: a value we do not have is shown as "—".
 *
 * The dashboard used to write `Number(x || 0).toFixed(2)`, which turns a failed
 * broker call into "$0.00" -- a confident, wrong number, and on an equity tile
 * the most alarming one possible. A missing figure is not a zero. Every helper
 * here returns "—" for null, undefined, "" and anything non-finite, so a gap in
 * the data stays visibly a gap.
 *
 * Numbers go through Intl.NumberFormat: grouped thousands ($100,105.08, not
 * $100105.08) and a real minus sign placed before the currency (−$12.34, not
 * $-12.34). Times go through Intl.DateTimeFormat in one named zone, always
 * labelled, so "15:24" is never ambiguous about whose afternoon it is.
 */
import { marketCurrency } from "./market.js";

export const MISSING = "—";

/** Every time on screen is shown in this zone, with DISPLAY_TZ_LABEL beside it. */
export const DISPLAY_TZ = "Asia/Kuala_Lumpur";
export const DISPLAY_TZ_LABEL = "MYT";

const MINUS = "−";
const LOCALE = "en-GB";

export function isPresent(value) {
  if (value === null || value === undefined || value === "") return false;
  return Number.isFinite(Number(value));
}

const fixedFormatters = new Map();
function fixed(digits) {
  if (!fixedFormatters.has(digits)) {
    fixedFormatters.set(
      digits,
      new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }),
    );
  }
  return fixedFormatters.get(digits);
}

const quantityFormatter = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 4 });

/** Sign handling shared by money and percent: "+" only when asked, a true minus always. */
function withSign(value, body, signed) {
  if (value < 0) return `${MINUS}${body}`;
  if (signed && value > 0) return `+${body}`;
  return body;
}

/**
 * Money in the currency of `marketCode`: "$1,234.56", "RM 6.50", or a bare
 * "1,234.56" for an unknown market. `signed` adds "+" to gains, for P&L.
 */
export function fmtMoney(value, marketCode, { signed = false, digits = 2 } = {}) {
  if (!isPresent(value)) return MISSING;
  const n = Number(value);
  const amount = fixed(digits).format(Math.abs(n));
  const currency = marketCurrency(marketCode);
  const body = !currency ? amount : currency === "RM" ? `RM ${amount}` : `${currency}${amount}`;
  return withSign(n, body, signed);
}

export function fmtPct(value, digits = 2, { signed = false } = {}) {
  if (!isPresent(value)) return MISSING;
  const n = Number(value);
  return withSign(n, `${fixed(digits).format(Math.abs(n))}%`, signed);
}

/** A plain number. With `digits`, fixed decimals; without, as precise as given (grouped). */
export function fmtNum(value, digits) {
  if (!isPresent(value)) return MISSING;
  const n = Number(value);
  const body = digits === undefined ? quantityFormatter.format(Math.abs(n)) : fixed(digits).format(Math.abs(n));
  return withSign(n, body, false);
}

/** Share or contract quantities: grouped, up to 4 decimals for fractional shares. */
export const fmtQty = (value) => fmtNum(value);

const dateTimeFormatter = new Intl.DateTimeFormat(LOCALE, {
  timeZone: DISPLAY_TZ,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const timeFormatter = new Intl.DateTimeFormat(LOCALE, {
  timeZone: DISPLAY_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
const dateOnlyFormatter = new Intl.DateTimeFormat(LOCALE, {
  timeZone: "UTC",
  day: "2-digit",
  month: "short",
  year: "numeric",
});

function toDate(value) {
  if (value === null || value === undefined || value === "") return null;
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "07 Oct 2026, 15:24:19 MYT". The backend writes UTC ISO strings with an offset. */
export function fmtDateTime(value) {
  const d = toDate(value);
  return d ? `${dateTimeFormatter.format(d)} ${DISPLAY_TZ_LABEL}` : MISSING;
}

/** "15:24:19 MYT" -- for "as of" stamps on data refreshed today. */
export function fmtTime(value) {
  const d = toDate(value);
  return d ? `${timeFormatter.format(d)} ${DISPLAY_TZ_LABEL}` : MISSING;
}

/**
 * A calendar date: "29 May 2026". A bare "YYYY-MM-DD" is a date, not an
 * instant, so it is formatted in UTC and never shifted a day by a time zone.
 * A full timestamp is shown as the date it falls on in DISPLAY_TZ.
 */
export function fmtDate(value) {
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return dateOnlyFormatter.format(new Date(`${value}T00:00:00Z`));
  }
  const d = toDate(value);
  if (!d) return MISSING;
  return new Intl.DateTimeFormat(LOCALE, {
    timeZone: DISPLAY_TZ,
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(d);
}
