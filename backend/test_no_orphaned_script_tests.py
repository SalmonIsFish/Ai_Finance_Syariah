"""A script-mode test file runs main(), and nothing else -- so a test main() never calls
does not run at all.

run_all_tests.py runs any file with a `__main__` guard as a plain script (see its module
docstring). Pytest never sees it, so a `def test_...` that main() does not call is dead
code that reads like coverage. On 2026-10-07 two such tests were found in
test_option_strategy_api.py: they guarded the strategy endpoint's refusal of option
contracts, and disabling that refusal left the census fully green. Fixed in 1c89422.

The fix had a second trap. The two functions were defined *below* the `__main__` block,
so calling them from main() would raise NameError when the block ran -- the module has
not reached those definitions yet. This guard checks both.
"""

import ast
from pathlib import Path

import run_all_tests

BACKEND_DIR = Path(__file__).resolve().parent


def _is_main_guard(node: ast.stmt) -> bool:
    return isinstance(node, ast.If) and run_all_tests.has_main_guard(ast.unparse(node.test))


def script_test_problems(source: str) -> list[str]:
    """Return one line per problem: a test never referenced, or one defined after the guard."""
    if not run_all_tests.has_main_guard(source):
        return []
    tree = ast.parse(source)
    guard_line = next((node.lineno for node in tree.body if _is_main_guard(node)), None)
    tests = {
        node.name: node.lineno
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }
    referenced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)

    problems = []
    for name, line in sorted(tests.items(), key=lambda item: item[1]):
        if name not in referenced:
            problems.append(f"{name} (line {line}) is never called, so it never runs")
        elif guard_line is not None and line > guard_line:
            problems.append(
                f"{name} (line {line}) is defined after the __main__ block "
                f"(line {guard_line}); calling it from main() raises NameError"
            )
    return problems


def check_the_detector_sees_an_orphan() -> None:
    source = (
        "def test_a():\n    pass\n\n"
        "def test_b():\n    pass\n\n"
        "def main():\n    test_a()\n\n"
        'if __name__ == "__main__":\n    main()\n'
    )
    problems = script_test_problems(source)
    assert len(problems) == 1 and problems[0].startswith("test_b "), problems


def check_the_detector_sees_a_definition_after_the_guard() -> None:
    source = (
        "def main():\n    test_a()\n\n"
        'if __name__ == "__main__":\n    main()\n\n'
        "def test_a():\n    pass\n"
    )
    problems = script_test_problems(source)
    assert len(problems) == 1 and "NameError" in problems[0], problems


def check_the_detector_passes_a_clean_file_and_ignores_pytest_files() -> None:
    clean = (
        "def test_a():\n    pass\n\n"
        "def main():\n    test_a()\n\n"
        'if __name__ == "__main__":\n    main()\n'
    )
    assert script_test_problems(clean) == []
    # No __main__ guard: the runner hands this to pytest, which collects test_a itself.
    assert script_test_problems("def test_a():\n    pass\n") == []


def check_no_script_mode_test_file_has_a_test_that_never_runs() -> None:
    failures = []
    for path in sorted(BACKEND_DIR.glob("test_*.py")):
        for problem in script_test_problems(path.read_text(encoding="utf-8")):
            failures.append(f"{path.name}: {problem}")
    assert not failures, "\n" + "\n".join(failures)


def main() -> None:
    check_the_detector_sees_an_orphan()
    check_the_detector_sees_a_definition_after_the_guard()
    check_the_detector_passes_a_clean_file_and_ignores_pytest_files()
    check_no_script_mode_test_file_has_a_test_that_never_runs()
    print("PASS: every test_ function in a script-mode file is called before it is needed.")


if __name__ == "__main__":
    main()
