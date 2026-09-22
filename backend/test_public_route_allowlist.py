"""Every publicly readable route must be a deliberate choice, not an omission.

Two separate times now, a route has been left open by accident rather than by
decision:

  * `POST /api/p3/portfolios` had no actor dependency at all and was an
    unauthenticated database write reachable from the internet (fixed
    2026-09-21).
  * `GET /positions` and `GET /portfolio/history` were publicly readable while
    their siblings `/portfolio` and `/paper/positions/live` were gated. They
    exposed real holdings -- symbol, account suffix, cost basis, unrealised P&L
    -- and account equity. Missed when the dashboard was locked down because
    only the "live" variants made it onto the list (fixed 2026-09-22).

Both were found by someone happening to look. This test removes the luck: a new
route is either authenticated, or it is added to PUBLIC_ROUTES with a reason,
and adding it there is a visible decision in a diff rather than a silence.

This is the same shape as the /api/* GET-only invariant in
test_screening_api.py, which is what made moving the P3 routes the obvious fix
instead of loosening an assertion.
"""

import inspect

from local_api import app

# Routes intentionally reachable without credentials, each with the reason.
# Adding to this list is a decision. If you are adding one because a test failed,
# stop and ask whether the route should instead be authenticated.
PUBLIC_ROUTES = {
    "/": "Service banner. Booleans about mode and adapter; no account data.",
    # The read-only screening surface. Deliberately public so the compliance
    # method can be inspected without an account -- it is the product's claim to
    # auditability. Guarded GET/HEAD-only by test_screening_api.py::test_10.
    "/api/evidence/{ticker}": "Read-only screening evidence.",
    "/api/knowledge/note/{note_path:path}": "Read-only policy note; traversal-audited.",
    "/api/knowledge/search": "Read-only policy-note search.",
    "/api/quant/{ticker}": "Read-only quant signal.",
    "/api/research/{ticker}": "Read-only research aggregation.",
    "/api/risk": "Read-only risk limits (config values, not positions).",
    "/api/screen/{ticker}": "Read-only Shariah screen.",
    "/api/shariah/publication/{publication_id}": "Read-only SC publication.",
    "/api/shariah/{ticker}": "Read-only Shariah verdict.",
    "/api/universe": "Read-only SC universe.",
    "/api/universe/publications": "Read-only publication list.",
    "/api/universe/{ticker}": "Read-only universe entry.",
    # Demo surface. The nginx vhost rate-limits these and notes that none can
    # reach the broker: the gate chain refuses a non-compliant order regardless
    # of who asked.
    "/agent/evaluate": "Evaluates a hypothetical ticket. Submits nothing.",
    "/watchlist": "Watchlist read/write for the live demo. Rate-limited in nginx.",
    "/opportunities": "Scan results. No account data.",
    # POST /audit is gated by METHOD in nginx ($amanah_write_denied), not here.
    "/audit": "GET is the public execution-audit view; POST needs the operator key at nginx.",
    "/market-data/{symbol}": "Market prices. Not account data.",
    "/moomoo/status": "Legacy gateway reachability. Booleans only.",
    # /news is NOT here any more. It was, with the reason "Market news. Not
    # account data." -- which was true and beside the point. Each request makes
    # up to NEWS_AI_SUMMARY_MAX_ARTICLES (5) OpenRouter calls, so an anonymous
    # caller could bill the owner's LLM account at the nginx rate limit: roughly
    # 20 calls/second, on the order of $850/day, with no spend cap anywhere in
    # the codebase. Gated 2026-09-22.
    #
    # The lesson for anything added below: "does this disclose data?" is only
    # half the question. The other half is "does calling this cost us money?"
    "/shariah/screens": "Append-only screening verdict log. Method, not holdings.",
    "/stock/{symbol}/explain": "Explanation of a screening verdict.",
    "/stock/{symbol}/option-strategy": "Proposes a contract. Approves nothing.",
    "/stock/{symbol}/profile": "Public company profile plus its screen.",
}

# Anything FastAPI adds for itself.
FRAMEWORK_PREFIXES = ("/openapi", "/docs", "/redoc")


