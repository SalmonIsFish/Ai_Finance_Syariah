import pytest
from fastapi.testclient import TestClient
import json
import base64

from replit_app import app as replit_app
import auth


@pytest.fixture
def auth_client(monkeypatch):
    users_json = json.dumps(
        [
            {
                "username": "project_owner",
                "password_hash": auth.hash_password("owner_pass", iterations=100),
                "role": "admin",
            },
            {
                "username": "other_user",
                "password_hash": auth.hash_password("other_pass", iterations=100),
                "role": "admin",
            },
        ]
    )
    monkeypatch.setenv("SC_ADMIN_AUTH_USERS", users_json)
    return TestClient(replit_app)


def test_dashboard_endpoints_require_owner_auth(auth_client):
    endpoints = [
        "/dashboard/",
        "/health",
        "/system/mode",
        "/paper/status",
        "/approvals",
        "/watchlist",
        "/opportunity-alerts",
        "/portfolio",
        "/portfolio/compliance",
        # Both were publicly readable until 2026-09-22 while their siblings
        # /portfolio and /paper/positions/live were gated -- an asymmetry that
        # leaked real holdings (symbol, account suffix, cost basis, P&L) and
        # account equity to anyone who asked. Missed when the dashboard was
        # locked down because only the "live" variants were on the list.
        "/positions",
        "/portfolio/history",
        # Not account data, but every call fans out into several OpenRouter
        # requests. Anonymous access was uncapped spend on the owner's account.
        "/news",
        "/investment-committee",
        "/market-overview",
        "/execution-audit",
        "/portfolio/history/live",
        "/paper/account",
        "/paper/positions/live",
        "/audit",
    ]

    owner_headers = {
        "Authorization": f"Basic {base64.b64encode(b'project_owner:owner_pass').decode()}"
    }
    other_headers = {
        "Authorization": f"Basic {base64.b64encode(b'other_user:other_pass').decode()}"
    }

    for ep in endpoints:
        # Without credentials -> 401
        res = auth_client.get(ep)
        assert res.status_code == 401, f"{ep} did not return 401"

        # Wrong user -> 403
        res = auth_client.get(ep, headers=other_headers)
        assert res.status_code == 403, f"{ep} did not return 403"

        # Correct user -> 200 (or whatever success code)
        # Note: Some endpoints might fail with 500/400 because of missing query params or DB state,
        # but they shouldn't return 401/403. We just assert they don't return 401/403.
        res = auth_client.get(ep, headers=owner_headers)
        assert res.status_code not in (401, 403), f"{ep} rejected valid owner auth"
