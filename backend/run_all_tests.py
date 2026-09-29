"""Run every backend test file and report a census.

    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py
    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py --verbose   # stream each result
    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py --filter sc_  # substring match
    .\\.venv\\Scripts\\python.exe backend\\run_all_tests.py --live      # network guard OFF

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

Tests never reach the network, and this runner enforces it: every test process
starts with backend/netguard/sitecustomize.py loaded, which refuses sockets, curl
and `uvx`, and records each refusal. A file that produced one FAILS with status
NETWORK -- even if the code under test swallowed the error and the file passed,
because that is precisely how eight files reached real services on every run and
stayed green (audit, 2026-09-28). One POSTed an order with limit_price "nan" to
Alpaca paper and passed because Alpaca refused it. `--live` turns the guard off
for deliberate live smoke tests.

KNOWN_NETWORK_USERS is an allowlist for files that reach the network. It is
EMPTY, and should stay that way. It exists so the guard could be switched on
without a red census: it began with the seven files the audit found, and emptied
as each one's seams were swapped (backend/offline_seams.py). An entry is reported
rather than failed, and one whose file no longer reaches the network fails as
STALE_ALLOWLIST -- so the list can only shrink, never quietly keep an exemption.

Exit status is 0 only if every file passed, so this is usable as a gate.

This is a census, not a substitute for evidence: when a specific file's result
matters, run that file individually and show its output (see CLAUDE.md).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent
PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
NETGUARD_DIR = BACKEND_DIR / "netguard"

# Drives the moomoo SDK directly against a real OpenD gateway and hangs without
# one. Its purpose is manual verification of a live connection -- see CLAUDE.md.
EXCLUDED = {"test_moomoo.py"}

# {filename: reason}. Empty since 2026-09-29. Adding an entry is a deliberate,
# reviewable exemption from "tests never reach the network" -- swap the seam
# instead (backend/offline_seams.py covers the common outage cases).
KNOWN_NETWORK_USERS: dict[str, str] = {}

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


def guarded_env(base: dict, log_path: Path) -> dict:
    """`base` with the network guard loaded into every Python process it starts."""
    env = dict(base)
    existing = base.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join([str(NETGUARD_DIR)] + ([existing] if existing else []))
    env["AMANAH_NETGUARD_LOG"] = str(log_path)
    return env


def read_refusals(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    refusals = []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            refusals.append(json.loads(line))
        except json.JSONDecodeError:
            refusals.append({"via": "unparseable", "raw": line[:200]})
    return refusals


def describe_refusals(refusals: list[dict], limit: int = 8) -> str:
    seen = {}
    for r in refusals:
        target = r.get("url") or r.get("command") or f"{r.get('host')}:{r.get('port')}"
        key = f"{r.get('via')} {target}  at {r.get('backend_frame')} <- {r.get('test_frame')}"
        seen[key] = seen.get(key, 0) + 1
    lines = [f"  {n:3d} x {key}" for key, n in list(seen.items())[:limit]]
    if len(seen) > limit:
        lines.append(f"  ... and {len(seen) - limit} more distinct")
    return "\n".join(lines)


def apply_network_rule(result: dict, refusals: list[dict], known: dict) -> dict:
    """Decide a file's status from what it tried to reach, not only its exit code."""
    name = result["file"]
    result["refusals"] = refusals
    if refusals and name not in known:
        if result["status"] == "PASS":
            result["status"] = "NETWORK"
        result["output"] = (
            f"{result['output']}\n\nnetguard refused {len(refusals)} network attempt(s) -- "
            f"swap the seam; tests may not reach real services:\n{describe_refusals(refusals)}"
        ).strip()
    elif not refusals and name in known and result["status"] == "PASS":
        result["status"] = "STALE_ALLOWLIST"
        result["output"] = (
            f"{name} no longer reaches the network. Remove it from KNOWN_NETWORK_USERS "
            "in run_all_tests.py -- the allowlist only shrinks."
        )
    return result


def run_one(path: Path, env: dict) -> dict:
    source = path.read_text(encoding="utf-8", errors="replace")
    command, mode = build_command(path, source)
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=PER_FILE_TIMEOUT_SECONDS,
            env=env,
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
    parser.add_argument(
        "--live", action="store_true", help="network guard OFF; enables deliberate live tests"
    )
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

    # Point the evidence trail at a throwaway directory for the whole run. The
    # trail's only value is that every line in it actually happened, so a test
    # run must never append to it. Set here rather than in each test file
    # because all 84 of them go through agent_coordinator sooner or later.
    evidence_sink = tempfile.mkdtemp(prefix="amanah-test-evidence-")
    base_env = {**os.environ, "EVIDENCE_DIR": evidence_sink}
    if args.live:
        base_env["AMANAH_LIVE_TESTS"] = "1"
        print("LIVE: network guard OFF -- tests may reach real services.")

    results = []
    for path in paths:
        if args.live:
            result = run_one(path, base_env)
        else:
            log_path = Path(evidence_sink) / f"netguard-{path.stem}.jsonl"
            result = run_one(path, guarded_env(base_env, log_path))
            result = apply_network_rule(result, read_refusals(log_path), KNOWN_NETWORK_USERS)
        results.append(result)
        if args.verbose:
            print(f"{result['status']:<20} {result['mode']:<7} {result['file']}", flush=True)

    shutil.rmtree(evidence_sink, ignore_errors=True)

    failures = [r for r in results if r["status"] != "PASS"]
    known_users = sorted(
        r["file"] for r in results if r.get("refusals") and r["file"] in KNOWN_NETWORK_USERS
    )

    print()
    print("=" * 68)
    print(
        f"{len(results) - len(failures)}/{len(results)} passed"
        + ("" if failures else "  --  all green")
    )
    if EXCLUDED and not args.filter:
        print(f"excluded: {', '.join(sorted(EXCLUDED))} (see module docstring)")
    if not args.live and known_users:
        print(
            f"network guard: {len(known_users)} allowlisted file(s) still reach the network "
            f"(refused, not failed): {', '.join(known_users)}"
        )
    print("=" * 68)

    for failure in failures:
        print()
        print(f"--- {failure['status']}  {failure['file']}  ({failure['mode']}) ---")
        print("\n".join(failure["output"].splitlines()[-25:]) or "(no output)")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
