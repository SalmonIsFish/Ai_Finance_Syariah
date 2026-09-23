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

# Files that legitimately carry paths for reasons other than calling the API.
PATH_EXEMPT = {"routes.py"}


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


def main() -> None:
    check_only_routes_py_spells_a_path()
    check_the_operator_key_is_attached_to_exactly_two_routes()
    check_the_header_function_refuses_every_other_route()
    check_no_route_spends_money()
    check_writes_are_never_cached_and_never_blindly_retried()
    check_every_route_is_reachable_by_name_only()
    print("PASS: routes.py is the only place a path is spelled; the operator key reaches two.")


if __name__ == "__main__":
    main()
