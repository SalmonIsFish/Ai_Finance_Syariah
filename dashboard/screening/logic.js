// logic.js
// Pure, framework-free functions: query building, status formatting,
// filtering, search, and safety fallbacks. Nothing here calls fetch or
// touches the DOM, and nothing here re-derives a Shariah PASS/REJECT/
// UNKNOWN verdict -- every status value comes from the backend response
// verbatim. Unit tested by tests/logic.test.js via `node --test`.

export function buildUniverseQuery({ shariahStatus = "PASS", limit = 1000, offset = 0 } = {}) {
  return new URLSearchParams({
    shariah_status: shariahStatus,
    limit: String(limit),
    offset: String(offset),
  }).toString();
}

const SHARIAH_BADGES = {
  PASS: {
    label: "PASS",
    className: "badge badge--ok",
    explanation:
      "An approved and activated SC Malaysia publication establishes this security as Shariah-compliant.",
  },
  REJECT: {
    label: "REJECT",
    className: "badge badge--bad",
    explanation:
      "An approved and activated SC Malaysia publication explicitly establishes this security as non-compliant.",
  },
  UNKNOWN: {
    label: "UNKNOWN",
    className: "badge badge--warn",
    explanation:
      "The security is not currently established as eligible by an approved and activated authoritative publication. This is not the same as non-compliant.",
  },
};

export function statusBadge(status) {
  return (
    SHARIAH_BADGES[status] || {
      label: "UNKNOWN",
      className: "badge badge--warn",
      explanation: "No authoritative Shariah status was returned. Treated as UNKNOWN and blocked.",
    }
  );
}

export function tradingStatusFromShariah(status) {
  if (status === "PASS") {
    return null;
  }
  if (status === "REJECT") {
    return { label: "BLOCKED", reason: "Authoritative source establishes non-compliance." };
  }
  if (status === "UNKNOWN") {
    return { label: "BLOCKED", reason: "No authoritative PASS is currently established." };
  }
  return { label: "BLOCKED", reason: "Shariah status could not be confirmed." };
}

export function formatDate(value) {
  return value ? value : "—";
}

export function formatPct(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
}

export function formatPctValue(value) {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(1)}%` : "—";
}

export function formatScore(value) {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—";
}

export function matchesSearch(security, query) {
  const q = (query || "").trim().toLowerCase();
  if (!q) return true;
  const ticker = (security.ticker || "").toLowerCase();
  const issuer = (security.issuer_name || "").toLowerCase();
  return ticker.includes(q) || issuer.includes(q);
}

export function filterBySelects(security, filters) {
  const { sector, market, verdict } = filters || {};
  if (sector && sector !== "ALL" && security.sector !== sector) return false;
  if (market && market !== "ALL" && security.board !== market) return false;
  if (verdict && verdict !== "ALL" && security.verdict !== verdict) return false;
  return true;
}

export function filterByQuant(quantState, filters) {
  const { signal, minAttractiveness, maxAttractiveness } = filters || {};
  const signalActive = signal && signal !== "ALL";
  const rangeActive = typeof minAttractiveness === "number" || typeof maxAttractiveness === "number";
  if (!signalActive && !rangeActive) return true;
  if (!quantState || quantState.status !== "ok") return false;
  if (signalActive && quantState.signal !== signal) return false;
  if (typeof minAttractiveness === "number" && (quantState.attractiveness ?? -Infinity) < minAttractiveness) {
    return false;
  }
  if (typeof maxAttractiveness === "number" && (quantState.attractiveness ?? Infinity) > maxAttractiveness) {
    return false;
  }
  return true;
}

export function publicationBannerState(activePublication) {
  if (!activePublication) {
    return {
      state: "none",
      headline: "NO ACTIVE SHARIAH PUBLICATION",
      detail:
        "The current SC Malaysia publication has not been approved and activated. Authoritative PASS cannot currently be established.",
    };
  }
  return {
    state: "active",
    id: activePublication.id,
    publicationDate: activePublication.publication_date,
    activatedAt: activePublication.activated_at,
  };
}

const MS_PER_DAY = 24 * 60 * 60 * 1000;

export function freshnessFromAsOfDate(asOfDate, todayIso, staleDays = 5) {
  if (!asOfDate || !todayIso) {
    return { label: "no data", isStale: true };
  }
  const asOf = new Date(`${asOfDate}T00:00:00Z`);
  const today = new Date(`${todayIso}T00:00:00Z`);
  const days = Math.round((today.getTime() - asOf.getTime()) / MS_PER_DAY);
  if (Number.isNaN(days)) {
    return { label: "no data", isStale: true };
  }
  if (days <= 0) {
    return { label: "as of today", isStale: false };
  }
  const isStale = days > staleDays;
  return { label: `${days}d old${isStale ? " — STALE" : ""}`, isStale };
}

export function safeQuantState(raw, error) {
  if (error || !raw || !raw.quant) {
    return { status: "error", signal: null, strategyId: null, asOfDate: null, attractiveness: null, components: null };
  }
  return {
    status: "ok",
    signal: raw.quant.signal ?? null,
    strategyId: raw.quant.strategy_id ?? null,
    asOfDate: raw.quant.as_of_date ?? null,
    attractiveness: raw.attractiveness?.attractiveness ?? null,
    components: raw.attractiveness?.components ?? null,
  };
}

export function distinctValues(securities, field) {
  const values = new Set();
  for (const s of securities) {
    if (s[field]) values.add(s[field]);
  }
  return Array.from(values).sort();
}
