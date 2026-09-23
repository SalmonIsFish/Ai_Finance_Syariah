"""The bridge client's contract: assert the request that gets BUILT, not just the reply.

Per CLAUDE.md's testing conventions, network access goes through one replaceable
module-level seam and tests swap it. Here that seam is bridge.client._request; nothing
in this file reaches the deployment.

The load-bearing assertions are the two about the operator key. It is the only thing
standing between this process and a real broker submission, so "it is attached to
exactly two routes and nowhere else" is checked against every route in the allowlist
rather than spot-checked.
"""

from __future__ import annotations

import threading

import pytest
from bridge import client as client_module
from bridge.client import (
    OPERATOR_HEADER,
    AmanahClient,
    BridgeConfig,
    OperatorKeyMisuse,
    load_bridge_config,
)
from bridge.ratelimit import ZoneLimiter
from bridge.routes import OPERATOR_ROUTES, ROUTES, ZONE_GENERAL, ZONE_WRITE

# Generous budgets so the token bucket never paces a unit test.
_FAST_BUDGETS = {ZONE_WRITE: (6000.0, 500.0), ZONE_GENERAL: (6000.0, 500.0)}

OPERATOR_KEY = "f" * 64
PASSWORD = "correct-horse-battery-staple"


def _config(*, operator: bool = True) -> BridgeConfig:
    return BridgeConfig(
        base_url="https://example.invalid",
        basic_user="project_owner",
        basic_password=PASSWORD,
        operator_key=OPERATOR_KEY if operator else None,
    )


class Recorder:
    """Stands in for bridge.client._request and records what was built."""

    def __init__(self, responses=None):
        self.calls = []
        self._responses = list(responses or [])

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        if self._responses:
            return self._responses.pop(0)
        return {"ok": True, "status_code": 200, "data": {"ok": True}}


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(client_module, "_request", rec)
    return rec


@pytest.fixture
def client():
    return AmanahClient(_config(), limiter=ZoneLimiter(_FAST_BUDGETS))


def _params_for(route):
    """Minimal path params so every route can be built."""
    params = {}
    if "{ticker}" in route.path:
        params["ticker"] = "AAPL"
    if "{symbol}" in route.path:
        params["symbol"] = "AAPL"
    if "{publication_id}" in route.path:
        params["publication_id"] = "sc-sac-my-2026-05-29"
    if "{queue_id}" in route.path:
        params["queue_id"] = 11
    return params


def test_basic_auth_is_sent_on_every_route(recorder, client):
    for name, route in ROUTES.items():
        recorder.calls.clear()
        body = {"probe": True} if route.method == "POST" else None
        client.call(name, path_params=_params_for(route), body=body, bypass_cache=True)
        header = recorder.calls[0]["headers"].get("Authorization", "")
        assert header.startswith("Basic "), f"{name} was built without Basic auth"


def test_operator_header_reaches_exactly_two_routes(recorder, client):
    carried = set()
    for name, route in ROUTES.items():
        recorder.calls.clear()
        body = {"probe": True} if route.method == "POST" else None
        client.call(name, path_params=_params_for(route), body=body, bypass_cache=True)
        if OPERATOR_HEADER in recorder.calls[0]["headers"]:
            carried.add(name)
    assert carried == set(OPERATOR_ROUTES), (
        "the operator key must reach exactly the two nginx-gated routes; "
        f"it reached {sorted(carried)}"
    )


def test_a_route_marked_operator_but_not_listed_raises():
    """A future edit that widens broker access must fail loudly, not silently."""
    from dataclasses import replace

    rogue = replace(ROUTES["shariah_status"], needs_operator=True)
    with pytest.raises(OperatorKeyMisuse):
        client_module._operator_header_for(rogue, _config())


def test_the_mcp_process_cannot_build_an_execute_request():
    """The LLM-facing process has no operator key, so execute is unreachable there."""
    mcp_client = AmanahClient(_config(operator=False), limiter=ZoneLimiter(_FAST_BUDGETS))
    with pytest.raises(OperatorKeyMisuse):
        mcp_client.call("paper_execute", path_params={"queue_id": 11}, body={"x": 1})


def test_load_bridge_config_refuses_a_relay_without_the_operator_key():
    env = {"BRIDGE_BASIC_USER": "project_owner", "BRIDGE_BASIC_PASSWORD": PASSWORD}
    assert load_bridge_config(env=env).operator_key is None
    with pytest.raises(RuntimeError):
        load_bridge_config(with_operator=True, env=env)


