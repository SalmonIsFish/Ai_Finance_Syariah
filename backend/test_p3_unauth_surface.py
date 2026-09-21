"""Regression test for the unauthenticated P3 / copilot surface.

Before this fix, `/api/p3/*` sat under the `/api/*` prefix whose structural
invariant (test_screening_api.py::test_10, test_screening_api_universe_status.py
::test_9) promises GET/HEAD-only, and nginx routes everything unmatched to a bare
`location /` with no auth. `POST /api/p3/portfolios` was therefore an
unauthenticated database write reachable from the public internet, and the four
P3 GETs leaked portfolio and order state.

The routes moved to `/p3/*` and `/copilot/*` and gained auth dependencies. This
test pins both halves: the old paths are gone, and every moved route rejects an
unauthenticated caller.

Deliberately configures no credentials and installs no dependency_overrides --
an unauthenticated request must be refused regardless of how SC_ADMIN_AUTH_USERS
is set, and a leaked override or env var is the exact bug class that previously
made an auth test pass while auth was silently bypassed.
"""

from fastapi.testclient import TestClient

from local_api import app

client = TestClient(app)

MOVED_ROUTES = [
    ("get", "/p3/portfolios"),
    ("post", "/p3/portfolios"),
    ("get", "/p3/portfolios/1"),
    ("get", "/p3/portfolios/1/positions"),
    ("get", "/p3/portfolios/1/orders"),
    ("post", "/p3/portfolios/1/proposals"),
    ("post", "/p3/portfolios/1/orders/1/approve"),
    ("post", "/p3/portfolios/1/orders/1/execute"),
    ("post", "/copilot/explain"),
    ("post", "/copilot/research"),
]

RETIRED_PATHS = [
    ("get", "/api/p3/portfolios"),
    ("post", "/api/p3/portfolios"),
    ("get", "/api/p3/portfolios/1/positions"),
    ("post", "/api/p3/portfolios/1/proposals"),
    ("post", "/api/explain"),
    ("post", "/api/research/copilot"),
]


def test_moved_routes_reject_unauthenticated_callers():
    for method, path in MOVED_ROUTES:
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code == 401, (
            f"{method.upper()} {path} returned {response.status_code}, expected 401 "
            f"-- this route is reachable without credentials"
        )


def test_retired_api_paths_are_gone():
    """404 or 405 both satisfy the intent: the old path no longer serves this
    method. POST /api/research/copilot returns 405 rather than 404 because the
    surviving read-only GET /api/research/{ticker} still matches that path with
    ticker="copilot" -- the mutation is refused either way.
    """
    for method, path in RETIRED_PATHS:
        kwargs = {"json": {}} if method == "post" else {}
        response = getattr(client, method)(path, **kwargs)
        assert response.status_code in (404, 405), (
            f"{method.upper()} {path} returned {response.status_code}, expected 404/405 "
            f"-- the old unauthenticated path is still serving this method"
        )


def test_api_prefix_exposes_no_mutation():
    """Mirrors the protected invariant tests without importing them, so this
    file fails on its own if a POST is ever added back under /api/."""
    offenders = [
        (sorted(route.methods or set()), route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/")
        and not (getattr(route, "methods", set()) or set()) <= {"GET", "HEAD"}
    ]
    assert offenders == [], f"mutating routes found under /api/: {offenders}"


def main():
    test_moved_routes_reject_unauthenticated_callers()
    print(f"PASS: all {len(MOVED_ROUTES)} moved routes reject unauthenticated callers")
    test_retired_api_paths_are_gone()
    print(f"PASS: all {len(RETIRED_PATHS)} retired /api/ paths refuse the method (404/405)")
    test_api_prefix_exposes_no_mutation()
    print("PASS: no mutating route remains under /api/")


if __name__ == "__main__":
    main()
