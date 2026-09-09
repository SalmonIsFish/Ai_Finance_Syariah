"""Enforce NEXT_STEPS.md's "one screening record, two views -- never two screening paths".

If browsing, the CLI, and the order path each invoke a screen their own way, they
will eventually disagree about the same company with no way to tell which is right.
This test makes that structural rather than a convention someone has to remember.

It is a static import check on purpose. A runtime test would only catch the paths it
happened to exercise; this catches a new one the moment it is written.

Zoya (zoya_compliance.py, check_zoya.py) was a second, sandboxed screening path and
has been removed entirely -- there is nothing left for a "no second Zoya path" check
to guard against, so that check was removed along with it rather than kept as a
vacuous pass against files that no longer exist.
"""

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parent

# The single entry point every screening caller must reach the US screen through.
SCREEN_MODULE = "sec_edgar_screen"

# Modules permitted to import the screen directly. Everything else must go via
# agents.shariah_agent (the gate path) or shariah_explain (the explanation path).
SCREEN_ALLOWED = {
    "agents/shariah_agent.py",  # the gate path
    "shariah_explain.py",  # the explanation path
    "sec_edgar_screen.py",
    "check_us_strategy.py",  # CLI, goes through us_strategy -> shariah_agent
}


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


def _source_files() -> list[Path]:
    return [
        path
        for path in sorted(BACKEND.rglob("*.py"))
        if "__pycache__" not in path.parts and not path.name.startswith("test_")
    ]


def check_the_screen_has_one_entry_point() -> None:
    offenders = []
    for path in _source_files():
        relative = path.relative_to(BACKEND).as_posix()
        if relative in SCREEN_ALLOWED:
            continue
        if SCREEN_MODULE in _imported_modules(path):
            offenders.append(relative)

    assert not offenders, (
        f"these modules import {SCREEN_MODULE} directly instead of going through "
        f"agents.shariah_agent or shariah_explain: {offenders}"
    )


def check_the_explain_cli_and_the_endpoint_share_a_source() -> None:
    """The CLI and the HTTP view must be the same function, not two renderings."""
    import explain_compliance
    import local_api
    import shariah_explain

    assert explain_compliance.explain_symbol is shariah_explain.explain_symbol
    assert local_api.explain_symbol is shariah_explain.explain_symbol


def main() -> None:
    check_the_screen_has_one_entry_point()
    check_the_explain_cli_and_the_endpoint_share_a_source()
    print("PASS: one screening record, two views -- no second screening path.")


if __name__ == "__main__":
    main()
