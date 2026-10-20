export async function fetchPreview(order) {
  const res = await fetch("/paper/preview", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(order)
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error("Unauthorized - Please refresh and sign in.");
    }
    const err = await res.text();
    throw new Error(err || "Failed to fetch preview");
  }

  return await res.json();
}

/** FastAPI puts the reason in `detail`; show that rather than a raw JSON blob. */
async function errorText(res, fallback) {
  const text = await res.text();
  try {
    const detail = JSON.parse(text)?.detail;
    if (detail) return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch {
    // not JSON -- fall through to the raw text
  }
  return text || fallback;
}

/**
 * Records an approval decision in the queue. It does NOT send anything to the
 * broker: /paper/approval always returns broker_submission: false, and the
 * verdict -- which can be REJECT -- is in `approval.status`, under HTTP 200.
 * Callers must read it rather than treat a 200 as success.
 */
export async function submitApproval(preview, approved) {
  const res = await fetch("/paper/approval", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ preview, approved })
  });

  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error("Unauthorized - Please refresh and sign in.");
    }
    throw new Error(await errorText(res, "Failed to submit approval"));
  }

  return await res.json();
}

const fetchGet = async (url) => {
  const res = await fetch(url);
  if (!res.ok) {
    // Every read route below requires owner auth. Without this branch an
    // expired session surfaced as a dozen identical "Failed to fetch /x"
    // errors across the dashboard, none of which said what to do about it.
    if (res.status === 401 || res.status === 403) {
      throw new Error("Unauthorized - Please refresh and sign in.");
    }
    throw new Error(`Failed to fetch ${url}`);
  }
  return await res.json();
};

export const fetchPortfolio = () => fetchGet("/portfolio");
export const fetchLivePositions = () => fetchGet("/paper/positions/live");
export const fetchAccount = () => fetchGet("/paper/account");
export const fetchRiskSnapshot = () => fetchGet("/paper/risk-snapshot");
/** What this instance will actually execute, and through which adapter, per market.
 *  The backend is the authority on this -- the UI must not restate it. */
export const fetchPaperStatus = () => fetchGet("/paper/status");
/** Alpaca's US market clock. is_open is null -- not false -- when it cannot be read. */
export const fetchMarketClock = () => fetchGet("/market/clock");
export const fetchPortfolioHistoryLive = (period = "1M") => fetchGet(`/portfolio/history/live?period=${period}`);
export const fetchPortfolioHistory = (period = "1M") => fetchGet(`/portfolio/history?period=${period}`);
export const fetchMarketOverview = () => fetchGet("/market-overview");
export const fetchNews = () => fetchGet("/news");
export const fetchStockProfile = (symbol) => fetchGet(`/stock/${symbol}/profile`);
export const fetchExecutionAudit = () => fetchGet("/execution-audit");
export const fetchApprovals = () => fetchGet("/approvals");
/** Re-screens held positions against the current Shariah authority, plus the
 *  estimated purification owed on any holding it finds non-compliant. */
export const fetchCompliance = () => fetchGet("/portfolio/compliance");
export const fetchAuditEvents = () => fetchGet("/audit");

/** The active SC Malaysia publication's securities. `limit` is server-capped at
 *  1000 and the list is 905, so this is deliberately one call rather than
 *  pagination -- the whole authority list fits. `status` is PASS | REJECT | ALL. */
export const fetchUniverse = (status = "ALL") =>
  fetchGet(`/api/universe?shariah_status=${status}&limit=1000`);

/** Publication provenance: source_document_hash, parser version, who approved
 *  and activated it. Deliberately a second call -- `active_publication` on the
 *  universe response carries only id/date/activated_at, not the hash. */
export const fetchPublication = (publicationId) =>
  fetchGet(`/api/shariah/publication/${encodeURIComponent(publicationId)}`);

/** The current US verdict for each symbol ever screened -- one row per company.
 *  Not the raw log: that repeats a symbol once per screen (14,213 rows across
 *  16 symbols in production) and, being capped, omits symbols entirely. */
export const fetchScreenedUS = () => fetchGet("/shariah/screens?latest_only=true");

/** The append-only decision trail for one ticker: what each gate said, on which
 *  authority and which prices, and what was decided. Owner-authenticated — it
 *  records orders actually put through the gate chain, not just screening. */
export const fetchEvidence = (ticker, limit = 20) =>
  fetchGet(`/api/evidence/${encodeURIComponent(ticker)}?limit=${limit}`);

// --- Shariah monitor (backend monitor_api.py) ---------------------------------

const postJson = async (url, body) => {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    if (res.status === 401 || res.status === 403) {
      throw new Error("Unauthorized - Please refresh and sign in.");
    }
    throw new Error(await errorText(res, `Failed: ${url}`));
  }
  return await res.json();
};

/** Screens and colours every holding you reported. This call WRITES: it records
 *  colour changes and advances the disposal clock, and each US holding is a live
 *  SEC fetch -- so the page calls it on load and on "Re-check", never on a timer. */
export const runMonitor = () => fetchGet("/monitor");
export const fetchMonitorAlerts = () => fetchGet("/monitor/alerts?limit=20");
export const fetchMonitorSettings = () => fetchGet("/monitor/settings");
export const saveMonitorSettings = (methodology) => postJson("/monitor/settings", { methodology });
export const addHolding = (holding) => postJson("/monitor/holdings", holding);
export const removeHolding = (symbol, broker) => postJson("/monitor/holdings/remove", { symbol, broker });
export const importHoldingsCsv = (broker, csv) => postJson("/monitor/holdings/import", { broker, csv });
export const recordSale = (sale) => postJson("/monitor/sales", sale);
/** Owner-only, and RED-only: there is deliberately no call that marks a holding green. */
export const escalateHolding = (symbol, detail, reportDate) =>
  postJson("/monitor/escalate", { symbol, detail, report_date: reportDate ?? null });
/** Asks a model to read the latest annual report. It can only add a YELLOW flag. */
export const reviewFiling = (symbol) => postJson(`/monitor/review/${encodeURIComponent(symbol)}`);
