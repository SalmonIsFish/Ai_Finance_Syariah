"""The MCP server speaks the protocol, scopes tools by role, and holds no operator key.

Driven through `serve()` with injected stdin/stdout, so the real JSON-RPC loop runs --
parsing, dispatch, framing and all -- without spawning a process or touching a network.
The client is a recorder, so nothing reaches the deployment.

The load-bearing test here is the last one: the process OpenClaw spawns must be incapable
of reaching the broker, not merely instructed not to.
"""

import io
import json

import pytest
from bridge.client import AmanahClient, BridgeConfig, OperatorKeyMisuse
from bridge.mcp_server import handle, list_tools, serve
from bridge.ratelimit import ZoneLimiter
from bridge.roster import ROSTER
from bridge.tools import ROLE_TOOLS

_FAST = {"write": (6000.0, 500.0), "general": (6000.0, 500.0)}


class RecordingClient:
    """Stands in for AmanahClient, recording the route names a tool asked for."""

    def __init__(self, data=None):
        self.calls = []
        self._data = data if data is not None else {}

    def call(self, route_name, **kwargs):
        self.calls.append(route_name)
        return {"status": "OK", "route": route_name, "data": self._data, "cached": False}

    def read_compliance(self):
        return self.call("portfolio_compliance")


def drive(messages, *, role="shariah_narrator", client=None):
    """Run serve() over a list of messages and return the parsed responses."""
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in messages))
    stdout = io.StringIO()
    serve(role, client if client is not None else RecordingClient(), stdin=stdin, stdout=stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_initialize_returns_the_protocol_and_the_role_instructions():
    responses = drive([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}])
    result = responses[0]["result"]
    assert result["protocolVersion"]
    assert "tools" in result["capabilities"]
    assert "Shariah narrator" in result["instructions"]
    # The house rules must travel with every role, not be assumed.
    assert "never determine anything yourself" in result["instructions"].lower()


def test_a_notification_produces_no_response():
    responses = drive([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
    assert responses == []


def test_malformed_input_is_a_parse_error_not_a_crash():
    stdin = io.StringIO("{not json\n")
    stdout = io.StringIO()
    serve("quant", RecordingClient(), stdin=stdin, stdout=stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700


def test_an_unknown_method_is_refused():
    responses = drive([{"jsonrpc": "2.0", "id": 9, "method": "resources/list"}])
    assert responses[0]["error"]["code"] == -32601


@pytest.mark.parametrize("role", sorted(ROSTER))
def test_every_role_lists_exactly_its_own_tools(role):
    listed = {tool["name"] for tool in list_tools(role)}
    assert listed == set(ROLE_TOOLS[role]), role
    for tool in list_tools(role):
        schema = tool["inputSchema"]
        assert schema["additionalProperties"] is False, (
            "a free-form property is a field a model can fill with something invented"
        )
        assert "path" not in schema["properties"]
        assert "url" not in schema["properties"]


def test_the_chief_of_staff_is_offered_no_tools_at_all():
    assert list_tools("chief_of_staff") == []


def test_a_tool_call_returns_the_rendered_block_first():
    client = RecordingClient({"ticker": "4197", "verdict": {"status": "PASS"}})
    responses = drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "shariah_status", "arguments": {"ticker": "4197"}},
            }
        ],
        client=client,
    )
    text = responses[0]["result"]["content"][0]["text"]
    assert text.startswith("[Shariah] 4197")
    assert "authoritative" in text
    assert client.calls == ["shariah_status"]


def test_a_tool_outside_the_role_is_refused_and_never_reaches_the_client():
    client = RecordingClient()
    responses = drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "compliance_snapshot", "arguments": {}},
            }
        ],
        role="quant",
        client=client,
    )
    assert responses[0]["result"]["isError"] is True
    assert client.calls == [], "a refused tool must not reach the network at all"


def test_an_invented_tool_name_is_refused():
    client = RecordingClient()
    responses = drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "submit_order", "arguments": {"symbol": "AAPL"}},
            }
        ],
        role="quant",
        client=client,
    )
    assert responses[0]["result"]["isError"] is True
    assert client.calls == []


def test_a_failing_tool_reports_unavailable_rather_than_an_empty_answer():
    class Failing(RecordingClient):
        def call(self, route_name, **kwargs):
            self.calls.append(route_name)
            return {"status": "UNAVAILABLE", "route": route_name, "data": {}, "reason": "http_503"}

    responses = drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "shariah_status", "arguments": {"ticker": "4197"}},
            }
        ],
        client=Failing(),
    )
    text = responses[0]["result"]["content"][0]["text"]
    assert "unavailable" in text
    assert "No verdict can be reported" in text


def test_a_raising_handler_is_reported_not_swallowed(monkeypatch):
    import bridge.mcp_server as server

    def boom(client, role, name, args):
        raise ValueError("kaboom")

    monkeypatch.setattr(server, "call_tool", boom)
    response = handle(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "quant_signal"}},
        role="quant",
        client=RecordingClient(),
    )
    assert response["result"]["isError"] is True
    assert "ValueError" in response["result"]["content"][0]["text"]


def test_non_finite_numbers_survive_strict_json_encoding():
    """MCP re-serialises; a raw inf would raise and lose the whole response."""
    import math

    from bridge.sanitize import sanitize_json

    client = RecordingClient(sanitize_json({"daily_loss_pct": math.inf, "orders_today": 2}))
    responses = drive(
        [
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "risk_snapshot", "arguments": {}},
            }
        ],
        role="risk_officer",
        client=client,
    )
    text = responses[0]["result"]["content"][0]["text"]
    assert "UNBOUNDED" in text
    assert "BLOCKING" in text


def test_the_mcp_process_cannot_reach_the_broker():
    """The whole process-separation argument, asserted rather than described.

    mcp_server builds its config without the operator key, so /paper/execute is not
    merely absent from the tool list -- it is unreachable from this address space.
    """
    config = BridgeConfig(
        base_url="https://example.invalid",
        basic_user="project_owner",
        basic_password="pw",
        operator_key=None,
    )
    client = AmanahClient(config, limiter=ZoneLimiter(_FAST))
    with pytest.raises(OperatorKeyMisuse):
        client.call("paper_execute", path_params={"queue_id": 1}, body={"x": 1})


def test_main_does_not_request_the_operator_key():
    """Read the source: with_operator must never be passed True in this module."""
    import ast
    import pathlib

    source = (pathlib.Path(__file__).resolve().parent / "bridge" / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                assert keyword.arg != "with_operator", (
                    "mcp_server must never ask for the operator key; that is what keeps "
                    "the LLM-facing process incapable of reaching the broker"
                )
