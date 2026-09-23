"""MCP stdio server: the surface OpenClaw spawns and the model talks to.

**This process is built without the operator key.** `load_bridge_config` is called with
`with_operator=False`, so `X-Amanah-Operator` is not merely unused here, it is absent from
the address space. `/paper/execute` and `/paper/reconcile` return 401 at nginx for this
process no matter what a model asks for or how convincingly it asks. That is the whole
process-separation argument, and it is worth more than any instruction in a prompt.

It is also a thin adapter. Everything real lives in `tools.py`, which is transport-
agnostic: swapping stdio for HTTP later changes this file and no test.

    python backend/bridge/mcp_server.py --role shariah_narrator

Speaks JSON-RPC 2.0 over stdin/stdout, one message per line. Only `initialize`,
`tools/list` and `tools/call` are implemented -- this server exposes no resources and no
prompts, because a resource or a prompt would be another surface to reason about for no
gain.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BRIDGE_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from bridge.client import AmanahClient, load_bridge_config  # noqa: E402
from bridge.roster import ROSTER, instructions_for  # noqa: E402
from bridge.tools import ROLE_TOOLS, TOOLS, call_tool  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "amanah-bridge"
SERVER_VERSION = "0.1.0"


def load_env_file(path: Path, env: dict | None = None) -> None:
    """Read backend/bridge/.env the way config.py reads backend/.env.

    setdefault, so a real environment variable always beats the file -- the same rule and
    the same first-occurrence-wins gotcha as config.py:29.
    """
    target = os.environ if env is None else env
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        target.setdefault(key.strip(), value.strip())


def tool_schema(name: str) -> dict:
    """One tool in MCP's shape. Params are an enum of names, never a free-form path."""
    tool = TOOLS[name]
    required = [key for key, spec in tool.params.items() if spec.get("type") == "string"]
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": {
            "type": "object",
            "properties": dict(tool.params),
            "required": required,
            "additionalProperties": False,
        },
    }


def list_tools(role: str) -> list[dict]:
    return [tool_schema(name) for name in sorted(ROLE_TOOLS.get(role, frozenset()))]


def _text(payload: str) -> dict:
    return {"content": [{"type": "text", "text": payload}]}


def render_tool_result(result: dict) -> dict:
    """What the model actually receives.

    The rendered block comes first and is labelled authoritative, then the raw facts. The
    model can read both and rewrite neither: compose.py builds the outgoing message from
    `render`, not from anything the model returns.
    """
    if result.get("status") not in {"OK", "REJECT"} and not result.get("render"):
        return _text(f"unavailable: {result.get('reason') or result.get('status')}")

    parts = [result.get("render") or ""]
    facts = result.get("facts")
    if facts:
        parts.append("")
        parts.append("--- source data (authoritative; do not contradict) ---")
        parts.append(json.dumps(facts, indent=2, default=str, allow_nan=False))
    source = result.get("source") or {}
    if source:
        parts.append("")
        parts.append(f"route={source.get('route')} cached={source.get('cached')}")
    return _text("\n".join(parts))


def handle(message: dict, *, role: str, client) -> dict | None:
    """One JSON-RPC message in, one response out. None for a notification."""
    method = message.get("method")
    message_id = message.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": message_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": f"{SERVER_NAME}-{role}", "version": SERVER_VERSION},
                "instructions": instructions_for(role),
            },
        }

    if method in {"notifications/initialized", "initialized"}:
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": list_tools(role)}}

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name") or ""
        arguments = params.get("arguments") or {}
        try:
            result = call_tool(client, role, name, arguments)
        except Exception as exc:
            # Fail closed and say so. Never return an empty answer that reads like
            # "nothing found" when what happened is "this broke".
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": dict(_text(f"tool '{name}' failed: {type(exc).__name__}"), isError=True),
            }
        payload = render_tool_result(result)
        if result.get("status") == "REJECT":
            payload = dict(payload, isError=True)
        return {"jsonrpc": "2.0", "id": message_id, "result": payload}

    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(role: str, client, *, stdin=None, stdout=None) -> None:
    """Read JSON-RPC lines until EOF. stdin/stdout are injectable so tests can drive it."""
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else sys.stdout
    for line in source:
        stripped = line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped)
        except json.JSONDecodeError:
            sink.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "parse error"},
                    }
                )
                + "\n"
            )
            sink.flush()
            continue
        response = handle(message, role=role, client=client)
        if response is not None:
            sink.write(json.dumps(response, default=str) + "\n")
            sink.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amanah bridge MCP server (read-only).")
    parser.add_argument("--role", required=True, choices=sorted(ROSTER))
    args = parser.parse_args(argv)

    load_env_file(BRIDGE_DIR / ".env")
    try:
        # with_operator is NOT passed. This process cannot reach the broker, by
        # construction rather than by policy.
        config = load_bridge_config()
    except RuntimeError as exc:
        sys.stderr.write(f"bridge configuration error: {exc}\n")
        return 1

    serve(args.role, AmanahClient(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
