"""Run every backend test file and report a census.

    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py
    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py --verbose   # stream each result
    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py --filter sc_  # substring match

This exists because getting it right by hand is harder than it looks, and
getting it wrong produces *false failures* that waste a session. Three shapes of
test file live in backend/, and they need different invocations:

  1. Plain scripts with a `main()` and a `__main__` guard -- run directly.
  2. pytest-native files with `def test_` functions -- run under pytest.
  3. Files with NEITHER a `__main__` guard NOR `def test_` functions, which
     execute every assertion at module import. These must be run directly.
     Handing one to pytest imports it (so the assertions really do run, and
     pass) and then reports "no tests ran" with exit code 5 -- which looks
     exactly like a failure and is not one.

So dispatch on **whether a file has collectable `test_` functions**, never on
whether it has a `__main__` guard. A hand-rolled runner that got this backwards
reported two false failures on 2026-09-21.

Exit status is 0 only if every file passed, so this is usable as a gate.

This is a census, not a substitute for evidence: when a specific file's result
matters, run that file individually and show its output (see CLAUDE.md).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"

# Drives the moomoo SDK directly against a real OpenD gateway and hangs without
# one. Its purpose is manual verification of a live connection -- see CLAUDE.md.
EXCLUDED = {"test_moomoo.py"}

PER_FILE_TIMEOUT_SECONDS = 180


def has_collectable_tests(source: str) -> bool:
    return re.search(r"^def test_", source, re.MULTILINE) is not None


def has_main_guard(source: str) -> bool:
    return '__name__ == "__main__"' in source or "__name__ == '__main__'" in source


def build_command(path: Path, source: str) -> tuple[list[str], str]:
    """Returns (argv, mode). See the module docstring for why this is the rule."""
    if not has_collectable_tests(source) or has_main_guard(source):
        return [str(PYTHON), str(path)], "script"
    return [str(PYTHON), "-m", "pytest", str(path), "-q", "--no-header"], "pytest"


def run_one(path: Path) -> dict:
    source = path.read_text(encoding="utf-8", errors="replace")
    command, mode = build_command(path, source)
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=PER_FILE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"file": path.name, "mode": mode, "status": "TIMEOUT", "output": ""}

    output = (completed.stdout or "") + (completed.stderr or "")
    status = "PASS" if completed.returncode == 0 else "FAIL"
    # A pytest run that collected nothing is not a pass; it means this file was
    # dispatched wrongly, or its tests stopped being discoverable.
    if status == "PASS" and mode == "pytest" and "no tests ran" in output:
        status = "NO_TESTS_COLLECTED"
    return {"file": path.name, "mode": mode, "status": status, "output": output.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true", help="print each result as it runs")
    parser.add_argument("--filter", default="", help="only run files whose name contains this")
    args = parser.parse_args()

    if not PYTHON.exists():
        print(f"ERROR: interpreter not found at {PYTHON}")
        return 1

    paths = sorted(
        p for p in BACKEND_DIR.glob("test_*.py") if p.name not in EXCLUDED and args.filter in p.name
    )
    if not paths:
        print("No test files matched.")
        return 1

    results = []
    for path in paths:
        result = run_one(path)
        results.append(result)
        if args.verbose:
            print(f"{result['status']:<20} {result['mode']:<7} {result['file']}", flush=True)

    failures = [r for r in results if r["status"] != "PASS"]

    print()
    print("=" * 68)
    print(
        f"{len(results) - len(failures)}/{len(results)} passed"
        + ("" if failures else "  --  all green")
    )
    if EXCLUDED and not args.filter:
        print(f"excluded: {', '.join(sorted(EXCLUDED))} (see module docstring)")
    print("=" * 68)

    for failure in failures:
        print()
        print(f"--- {failure['status']}  {failure['file']}  ({failure['mode']}) ---")
        print("\n".join(failure["output"].splitlines()[-25:]) or "(no output)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
