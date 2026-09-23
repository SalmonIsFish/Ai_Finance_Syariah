"""The route allowlist -- the only module permitted to contain an API path literal.

Every call goes through AmanahClient.call(route_name), never a URL. That is what makes
"the model cannot invent a route" a property of the Python layer rather than only of the
MCP tool schema, and it is what lets test_bridge_route_allowlist.py verify the claim with
a string scan instead of a runtime crawl.

Zones mirror the nginx vhost (docs/deployment/nginx/amanahtrader.uk.conf):
  write   20 r/m burst 5-10   /paper/preview, /paper/approval, /paper/execute, /watchlist
  general 240 r/m burst 60    everything else
"""

from __future__ import annotations

from dataclasses import dataclass

ZONE_WRITE = "write"
ZONE_GENERAL = "general"


@dataclass(frozen=True)
class Route:
    """One allowed endpoint.

    ``cache_ttl_seconds`` of 0 means never cache. ``retryable`` is False wherever a
    retry could duplicate a side effect -- see client.py, which refuses to retry an
    execute at all because the failure mode is two orders.
    """

    name: str
    method: str
    path: str
    zone: str
    needs_operator: bool = False
    cache_ttl_seconds: float = 0.0
    retryable: bool = True


# TTL groups. Shariah verdicts change only when the SC publishes; quotes go stale in
# seconds; account state sits in between.
_TTL_VERDICT = 900.0
_TTL_QUOTE = 60.0
_TTL_ACCOUNT = 30.0
_TTL_AUDIT = 60.0
_TTL_COMPLIANCE = 600.0

_ROUTE_LIST = (
    # --- Shariah / universe (public on the deployment, cached longest) ---
    Route(
        "shariah_status",
        "GET",
        "/api/shariah/{ticker}",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_VERDICT,
    ),
    Route(
        "screen_detail", "GET", "/api/screen/{ticker}", ZONE_GENERAL, cache_ttl_seconds=_TTL_VERDICT
    ),
    Route("universe", "GET", "/api/universe", ZONE_GENERAL, cache_ttl_seconds=_TTL_VERDICT),
    Route(
        "universe_entry",
        "GET",
        "/api/universe/{ticker}",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_VERDICT,
    ),
    Route(
        "publications",
        "GET",
        "/api/universe/publications",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_VERDICT,
    ),
    Route(
        "publication",
        "GET",
        "/api/shariah/publication/{publication_id}",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_VERDICT,
    ),
    Route("screens_log", "GET", "/shariah/screens", ZONE_GENERAL, cache_ttl_seconds=_TTL_VERDICT),
    Route(
        "stock_explain",
        "GET",
        "/stock/{symbol}/explain",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_VERDICT,
    ),
    # --- Quant / market data ---
    Route("quant_signal", "GET", "/api/quant/{ticker}", ZONE_GENERAL, cache_ttl_seconds=_TTL_QUOTE),
    Route(
        "market_data", "GET", "/market-data/{symbol}", ZONE_GENERAL, cache_ttl_seconds=_TTL_QUOTE
    ),
    Route("research", "GET", "/api/research/{ticker}", ZONE_GENERAL, cache_ttl_seconds=_TTL_QUOTE),
    Route(
        "option_strategy",
        "GET",
        "/stock/{symbol}/option-strategy",
        ZONE_GENERAL,
        cache_ttl_seconds=0.0,
    ),
    # --- Risk / account ---
    Route("risk_limits", "GET", "/api/risk", ZONE_GENERAL, cache_ttl_seconds=_TTL_VERDICT),
    Route(
        "risk_snapshot", "GET", "/paper/risk-snapshot", ZONE_GENERAL, cache_ttl_seconds=_TTL_ACCOUNT
    ),
    Route("paper_account", "GET", "/paper/account", ZONE_GENERAL, cache_ttl_seconds=_TTL_ACCOUNT),
    Route("paper_status", "GET", "/paper/status", ZONE_GENERAL, cache_ttl_seconds=_TTL_ACCOUNT),
    # --- Portfolio / disposal clock ---
    Route("portfolio", "GET", "/portfolio", ZONE_GENERAL, cache_ttl_seconds=_TTL_ACCOUNT),
    Route("positions", "GET", "/positions", ZONE_GENERAL, cache_ttl_seconds=_TTL_ACCOUNT),
    Route(
        "positions_live",
        "GET",
        "/paper/positions/live",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_ACCOUNT,
    ),
    Route(
        "portfolio_history",
        "GET",
        "/portfolio/history",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_ACCOUNT,
    ),
    # Calling this endpoint is what advances and persists the disposal clock; see
    # client.refresh_disposal_clock, which bypasses the cache for exactly that reason.
    Route(
        "portfolio_compliance",
        "GET",
        "/portfolio/compliance",
        ZONE_GENERAL,
        cache_ttl_seconds=_TTL_COMPLIANCE,
    ),
    # --- Audit / evidence ---
    Route("evidence", "GET", "/api/evidence/{ticker}", ZONE_GENERAL, cache_ttl_seconds=_TTL_AUDIT),
    Route("execution_audit", "GET", "/execution-audit", ZONE_GENERAL, cache_ttl_seconds=_TTL_AUDIT),
    Route("approvals", "GET", "/approvals", ZONE_GENERAL, cache_ttl_seconds=_TTL_AUDIT),
    Route("audit_log", "GET", "/audit", ZONE_GENERAL, cache_ttl_seconds=_TTL_AUDIT),
    # --- Writes. Never cached; approval and execute are never retried. ---
    Route("paper_preview", "POST", "/paper/preview", ZONE_WRITE, retryable=True),
    Route("paper_approval", "POST", "/paper/approval", ZONE_WRITE, retryable=False),
    Route(
        "paper_execute",
        "POST",
        "/paper/execute/{queue_id}",
        ZONE_WRITE,
        needs_operator=True,
        retryable=False,
    ),
    Route(
        "paper_reconcile",
        "POST",
        "/paper/reconcile/{queue_id}",
        ZONE_WRITE,
        needs_operator=True,
        retryable=False,
    ),
)

ROUTES: dict[str, Route] = {route.name: route for route in _ROUTE_LIST}

# The only two routes nginx gates on the X-Amanah-Operator header. client.py asserts
# this set against Route.needs_operator so the two cannot drift apart.
OPERATOR_ROUTES = frozenset({"paper_execute", "paper_reconcile"})

# Deliberately absent from ROUTES. /news and /copilot/* each spend real OpenRouter
# money per call and the backend has no spend cap anywhere (see
# backend/test_public_route_allowlist.py:146-182). A bot paying a model to summarise
# for another model is the worst version of that bill. Named here so the enforcement
# test can assert their absence rather than merely relying on nobody adding them.
FORBIDDEN_PATHS = frozenset({"/news", "/copilot/explain", "/copilot/research"})

# Additionally barred from schedules.py: /paper/preview sits on a live SEC fetch
# (0.7-2s cold) and is an order-shaped action, so it is only ever user-initiated.
FORBIDDEN_SCHEDULED_ROUTES = frozenset({"paper_preview"})


def route_for(name: str) -> Route:
    """Look up an allowed route, failing closed on anything unknown."""
    try:
        return ROUTES[name]
    except KeyError:
        raise KeyError(f"unknown route '{name}'; allowed: {sorted(ROUTES)}") from None
