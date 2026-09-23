"""The bridge and the backend must agree about the route, and about what it returns.

**This file exists because of a bug that green tests hid.** `execution_markets` was added
to `trading_mode_status()`, which serves `/system/mode`, and the relay was then pointed at
`/paper/status` -- a different handler that builds its own dict. The relay read a key the
backend never sent, so `market_execution()` failed closed for every market including US and
the two-tap execute path was dead on arrival.

`test_bridge_two_tap.py` and `test_bridge_malaysia_no_execute.py` both covered that code
and both passed, because both stub a client whose fake response contains the key. They
asserted the relay's behaviour *given the response the author intended*, which is a
different claim from the one that mattered.

So nothing here fakes an HTTP response. Every assertion runs against the real FastAPI app
through TestClient, and the one static check compares the path the client names with the
paths the app actually serves. A contract needs both halves in the same test.
"""

import os

import pytest
from bridge.client import STATUS_OK
from bridge.relay import Relay
from bridge.routes import ROUTES
from fastapi.testclient import TestClient

import auth

_ENV_KEYS = ["PAPER_EXECUTION_ADAPTER", "PAPER_EXECUTION_ADAPTER_MY", "PAPER_EXECUTION_ENABLED"]

REQUIRED_MARKET_FIELDS = {"adapter", "enabled", "reason"}


@pytest.fixture(autouse=True)
def _env():
    original = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ["PAPER_EXECUTION_ENABLED"] = "true"
    os.environ.pop("PAPER_EXECUTION_ADAPTER_MY", None)
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def client():
    import local_api

    local_api.app.dependency_overrides[local_api.get_owner_actor] = lambda: auth.Actor(
        username="project_owner", role="admin"
    )
    yield TestClient(local_api.app)
    local_api.app.dependency_overrides.pop(local_api.get_owner_actor, None)


# --- the route the client names must be the route the app serves ---------------------


def test_every_bridge_route_exists_on_the_real_app():
    """A static check the fixtures could never make: do both halves name the same path?

    The bug was not a wrong value, it was the client and the server talking about two
    different handlers. Path parameters are compared by shape, since FastAPI and the
    allowlist may spell a parameter differently.
    """
    import re

    import local_api

    def shape(path):
        return re.sub(r"\{[^}]+\}", "{}", path)

    served = {shape(route.path) for route in local_api.app.routes if hasattr(route, "path")}
    missing = [r.path for r in ROUTES.values() if shape(r.path) not in served]
    assert not missing, f"the bridge names routes the app does not serve: {sorted(missing)}"


def test_the_relay_reads_the_route_it_thinks_it_does():
    assert ROUTES["paper_status"].path == "/paper/status"
    assert ROUTES["paper_status"].method == "GET"


# --- the real response shape ----------------------------------------------------------


def test_paper_status_really_returns_execution_markets(client):
    """The assertion that was missing. Against the app, not a fixture."""
    body = client.get("/paper/status").json()

    assert "execution_markets" in body, (
        "the relay reads this key from /paper/status; without it every market fails "
        "closed and the two-tap execute path is dead"
    )
    markets = body["execution_markets"]
    assert set(markets) >= {"US", "MY"}
    for market, entry in markets.items():
        assert REQUIRED_MARKET_FIELDS <= set(entry), (market, entry)


def test_system_mode_returns_the_same_markets(client):
    """Two views of one rule. If they disagree, one of them is lying to a caller."""
    from_status = client.get("/paper/status").json()["execution_markets"]
    from_mode = client.get("/system/mode").json()["execution_markets"]
    assert from_status == from_mode


def test_us_is_enabled_and_malaysia_is_not_by_default(client):
    markets = client.get("/paper/status").json()["execution_markets"]

    assert markets["US"]["enabled"] is True
    assert markets["US"]["adapter"] == "alpaca_mcp"

    assert markets["MY"]["enabled"] is False
    assert markets["MY"]["adapter"] == "disabled"
    assert "PAPER_EXECUTION_ADAPTER_MY" in markets["MY"]["reason"]


def test_enabling_malaysia_shows_up_on_the_route(client):
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"
    markets = client.get("/paper/status").json()["execution_markets"]
    assert markets["MY"]["enabled"] is True
    assert markets["MY"]["adapter"] == "moomoo"
    assert markets["US"]["adapter"] == "alpaca_mcp", "enabling MY must not move US"


def test_an_enabled_market_does_not_carry_a_disabled_reason(client):
    """The PASS path used to have no `reason`, and the relay turned None into
    "execution is not enabled for this market" -- for a market that is."""
    us = client.get("/paper/status").json()["execution_markets"]["US"]
    assert us["reason"], "an enabled market needs an affirmative reason, not None"
    assert "not enabled" not in us["reason"].lower()
    assert "alpaca_mcp" in us["reason"]


def test_the_fields_the_dashboard_reads_are_untouched(client):
    """Adding a key must not disturb the ones a live UI already depends on."""
    body = client.get("/paper/status").json()
    for field in (
        "mode",
        "trading_mode",
        "approval_required",
        "paper_execution_enabled",
        "broker_submission",
        "live_trading",
    ):
        assert field in body, field
    assert body["live_trading"] is False


# --- the relay, fed the real response ------------------------------------------------


class RealResponseClient:
    """Wraps TestClient so the relay sees the genuine backend payload, unedited."""

    def __init__(self, test_client):
        self._client = test_client
        self.calls = []

        class _Config:
            operator_key = None

        self._config = _Config()

    def call(self, route_name, *, path_params=None, query=None, body=None, bypass_cache=False):
        self.calls.append(route_name)
        route = ROUTES[route_name]
        response = self._client.get(route.path)
        status = STATUS_OK if response.status_code == 200 else "UNAVAILABLE"
        return {"status": status, "route": route_name, "data": response.json()}


def _relay(test_client, connection):
    return Relay(
        RealResponseClient(test_client),
        connection,
        token="t",
        owner_user_id=1,
        chat_id=-1,
        transport=lambda *a, **k: {"ok": True, "data": {"result": {"message_id": 1}}},
    )


@pytest.fixture
def db():
    from bridge import proposals

    connection = proposals.connect(":memory:")
    yield connection
    connection.close()


def test_the_relay_reads_the_real_payload_correctly(client, db):
    """End to end on the contract: real route, real handler, real parsing."""
    relay = _relay(client, db)

    us = relay.market_execution("US")
    assert us["enabled"] is True, "the relay must see US as executable against the real app"
    assert us["adapter"] == "alpaca_mcp"

    my = relay.market_execution("MY")
    assert my["enabled"] is False
    assert "PAPER_EXECUTION_ADAPTER_MY" in my["reason"]


def test_enabling_malaysia_reaches_the_relay(client, db):
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"
    relay = _relay(client, db)
    assert relay.market_execution("MY")["enabled"] is True


def test_a_market_the_backend_never_mentions_is_refused(client, db):
    relay = _relay(client, db)
    assert relay.market_execution("HK")["enabled"] is False
