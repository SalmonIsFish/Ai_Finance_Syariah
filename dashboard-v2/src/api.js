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
