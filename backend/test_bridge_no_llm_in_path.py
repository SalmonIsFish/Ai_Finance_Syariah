"""No LLM may originate a verdict, and no bot may reach the broker.

This closes a gap the repo documents about itself. docs/PHASE2A_REPORT.md:150 says the
no-LLM-in-the-decision-path rule was "Verified by direct grep of the decision path, not
test-asserted (there is nothing to call)". Once a bridge exists there IS something to
call, and a grep someone remembers to run is not an invariant.

It is a static check on purpose, in the spirit of test_single_screening_path.py: a runtime
test catches only the paths it happens to exercise, while this catches a new one the moment
it is written.

What it enforces:

1. Nothing under backend/bridge/ touches a model client. OpenClaw owns the model and the
   OpenRouter key; the bridge owns facts and formatting. They are separate processes.
2. Nothing outside backend/bridge/ imports the bridge. The server must not grow a
   dependency on its own client.
3. No tool names a write route except the one preview tool, and no tool names a route that
   spends money.
4. The chief of staff has no data access at all, so it cannot arbitrate in code.
5. Every role names tools that exist, and every tool belongs to some role.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
BRIDGE = BACKEND / "bridge"

# Model clients and transports. `openrouter` is checked as a literal too, because the one
# LLM transport in this repo is reached by URL as much as by import.
FORBIDDEN_IMPORTS = {
    "news_summarizer",
    "copilot_api",
    "openai",
    "anthropic",
    "litellm",
    "langchain",
}
FORBIDDEN_LITERALS = ("openrouter", "api.openai.com", "api.anthropic.com")

# The only write route any LLM-reachable tool may name. /paper/approval and
# /paper/execute are human actions carried out by the relay: if a bot could queue an
# order, the two-tap approval flow would be one tap with extra steps.
ALLOWED_WRITE_ROUTES = {"paper_preview"}


def _bridge_files() -> list[Path]:
    return [p for p in sorted(BRIDGE.rglob("*.py")) if "__pycache__" not in p.parts]


def _backend_files() -> list[Path]:
    return [
        p
        for p in sorted(BACKEND.rglob("*.py"))
        if "__pycache__" not in p.parts
        and not p.name.startswith("test_")
        and BRIDGE not in p.parents
        and p != BRIDGE
    ]


def _code_strings(path: Path) -> list[str]:
    """Every string literal in the file EXCEPT docstrings.

    Comments never reach the AST, and docstrings are skipped deliberately: this file and
    the bridge modules explain at length *why* OpenRouter is excluded, and a scan of raw
    source text flags that prose as a violation. The risk being guarded is a URL or a
    module name in executable code, so that is what gets read.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover
        return []
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


def _imported_modules(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:  # pragma: no cover - a broken file is another test's problem
        return set()
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def check_the_bridge_never_touches_a_model() -> None:
    offenders = []
    for path in _bridge_files():
        relative = path.relative_to(BACKEND).as_posix()
        for module in _imported_modules(path):
            root = module.split(".")[0]
            if root in FORBIDDEN_IMPORTS:
                offenders.append(f"{relative} imports {module}")
        for value in _code_strings(path):
            lowered = value.lower()
            for literal in FORBIDDEN_LITERALS:
                if literal in lowered:
                    offenders.append(f"{relative} has a code string containing '{literal}'")
    assert not offenders, (
        "the bridge must contain no LLM call of any kind -- OpenClaw owns the model and "
        f"the bridge owns the facts: {offenders}"
    )


def check_nothing_outside_the_bridge_imports_it() -> None:
    """The server must not depend on its own client; the dependency runs one way."""
    offenders = []
    for path in _backend_files():
        for module in _imported_modules(path):
            if module == "bridge" or module.startswith("bridge."):
                offenders.append(path.relative_to(BACKEND).as_posix())
    assert not offenders, (
        f"these backend modules import the bridge; the dependency must run one way: {sorted(set(offenders))}"
    )


def check_no_tool_can_reach_the_broker() -> None:
    from bridge.routes import FORBIDDEN_PATHS, OPERATOR_ROUTES, ROUTES
    from bridge.tools import TOOLS

    named_routes = {route for tool in TOOLS.values() for route in tool.routes}

    unknown = named_routes - set(ROUTES)
    assert not unknown, f"tools name routes that do not exist: {sorted(unknown)}"

    operator_reachable = named_routes & set(OPERATOR_ROUTES)
    assert not operator_reachable, (
        "no LLM-reachable tool may name an operator-gated route -- execution is a human "
        f"action carried out by the relay: {sorted(operator_reachable)}"
    )

    writes = {name for name in named_routes if ROUTES[name].method == "POST"}
    assert writes <= ALLOWED_WRITE_ROUTES, (
        f"tools may name no write route except {sorted(ALLOWED_WRITE_ROUTES)}: {sorted(writes)}"
    )

    declared_writes = {tool.name for tool in TOOLS.values() if tool.writes}
    for name in declared_writes:
        assert set(TOOLS[name].routes) & writes, (
            f"tool '{name}' declares writes=True but names no write route"
        )

    # Money. /news and /copilot/* have no spend cap anywhere in the backend.
    paths = {ROUTES[name].path for name in named_routes}
    assert not (paths & set(FORBIDDEN_PATHS)), (
        f"a tool names a route that spends OpenRouter money: {sorted(paths & set(FORBIDDEN_PATHS))}"
    )


def check_the_chief_of_staff_cannot_arbitrate() -> None:
    """Routing without data access is a property of the wiring, not of a prompt."""
    from bridge.tools import ROLE_TOOLS

    assert ROLE_TOOLS["chief_of_staff"] == frozenset(), (
        "the chief of staff must have no backend tools; with data access it sees a choice "
        "point in code and becomes a model arbitrating a compliance question"
    )


def check_every_role_and_tool_line_up() -> None:
    from bridge.roster import ROSTER
    from bridge.tools import ROLE_TOOLS, TOOLS

    named = set().union(*ROLE_TOOLS.values()) if ROLE_TOOLS else set()
    assert named <= set(TOOLS), f"roles name tools that do not exist: {sorted(named - set(TOOLS))}"
    assert set(TOOLS) <= named, f"these tools belong to no role: {sorted(set(TOOLS) - named)}"
    assert set(ROSTER) == set(ROLE_TOOLS), (
        "every bot in the roster needs a tool set and vice versa: "
        f"{sorted(set(ROSTER) ^ set(ROLE_TOOLS))}"
    )


def check_a_role_cannot_call_another_roles_tool() -> None:
    """The role check is enforced at call time, not merely advertised in the schema."""
    from bridge.tools import call_tool

    refused = call_tool(None, "quant", "compliance_snapshot", {})
    assert refused["status"] == "REJECT"
    assert refused["reason"] == "tool_not_available_to_role"

    unknown = call_tool(None, "quant", "submit_order", {})
    assert unknown["status"] == "REJECT"
    assert unknown["reason"] == "unknown_tool"

    # An unknown role gets nothing, not everything.
    assert call_tool(None, "not_a_role", "quant_signal", {})["status"] == "REJECT"


def main() -> None:
    check_the_bridge_never_touches_a_model()
    check_nothing_outside_the_bridge_imports_it()
    check_no_tool_can_reach_the_broker()
    check_the_chief_of_staff_cannot_arbitrate()
    check_every_role_and_tool_line_up()
    check_a_role_cannot_call_another_roles_tool()
    print("PASS: no LLM originates a verdict, and no bot can reach the broker.")


if __name__ == "__main__":
    main()