def _requires_an_actor(endpoint) -> bool:
    """True when the handler takes an authenticated actor dependency.

    Checks the signature rather than calling anything: every gate in this app is
    expressed as `actor: auth.Actor = Depends(get_*_actor)`.
    """
    try:
        signature = str(inspect.signature(endpoint))
    except (TypeError, ValueError):
        return False
    return "_actor" in signature


def test_every_route_is_authenticated_or_explicitly_public():
    undeclared = []
    for route in app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if not path or endpoint is None:
            continue
        if path.startswith(FRAMEWORK_PREFIXES):
            continue
        if _requires_an_actor(endpoint):
            continue
        if path in PUBLIC_ROUTES:
            continue
        undeclared.append((sorted(getattr(route, "methods", set()) or set()), path))

    assert not undeclared, (
        "These routes are reachable without credentials and are not declared public:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(undeclared, key=lambda r: r[1]))
        + "\n\nEither add an actor dependency, or add the path to PUBLIC_ROUTES with a "
        "reason. Do not add it to PUBLIC_ROUTES just to make this pass -- two real "
        "data leaks got in exactly that way."
    )


def test_account_and_position_routes_are_never_public():
    """The specific failure that happened twice, stated directly.

    Anything serving holdings, balances or order state must be gated. Kept
    separate from the allowlist test so it cannot be silenced by adding an entry
    to PUBLIC_ROUTES.
    """
    sensitive_markers = ("/positions", "/portfolio", "/paper/account", "/approvals")
    leaked = []
    for route in app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if not path or endpoint is None:
            continue
        if not any(marker in path for marker in sensitive_markers):
            continue
        # /p3/* portfolios are a separate virtual-book surface with their own
        # RBAC (propose/approve/execute), checked by test_p3_unauth_surface.py.
        if path.startswith("/p3/"):
            continue
        if not _requires_an_actor(endpoint):
            leaked.append((sorted(getattr(route, "methods", set()) or set()), path))

    assert not leaked, (
        "Routes serving account or position data with no authentication:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(leaked, key=lambda r: r[1]))
    )


# Routes whose handlers reach a metered external service, so that calling them
# costs real money. Kept as an explicit list because a static check cannot
# reliably follow the call graph into news_summarizer / copilot_api. Add to this
# list whenever a route starts spending.
COST_INCURRING_ROUTES = [
    "/news",  # up to NEWS_AI_SUMMARY_MAX_ARTICLES OpenRouter calls per request
    "/copilot/explain",  # one OpenRouter call, large prompt
    "/copilot/research",  # one OpenRouter call, larger prompt
]


def test_routes_that_spend_money_are_authenticated():
    """Data disclosure is only half of why a route needs auth.

    `/news` sat on the public allowlist with the reason "Market news. Not
    account data." That was accurate and irrelevant: each request fans out into
    several OpenRouter calls, so an anonymous caller could bill the owner's LLM
    account as fast as nginx would let them through -- on the order of $850/day
    at the existing rate limit, with no spend cap anywhere in the codebase.

    There is still no spend cap. Until there is, authentication is the only
    thing standing between a stranger and the bill.
    """
    by_path = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if path:
            by_path.setdefault(path, getattr(route, "endpoint", None))

    ungated = []
    for path in COST_INCURRING_ROUTES:
        endpoint = by_path.get(path)
        assert endpoint is not None, f"{path} is listed as cost-incurring but does not exist"
        if not _requires_an_actor(endpoint):
            ungated.append(path)

    assert not ungated, (
        "These routes spend money on every call and are reachable without credentials:\n"
        + "\n".join(f"  {p}" for p in ungated)
        + "\n\nThere is no spend cap in this codebase. Auth is the only control."
    )


def main():
    test_every_route_is_authenticated_or_explicitly_public()
    print(f"PASS: every route is authenticated or one of {len(PUBLIC_ROUTES)} declared public")
    test_account_and_position_routes_are_never_public()
    print("PASS: no account or position route is publicly readable")
    test_routes_that_spend_money_are_authenticated()
    print(f"PASS: all {len(COST_INCURRING_ROUTES)} cost-incurring routes require credentials")


if __name__ == "__main__":
    main()
