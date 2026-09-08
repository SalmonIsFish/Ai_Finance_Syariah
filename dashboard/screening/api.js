// api.js
// Thin fetch client for the read-only Malaysian screening API. Each
// function maps to exactly one backend endpoint and returns parsed JSON.
// No retries, no fallback to an optimistic default on failure -- callers
// see the real error and render an explicit error state (logic.js's
// safeQuantState is where a per-row failure gets normalized for display).

import { buildUniverseQuery } from "./logic.js";

const DEFAULT_BASE = "http://127.0.0.1:8000";

export function apiBase() {
  try {
    const store = globalThis.localStorage;
    const stored = store && store.getItem("amanah-screening-api-base");
    if (stored) return stored.replace(/\/+$/, "");
  } catch {
    // localStorage unavailable -- fall through to the default
  }
  return DEFAULT_BASE;
}

async function getJson(path) {
  const response = await fetch(`${apiBase()}${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }
  return response.json();
}

export function fetchUniverse(options) {
  return getJson(`/api/universe?${buildUniverseQuery(options)}`);
}

export function fetchQuant(ticker) {
  return getJson(`/api/quant/${encodeURIComponent(ticker)}`);
}

export function fetchScreen(ticker) {
  return getJson(`/api/screen/${encodeURIComponent(ticker)}`);
}

export function fetchPublicationDetail(publicationId) {
  return getJson(`/api/shariah/publication/${encodeURIComponent(publicationId)}`);
}

export function fetchRiskLimits() {
  return getJson(`/api/risk`);
}

export function fetchEvidence(ticker, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) });
  return getJson(`/api/evidence/${encodeURIComponent(ticker)}?${params.toString()}`);
}
