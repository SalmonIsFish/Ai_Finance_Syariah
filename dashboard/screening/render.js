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