def test_the_client_exposes_no_way_to_pass_a_url():
    public = [name for name in dir(AmanahClient) if not name.startswith("_")]
    for name in public:
        attribute = getattr(AmanahClient, name)
        if not callable(attribute):
            continue
        annotations = getattr(attribute, "__annotations__", {})
        assert "url" not in annotations, f"{name} accepts a url; routes must be named"


def test_a_post_is_never_cached(recorder, client):
    for _ in range(3):
        client.call("paper_preview", body={"symbol": "AAPL", "quantity": 1})
    assert len(recorder.calls) == 3, "a POST must reach the backend every time"


def test_a_cache_hit_makes_no_second_request(recorder, client):
    first = client.call("shariah_status", path_params={"ticker": "AAPL"})
    second = client.call("shariah_status", path_params={"ticker": "AAPL"})
    assert first["cached"] is False
    assert second["cached"] is True
    assert len(recorder.calls) == 1


def test_a_failure_is_never_cached(monkeypatch, client):
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)
    # Enough 503s that the first call exhausts its retries and genuinely fails; a short
    # queue would let a retry succeed and be cached, which is correct behaviour and
    # would make this test pass for the wrong reason.
    failures = [{"ok": False, "status_code": 503, "data": {}, "reason": "http_503"}] * 10
    rec = Recorder(failures)
    monkeypatch.setattr(client_module, "_request", rec)

    first = client.call("shariah_status", path_params={"ticker": "AAPL"})
    assert first["status"] == client_module.STATUS_UNAVAILABLE
    calls_after_failure = len(rec.calls)

    client.call("shariah_status", path_params={"ticker": "AAPL"})
    assert len(rec.calls) > calls_after_failure, (
        "a 503 is a fact about the droplet, not about the company -- it must not be cached"
    )


def test_single_flight_collapses_concurrent_identical_reads(monkeypatch, client):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_request(method, url, headers, body, timeout):
        calls.append(url)
        started.set()
        release.wait(timeout=5)
        return {"ok": True, "status_code": 200, "data": {"ticker": "AAPL"}}

    monkeypatch.setattr(client_module, "_request", slow_request)

    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                client.call("shariah_status", path_params={"ticker": "AAPL"})
            )
        )
        for _ in range(4)
    ]
    threads[0].start()
    started.wait(timeout=5)
    for thread in threads[1:]:
        thread.start()
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert len(calls) == 1, (
        "four callers asking the same question must cost the droplet one PBKDF2 "
        f"verification, not four; saw {len(calls)}"
    )
    assert len(results) == 4


def test_a_retryable_route_backs_off_and_stops(monkeypatch, client):
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)
    rec = Recorder([{"ok": False, "status_code": 429, "data": {}, "reason": "http_429"}] * 5)
    monkeypatch.setattr(client_module, "_request", rec)
    result = client.call("shariah_status", path_params={"ticker": "AAPL"})
    assert len(rec.calls) == client_module.MAX_ATTEMPTS
    assert result["status"] == client_module.STATUS_RATE_LIMITED


def test_execute_is_never_retried(monkeypatch, client):
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)
    rec = Recorder([{"ok": False, "status_code": 503, "data": {}, "reason": "http_503"}] * 5)
    monkeypatch.setattr(client_module, "_request", rec)
    client.call("paper_execute", path_params={"queue_id": 11}, body={"confirmation_phrase": "x"})
    assert len(rec.calls) == 1, (
        "a retried execute is how you get two orders; it must be attempted exactly once"
    )


def test_approval_is_never_retried(monkeypatch, client):
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)
    rec = Recorder([{"ok": False, "status_code": 503, "data": {}, "reason": "http_503"}] * 5)
    monkeypatch.setattr(client_module, "_request", rec)
    client.call("paper_approval", body={"preview": {}, "approved": True})
    assert len(rec.calls) == 1


def test_a_missing_path_parameter_fails_closed(client):
    with pytest.raises(KeyError):
        client.call("shariah_status")


def test_an_unknown_route_fails_closed(client):
    with pytest.raises(KeyError):
        client.call("/api/shariah/AAPL")


def test_refresh_disposal_clock_bypasses_the_cache(recorder, client):
    """Calling /portfolio/compliance is what advances the clock; a cache hit stops it."""
    client.read_compliance()
    client.read_compliance()
    assert len(recorder.calls) == 1, "the cached read should be served from cache"
    client.refresh_disposal_clock()
    assert len(recorder.calls) == 2, (
        "the scheduled refresh must always reach the droplet -- holdings_compliance."
        "apply_disposal_clock only runs when this endpoint is actually called"
    )
