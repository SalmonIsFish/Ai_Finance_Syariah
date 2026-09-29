"""Tests never reach the network. run_all_tests.py enforces it with this module.

The runner puts this directory on PYTHONPATH for every test process, so Python loads
this file at startup. From then on, in that process:

* a socket connect to anything but a loopback port *this process bound itself* is
  refused -- which blocks paper-api, market data, SEC and Yahoo, and also OpenD on
  127.0.0.1:11111 and any other real service on this machine. A port the process bound
  cannot be someone else's service: asyncio's socketpair connects to its own listener,
  and test_moomoo_status binds a port and closes it to get one that is surely closed;
* a curl_cffi request is refused. yfinance fetches through libcurl, which never touches
  Python's `socket`, so a socket-only guard would have missed every Yahoo call;
* launching `uvx` is refused -- that is how the alpaca_mcp transport starts the Alpaca
  MCP server, a child process whose sockets this module cannot see.

Refusing is not enough on its own. Most code under test catches a connection error and
degrades, which is exactly how eight test files talked to real services on every run and
stayed green (audit, 2026-09-28): one POSTed an order with limit_price "nan" to Alpaca
paper and passed *because Alpaca refused it*. So every refusal is also appended to
$AMANAH_NETGUARD_LOG, and the runner fails any file that produced one -- whether or not
the code swallowed the error.

Blocking, not just recording, also means a test run can never place a paper order.

Stdlib only, and nothing heavy at startup: this loads into every test process.
"""

import importlib.abc
import json
import os
import socket
import subprocess
import sys
import traceback

_LOG = os.environ.get("AMANAH_NETGUARD_LOG")
_own_ports = set()


def _where():
    """The innermost backend frame and the test frame, for a refusal someone can act on."""
    backend = test = None
    for frame in traceback.extract_stack()[:-3]:
        path = frame.filename.replace("/", "\\")
        if "\\backend\\" not in path or "\\site-packages\\" in path:
            continue
        short = f"{path.split(chr(92) + 'backend' + chr(92), 1)[1]}:{frame.lineno}"
        if os.path.basename(path).startswith("test_"):
            test = short
        elif "netguard" not in path:
            backend = short
    return backend, test


def _refuse(error, **what):
    backend, test = _where()
    if _LOG:
        record = {"backend_frame": backend, "test_frame": test, **what}
        try:
            with open(_LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError:
            pass
    raise error(f"netguard: tests may not reach the network ({what})")


def _is_loopback(host):
    host = str(host)
    return host in {"localhost", "::1"} or host.startswith("127.")


def _check(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return
    host, port = address[0], address[1]
    if _is_loopback(host) and port in _own_ports:
        return
    _refuse(ConnectionRefusedError, via="socket", host=host, port=port)


_real_bind = socket.socket.bind
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _bind(self, address):
    result = _real_bind(self, address)
    if self.family in (socket.AF_INET, socket.AF_INET6):
        try:
            _own_ports.add(self.getsockname()[1])
        except OSError:
            pass
    return result


def _connect(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _check(address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    if self.family in (socket.AF_INET, socket.AF_INET6):
        _check(address)
    return _real_connect_ex(self, address)


socket.socket.bind = _bind
socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex


_real_popen_init = subprocess.Popen.__init__


def _popen_init(self, args, *rest, **kwargs):
    program = args if isinstance(args, str) else (args[0] if args else "")
    if os.path.basename(str(program)).lower().split(".")[0] == "uvx":
        _refuse(FileNotFoundError, via="subprocess", command=str(args)[:200])
    return _real_popen_init(self, args, *rest, **kwargs)


subprocess.Popen.__init__ = _popen_init


def _guard_curl(module):
    def refuse(self, method, url, *args, **kwargs):
        _refuse(
            module.exceptions.ConnectionError, via="curl_cffi", method=method, url=str(url)[:200]
        )

    module.Session.request = refuse
    module.AsyncSession.request = refuse


class _PatchCurlOnImport(importlib.abc.MetaPathFinder):
    """Patch curl_cffi.requests the moment something imports it, not before: importing it
    eagerly would add its load time to every one of the ~100 test processes."""

    def find_spec(self, name, path, target=None):
        if name != "curl_cffi.requests":
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = getattr(finder, "find_spec", lambda *a: None)(name, path, target)
            if spec is not None and spec.loader is not None:
                exec_module = spec.loader.exec_module

                def patched(module, _exec=exec_module):
                    _exec(module)
                    _guard_curl(module)

                spec.loader.exec_module = patched
                return spec
        return None


if "curl_cffi.requests" in sys.modules:
    _guard_curl(sys.modules["curl_cffi.requests"])
else:
    sys.meta_path.insert(0, _PatchCurlOnImport())
