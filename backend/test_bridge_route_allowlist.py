"""routes.py is the only module allowed to spell an API path.

The bridge's claim is that a model cannot invent a request. That rests on two things:
`AmanahClient.call` takes a route *name* rather than a URL, and no other bridge module
builds a path of its own. The first is enforced by the client's signature; this file
enforces the second, and checks the operator key against every route rather than
spot-checking the two that carry it.

Static, in the spirit of test_single_screening_path.py -- a runtime test would catch only
the paths it happened to exercise.
"""

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
BRIDGE = BACKEND / "bridge"

# Anything that looks like an API path: a leading slash and a lowercase letter.
PATH_SHAPED = re.compile(r"^/[a-z]")

# Files that legitimately carry paths for reasons other than calling the Amanah API.
# relay.py talks to a second host entirely -- api.telegram.org -- so it spells Telegram's
# own URL prefix. check_the_relay_spells_no_amanah_path below holds it to the real rule.
PATH_EXEMPT = {"routes.py", "relay.py"}


def _bridge_files() -> list[Path]:
    return [p for p in sorted(BRIDGE.rglob("*.py")) if "__pycache__" not in p.parts]


def _code_strings(path: Path) -> list[str]:
    """String literals in executable code; docstrings and comments are prose, not paths."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def check_only_routes_py_spells_a_path() -> None:
    from bridge.routes import ROUTES

    known = {route.path for route in ROUTES.values()}
    offenders = []
    for path in _bridge_files():
        if path.name in PATH_EXEMPT:
            continue
        for value in _code_strings(path):
            if PATH_SHAPED.match(value) and value not in known:
                offenders.append(f"{path.relative_to(BACKEND).as_posix()}: {value!r}")
    assert not offenders, (
        "only routes.py may contain an API path literal; an ad-hoc path is a route the "
        f"allowlist does not cover: {offenders}"
    )


def check_the_relay_spells_no_amanah_path() -> None:
    """The relay is exempt from the generic scan only because of Telegram.

    It is the process that holds the operator key, so it is the last place an ad-hoc
    Amanah path should be allowed. Every backend call it makes must still go through a
    route name.
    """
    source = (BRIDGE / "relay.py").read_text(encoding="utf-8")
    amanah_shaped = re.compile(r"[\"']/(?:api|paper|stock|portfolio|positions|approvals|audit)")
    matches = amanah_shaped.findall(source)
    assert not matches, f"relay.py spells an Amanah API path directly: {matches}"

    # And the Telegram host it IS allowed to name must be the real one.
    from bridge.relay import TELEGRAM_API

    assert TELEGRAM_API == "https://api.telegram.org"


def check_the_operator_key_is_attached_to_exactly_two_routes() -> None:
    """Checked against every route, not the two we expect -- the point is what is absent."""
    from bridge.routes import OPERATOR_ROUTES, ROUTES

    marked = {name for name, route in ROUTES.items() if route.needs_operator}
    assert marked == set(OPERATOR_ROUTES), (
        "Route.needs_operator and OPERATOR_ROUTES must not drift: "
        f"{sorted(marked ^ set(OPERATOR_ROUTES))}"
    )
    assert marked == {"paper_execute", "paper_reconcile"}, sorted(marked)


def check_the_header_function_refuses_every_other_route() -> None:
    from bridge.client import OPERATOR_HEADER, BridgeConfig, OperatorKeyMisuse
    from bridge.client import _operator_header_for
    from bridge.routes import OPERATOR_ROUTES, ROUTES

    config = BridgeConfig(
        base_url="https://example.invalid",
        basic_user="project_owner",
        basic_password="pw",
        operator_key="f" * 64,
    )
    for name, route in ROUTES.items():
        headers = _operator_header_for(route, config)
        if name in OPERATOR_ROUTES:
            assert OPERATOR_HEADER in headers, name
        else:
            assert headers == {}, f"{name} received the operator key"

    # A route re-marked as needing the key, without being added to OPERATOR_ROUTES, must
    # raise rather than quietly widen broker access.
    from dataclasses import replace

    rogue = replace(ROUTES["quant_signal"], needs_operator=True)
    try:
        _operator_header_for(rogue, config)
    except OperatorKeyMisuse:
        pass
    else:  # pragma: no cover - the assertion below is the failure message
        raise AssertionError("a rogue operator route did not raise")


def check_no_route_spends_money() -> None:
    """/news and /copilot/* are absent by design; the backend has no spend cap anywhere."""
    from bridge.routes import FORBIDDEN_PATHS, ROUTES

    present = {route.path for route in ROUTES.values()} & set(FORBIDDEN_PATHS)
    assert not present, (
        f"these routes spend real OpenRouter money and must not be in the allowlist: {sorted(present)}"
    )
    for path in FORBIDDEN_PATHS:
        assert path not in {route.path for route in ROUTES.values()}


def check_writes_are_never_cached_and_never_blindly_retried() -> None:
    from bridge.routes import ROUTES

    for name, route in ROUTES.items():
        if route.method == "POST":
            assert route.cache_ttl_seconds == 0.0, f"{name} caches a POST"
    # A retried submission is how you get two orders.
    assert ROUTES["paper_approval"].retryable is False
    assert ROUTES["paper_execute"].retryable is False
    assert ROUTES["paper_reconcile"].retryable is False


def check_every_route_is_reachable_by_name_only() -> None:
    """route_for fails closed, so an invented name cannot fall through to a default."""
    from bridge.routes import ROUTES, route_for

    for name in ROUTES:
        assert route_for(name).name == name
    for invented in ("/api/shariah/AAPL", "paper_submit", ""):
        try:
            route_for(invented)
        except KeyError:
            continue
        raise AssertionError(f"route_for accepted {invented!r}")


def check_no_scheduled_job_previews_or_spends() -> None:
    """A polling loop must not run order-shaped actions or spend model money.

    /paper/preview writes an evidence record with `source: "preview"` -- the one field
    that separates an order the owner actually considered from watchlist scan noise. A
    scheduled preview would destroy that distinction permanently, and no migration brings
    it back. /news and /copilot/* have no spend cap anywhere in the backend.
    """
    import ast

    from bridge.routes import FORBIDDEN_SCHEDULED_ROUTES

    source = (BRIDGE / "schedules.py").read_text(encoding="utf-8")
    called = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "call" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    called.add(first.value)

    forbidden = called & set(FORBIDDEN_SCHEDULED_ROUTES)
    assert not forbidden, f"a scheduled job calls an order-shaped route: {sorted(forbidden)}"

    from bridge.routes import ROUTES

    unknown = called - set(ROUTES)
    assert not unknown, f"a scheduled job names a route that does not exist: {sorted(unknown)}"

    writes = {name for name in called if ROUTES[name].method == "POST"}
    assert not writes, f"a scheduled job performs a write: {sorted(writes)}"


def check_the_disposal_clock_job_bypasses_the_cache() -> None:
    """Calling /portfolio/compliance IS the clock; a cache hit silently stops it."""
    source = (BRIDGE / "schedules.py").read_text(encoding="utf-8")
    assert "refresh_disposal_clock" in source
    assert "read_compliance()" not in source, (
        "the scheduled job must use the uncached path -- holdings_compliance."
        "apply_disposal_clock only runs when the endpoint is really called"
    )


def main() -> None:
    check_only_routes_py_spells_a_path()
    check_the_relay_spells_no_amanah_path()
    check_no_scheduled_job_previews_or_spends()
    check_the_disposal_clock_job_bypasses_the_cache()
    check_the_operator_key_is_attached_to_exactly_two_routes()
    check_the_header_function_refuses_every_other_route()
    check_no_route_spends_money()
    check_writes_are_never_cached_and_never_blindly_retried()
    check_every_route_is_reachable_by_name_only()
    print("PASS: routes.py is the only place a path is spelled; the operator key reaches two.")


if __name__ == "__main__":
    main()
