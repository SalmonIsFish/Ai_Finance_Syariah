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

export async function submitApproval(preview, approved) {
  const res = await fetch("/paper/approval", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ preview, approved })
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(err || "Failed to submit approval");
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
