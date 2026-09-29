"""The network guard is real, and the runner acts on it.

run_all_tests.py loads backend/netguard/sitecustomize.py into every test process and
fails any file that tried to reach the network. A guard that silently stopped loading
would turn every result back into the pre-2026-09-28 state -- tests talking to Alpaca
paper and OpenD and passing anyway -- with nothing visibly wrong. So this file proves
the guard in a child process, the same way the runner starts one.

The proof is the refusal log, not the exception type: without the guard, a DNS failure
on a fake host also raises a curl ConnectionError, and an assertion on the type alone
would pass with the guard gone.
"""

import json
import os
import subprocess
import sys

import run_all_tests

PROBE = r"""
import json, socket, subprocess
out = {}

def attempt(name, fn):
    try:
        fn()
        out[name] = "reached"
    except Exception as exc:
        out[name] = [type(exc).__name__, "netguard" in str(exc)]

# 192.0.2.1 is TEST-NET-1, reserved and never routed, so nothing real is contacted even
# if the guard is missing.
attempt("public", lambda: socket.create_connection(("192.0.2.1", 443), timeout=1))
attempt("opend", lambda: socket.create_connection(("127.0.0.1", 11111), timeout=1))

def curl():
    import curl_cffi.requests as requests
    requests.Session().get("https://example.invalid/", timeout=1)

attempt("curl", curl)
attempt("uvx", lambda: subprocess.run(["uvx", "--version"], capture_output=True, timeout=5))

def own_closed_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    socket.create_connection(("127.0.0.1", port), timeout=1)

attempt("own_bound_port", own_closed_port)

a, b = socket.socketpair()
a.sendall(b"x")
out["socketpair"] = b.recv(1).decode()
print(json.dumps(out))
"""


def _run_probe(tmp_path):
    log = tmp_path / "refusals.jsonl"
    env = run_all_tests.guarded_env(dict(os.environ), log)
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1]), run_all_tests.read_refusals(log)


def test_the_guard_refuses_and_records_every_route(tmp_path):
    out, refusals = _run_probe(tmp_path)

    assert out["public"] == ["ConnectionRefusedError", True], out
    assert out["opend"] == ["ConnectionRefusedError", True], out
    assert out["curl"] == ["ConnectionError", True], out
    assert out["uvx"] == ["FileNotFoundError", True], out

    routes = [(r["via"], r.get("port")) for r in refusals]
    assert routes == [
        ("socket", 443),
        ("socket", 11111),
        ("curl_cffi", None),
        ("subprocess", None),
    ], refusals


def test_the_guard_still_allows_a_process_its_own_loopback(tmp_path):
    """asyncio's socketpair connects to a listener in the same process. Blocking that
    would break every FastAPI TestClient in the suite."""
    out, _ = _run_probe(tmp_path)
    assert out["socketpair"] == "x", out
    # test_moomoo_status binds a port and closes it to get one that is surely closed.
    # The guard must let that connect through -- the refusal has to come from the OS
    # (no "netguard" in it), because a closed port is exactly what that test measures.
    # Windows retries a SYN to a closed loopback port for ~2s before refusing, so within
    # the probe's 1s timeout the OS answer may arrive as a timeout instead.
    error, from_guard = out["own_bound_port"]
    assert error in {"ConnectionRefusedError", "TimeoutError"}, out
    assert from_guard is False, out


def test_guarded_env_puts_the_guard_first_and_keeps_the_existing_path(tmp_path):
    env = run_all_tests.guarded_env({"PYTHONPATH": "elsewhere"}, tmp_path / "log")
    first, rest = env["PYTHONPATH"].split(os.pathsep, 1)
    assert first == str(run_all_tests.NETGUARD_DIR)
    assert rest == "elsewhere"
    assert (run_all_tests.NETGUARD_DIR / "sitecustomize.py").exists()


# --- how the runner turns refusals into a status ------------------------------------

REFUSAL = [{"via": "socket", "host": "35.194.67.18", "port": 443}]


def _result(status="PASS", file="test_new.py"):
    return {"file": file, "mode": "pytest", "status": status, "output": ""}


def test_a_file_that_reached_the_network_fails_even_though_it_passed():
    result = run_all_tests.apply_network_rule(_result(), REFUSAL, known={})
    assert result["status"] == "NETWORK"
    assert "35.194.67.18:443" in result["output"]


def test_an_allowlisted_file_is_reported_not_failed():
    known = {"test_new.py": "reason"}
    assert run_all_tests.apply_network_rule(_result(), REFUSAL, known)["status"] == "PASS"


def test_an_allowlist_entry_that_no_longer_reaches_the_network_fails_as_stale():
    known = {"test_new.py": "reason"}
    result = run_all_tests.apply_network_rule(_result(), [], known)
    assert result["status"] == "STALE_ALLOWLIST"


def test_a_real_failure_keeps_its_status_and_gains_the_refusals():
    result = run_all_tests.apply_network_rule(_result(status="FAIL"), REFUSAL, known={})
    assert result["status"] == "FAIL"
    assert "netguard refused" in result["output"]


def test_every_allowlisted_name_is_a_real_test_file():
    """A misspelt entry would never run, so it could never be flagged stale -- it would
    just sit there allowlisting nothing, forever."""
    for name in run_all_tests.KNOWN_NETWORK_USERS:
        assert (run_all_tests.BACKEND_DIR / name).exists(), name
