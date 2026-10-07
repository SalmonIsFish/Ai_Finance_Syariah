"""GET /market/clock reports Alpaca's US clock for the dashboard's status bar.

The property that matters is the one check_market_clock already promises:
`is_open` is None -- never False -- when the clock cannot be read. A status bar
that showed "Closed" for an unreachable broker would be stating something nobody
measured. Network is never touched: the adapter's credential and request seams
are swapped, and the route is reached through the real FastAPI app.
"""

from fastapi.testclient import TestClient

import alpaca_paper_adapter
import auth
import local_api

FAKE_CREDENTIALS = {"key_id": "test", "secret_key": "test"}


def owner_client() -> TestClient:
    local_api.app.dependency_overrides[local_api.get_owner_actor] = lambda: auth.Actor(
        username="project_owner", role="admin"
    )
    return TestClient(local_api.app)


def with_seams(credentials, response):
    """Run the route with the adapter's two network seams swapped; restore after."""
    original_credentials = alpaca_paper_adapter.alpaca_credentials
    original_request = alpaca_paper_adapter.alpaca_request
    requests = []

    def fake_request(method, path, **kwargs):
        requests.append((method, path))
        return response

    alpaca_paper_adapter.alpaca_credentials = lambda: credentials
    alpaca_paper_adapter.alpaca_request = fake_request
    try:
        body = owner_client().get("/market/clock")
    finally:
        alpaca_paper_adapter.alpaca_credentials = original_credentials
        alpaca_paper_adapter.alpaca_request = original_request
    return body, requests


def check_an_open_market_is_reported_open() -> None:
    body, requests = with_seams(
        FAKE_CREDENTIALS,
        {
            "ok": True,
            "data": {
                "is_open": True,
                "next_open": "2026-10-08T13:30:00Z",
                "next_close": "2026-10-07T20:00:00Z",
                "timestamp": "2026-10-07T15:00:00Z",
            },
        },
    )
    assert body.status_code == 200, body.text
    payload = body.json()
    assert payload["status"] == "ok" and payload["is_open"] is True, payload
    assert payload["next_close"] == "2026-10-07T20:00:00Z", payload
    # Assert the request that was built, not just the answer.
    assert requests == [("GET", "/v2/clock")], requests


def check_an_unreadable_clock_is_unknown_not_closed() -> None:
    body, requests = with_seams(FAKE_CREDENTIALS, {"ok": False, "error": "timeout"})
    payload = body.json()
    assert payload["status"] == "unreachable", payload
    assert payload["is_open"] is None, "an unreadable clock must not read as closed"

    body, requests = with_seams(None, {"ok": True, "data": {"is_open": True}})
    payload = body.json()
    assert payload == {"status": "credentials_missing", "is_open": None}, payload
    assert requests == [], "no credentials must mean no request at all"


def check_the_route_requires_the_owner() -> None:
    local_api.app.dependency_overrides.pop(local_api.get_owner_actor, None)
    response = TestClient(local_api.app).get("/market/clock")
    assert response.status_code in (401, 403), response.status_code


def main() -> None:
    try:
        check_an_open_market_is_reported_open()
        check_an_unreadable_clock_is_unknown_not_closed()
    finally:
        local_api.app.dependency_overrides.pop(local_api.get_owner_actor, None)
    check_the_route_requires_the_owner()
    print("PASS: /market/clock reports open, closed or unknown -- never closed for unknown.")


if __name__ == "__main__":
    main()
