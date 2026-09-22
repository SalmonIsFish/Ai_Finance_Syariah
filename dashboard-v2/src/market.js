/**
 * Which market a symbol belongs to, and what that means for display.
 *
 * ON DUPLICATING A BACKEND RULE
 *
 * `detectMarket` mirrors `backend/agents/shariah_agent.py:10-14`, which is the
 * source of truth:
 *
 *     normalized.isdigit()  ->  "MY"      (Bursa codes are numeric: 5225, 0026)
 *     otherwise             ->  "US"
 *
 * Duplicating backend logic in a client is usually a mistake, so the reasoning
 * is worth stating. This rule decides *which authority to ask* -- it is about
 * the shape of a ticker, not about compliance. No gate consults this copy, and
 * nothing is permitted or refused on its say-so. Duplicating `check_symbol`,
 * or any ratio threshold, would be a different matter entirely.
 *
 * It exists because The Desk shows the market as you type, before any request
 * is made. The authoritative value arrives with the preview, in
 * `preview.agent_summary.shariah.market`, and that is what the UI shows once a
 * preview exists. Until then this is a labelled guess.
 *
 * One deliberate divergence: empty input returns "UNKNOWN", not "US". The
 * backend's US default is safe because it only ever sees a real symbol; a blank
 * field is not a US stock.
 *
 * NOTE ON THE LITERAL CLASS STRINGS: as in ./verdict.js, every Tailwind class
 * is written out in full. Tailwind scans source text, so an interpolated
 * `text-[var(--color-${x})]` is never generated and silently renders as
 * inherited colour.
 */

export function detectMarket(symbol) {
  const normalized = String(symbol ?? "").trim().toUpperCase();
  if (!normalized) return "UNKNOWN";
  return /^\d+$/.test(normalized) ? "MY" : "US";
}

const MARKETS = {
  MY: {
    label: "MY",
    name: "Malaysia",
    authority: "Securities Commission Malaysia SAC list",
    currency: "RM",
  },
  US: {
    label: "US",
    name: "United States",
    authority: "Self-built SEC EDGAR ratio screen",
    currency: "$",
  },
  UNKNOWN: {
    label: "—",
    name: "Unknown",
    authority: "No market determined yet",
    currency: "",
  },
};

function market(code) {
  return MARKETS[String(code ?? "").trim().toUpperCase()] ?? MARKETS.UNKNOWN;
}

export const marketName = (code) => market(code).name;
export const marketLabel = (code) => market(code).label;

/** Which authority will screen this symbol. The point of the badge. */
export const marketAuthority = (code) => market(code).authority;

/**
 * Neutral on purpose. A market badge must never borrow the verdict palette --
 * green/red there mean permitted/refused, and "this is a Malaysian stock" is
 * neither.
 */
export function marketBadgeClass() {
  return "bg-[var(--color-panel-2)] text-[var(--color-subtle)] border border-[var(--color-border)]";
}

/**
 * Money, in the currency of the market it was quoted in.
 *
 * An unknown market gets no symbol at all rather than a guessed "$". Printing
 * RM 6.50 as $6.50 misstates a price by roughly 4x, and a bare number is
 * honest where a wrong symbol is not.
 */
export function formatPrice(value, marketCode) {
  const numeric = Number(value);
  if (value === null || value === undefined || Number.isNaN(numeric)) return "—";
  const amount = numeric.toFixed(2);
  const symbol = market(marketCode).currency;
  if (!symbol) return amount;
  return symbol === "RM" ? `RM ${amount}` : `${symbol}${amount}`;
}
