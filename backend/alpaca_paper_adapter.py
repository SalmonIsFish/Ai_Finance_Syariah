"""Paper execution adapter boundary for Alpaca submissions.

Drop-in replacement for ``moomoo_paper_adapter``: same public call shape
(``submit_paper_order(approval, broker) -> dict`` and
``reconcile_paper_order(approval) -> dict``) and the same broker-response keys the
approval queue, execution audit, and ``portfolio_store`` already consume.

Safety notes:

- The base URL is the hardcoded Alpaca **paper** host. There is no live URL in this
  module and no env var that can point it at one, so live submission is impossible.
- ``ALPACA_MODE`` is additionally pinned to ``paper`` in ``config.load_settings``.
- The MCP transport always forces ``ALPACA_PAPER_TRADE=true`` for the spawned server.
- This adapter performs **no** Shariah, risk, or approval checks. It submits whatever
  an already-gated approval row says to submit, exactly like the Moomoo adapter does.
"""

import json
import os
import shlex
import shutil
import subprocess
from datetime import date, datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import load_settings


ALPACA_PAPER_BASE_URL = "https://paper-api.alpaca.markets"
ALPACA_ADAPTERS = {"alpaca", "alpaca_mcp"}
SUPPORTED_REAL_MARKETS = {"US"}

# Alpaca options level 1 only: sell covered call, sell cash-secured put (and closing
# the resulting short). Spreads and naked structures stay out of scope on purpose.
SUPPORTED_OPTION_STRATEGIES = {"COVERED_CALL": "CALL", "CASH_SECURED_PUT": "PUT"}
DEFAULT_MCP_COMMAND = "uvx alpaca-mcp-server"
REQUEST_TIMEOUT_SECONDS = 20


class AlpacaMcpUnavailable(RuntimeError):
    """Raised when the Alpaca MCP server cannot be started or spoken to."""


def submit_paper_order(approval: dict, alpaca: dict, adapter: str | None = None) -> dict:
    """``adapter`` is the per-market choice from broker_routing; None keeps the global."""
    settings = load_settings()
    adapter = adapter or settings.paper_execution_adapter
    if adapter == "fake":
        return fake_submit_paper_order(approval=approval, alpaca=alpaca)
    if adapter == "alpaca":
        return submit_alpaca_paper_order(approval=approval)
    if adapter == "alpaca_mcp":
        return submit_alpaca_mcp_paper_order(approval=approval)
    return {
        "status": "ADAPTER_NOT_CONFIGURED",
        "adapter": adapter,
        "broker_submission": False,
        "reason": "PAPER_EXECUTION_ADAPTER must be fake, alpaca, or alpaca_mcp to submit paper orders",
    }


def reconcile_paper_order(approval: dict) -> dict:
    settings = load_settings()
    broker_submission = broker_submission_from_approval(approval)
    if not broker_submission:
        return {
            "status": "BROKER_NOT_SUBMITTED",
            "adapter": settings.paper_execution_adapter,
            "broker_submission": False,
            "reason": "approval row has no broker submission",
        }

    adapter = broker_submission.get("adapter") or settings.paper_execution_adapter
    if adapter == "fake":
        return fake_reconcile_paper_order(approval=approval, broker_submission=broker_submission)
    if adapter == "alpaca":
        return reconcile_alpaca_paper_order(approval=approval, broker_submission=broker_submission)
    if adapter == "alpaca_mcp":
        return reconcile_alpaca_mcp_paper_order(
            approval=approval, broker_submission=broker_submission
        )
    return {
        "status": "ADAPTER_NOT_CONFIGURED",
        "adapter": adapter,
        "broker_submission": bool(approval.get("broker_submission")),
        "reason": "submitted order adapter is not supported for reconciliation",
    }


# ---------------------------------------------------------------------------
# Credentials and transport
# ---------------------------------------------------------------------------


def alpaca_credentials() -> dict | None:
    """Return paper credentials, or None when the user has not set them yet."""
    settings = load_settings()
    if not settings.alpaca_api_key_id or not settings.alpaca_secret_key:
        return None
    return {"key_id": settings.alpaca_api_key_id, "secret_key": settings.alpaca_secret_key}


def alpaca_request(method: str, path: str, *, credentials: dict, body: dict | None = None) -> dict:
    """Single seam for every Alpaca REST call, so tests never touch the network."""
    url = f"{ALPACA_PAPER_BASE_URL}{path}"
    headers = {
        "APCA-API-KEY-ID": credentials["key_id"],
        "APCA-API-SECRET-KEY": credentials["secret_key"],
        "Accept": "application/json",
    }
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(url, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return {
                "ok": True,
                "status_code": response.status,
                "data": decode_json(response.read()),
            }
    except HTTPError as exc:
        return {
            "ok": False,
            "status_code": exc.code,
            "data": decode_json(exc.read()),
            "reason": f"http_{exc.code}",
        }
    except URLError as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}


def decode_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# REST adapter
# ---------------------------------------------------------------------------


def submit_alpaca_paper_order(*, approval: dict) -> dict:
    plan = build_order_plan(approval, adapter="alpaca")
    if plan["status"] != "PASS":
        return plan["error"]

    credentials = alpaca_credentials()
    if credentials is None:
        return credentials_missing("alpaca", submitted=False)

    account_gate = fetch_paper_account(credentials=credentials, adapter="alpaca", submitted=False)
    if account_gate["status"] != "PASS":
        return account_gate["error"]
    account = account_gate["account"]

    if plan["asset_class"] == "option" and account["options_trading_level"] < 1:
        return {
            "status": "OPTIONS_NOT_ENABLED",
            "adapter": "alpaca",
            "broker_submission": False,
            "reason": "alpaca_options_trading_level_below_1",
            "environment": "PAPER",
            "account_type": account["account_type"],
            "account_suffix": account["account_suffix"],
        }

    response = alpaca_request("POST", "/v2/orders", credentials=credentials, body=plan["body"])
    if not response.get("ok"):
        return {
            "status": "BROKER_REJECTED",
            "adapter": "alpaca",
            "broker_submission": False,
            "reason": error_message(response),
            "environment": "PAPER",
            "account_type": account["account_type"],
            "account_suffix": account["account_suffix"],
        }

    return submission_result(
        approval=approval,
        plan=plan,
        account=account,
        order=response.get("data") or {},
        adapter="alpaca",
    )


def reconcile_alpaca_paper_order(*, approval: dict, broker_submission: dict) -> dict:
    guard = reconcile_preconditions(
        approval=approval, broker_submission=broker_submission, adapter="alpaca"
    )
    if guard["status"] != "PASS":
        return guard["error"]

    credentials = alpaca_credentials()
    if credentials is None:
        return credentials_missing("alpaca", submitted=True)

    account_gate = fetch_paper_account(credentials=credentials, adapter="alpaca", submitted=True)
    if account_gate["status"] != "PASS":
        return account_gate["error"]
    account = account_gate["account"]

    broker_order_id = guard["broker_order_id"]
    response = alpaca_request("GET", f"/v2/orders/{broker_order_id}", credentials=credentials)
    if not response.get("ok"):
        if response.get("status_code") == 404:
            return order_not_found(
                adapter="alpaca",
                broker_order_id=broker_order_id,
                code=guard["code"],
                account=account,
            )
        return {
            "status": "BROKER_RECONCILE_ERROR",
            "adapter": "alpaca",
            "broker_submission": True,
            "reason": error_message(response),
        }

    return reconciliation_result(
        approval=approval,
        order=response.get("data") or {},
        account=account,
        broker_order_id=broker_order_id,
        code=guard["code"],
        adapter="alpaca",
    )


# ---------------------------------------------------------------------------
# MCP adapter (hackathon-mandated MCP transport)
# ---------------------------------------------------------------------------


def load_alpaca_mcp_client():
    """Start the Alpaca MCP server over stdio, pinned to paper trading."""
    credentials = alpaca_credentials()
    if credentials is None:
        raise AlpacaMcpUnavailable("credentials_missing")

    command = split_mcp_command(os.getenv("ALPACA_MCP_COMMAND", DEFAULT_MCP_COMMAND))
    if not command:
        raise AlpacaMcpUnavailable("mcp_command_empty")
    if shutil.which(command[0]) is None:
        raise AlpacaMcpUnavailable(f"{command[0]}_not_installed")

    env = dict(os.environ)
    env["ALPACA_API_KEY"] = credentials["key_id"]
    env["ALPACA_SECRET_KEY"] = credentials["secret_key"]
    env["ALPACA_PAPER_TRADE"] = "true"

    client = AlpacaMcpStdioClient(command=command, env=env)
    client.start()
    return client


def split_mcp_command(value: str) -> list[str]:
    r"""Split the MCP command line without mangling Windows paths.

    ``shlex.split`` in POSIX mode treats backslashes as escapes, which destroys
    ``C:\path\to\uvx.exe``. On Windows we split in non-POSIX mode and strip the
    quotes shlex leaves attached to each token.
    """
    if os.name == "nt":
        return [token.strip('"') for token in shlex.split(value, posix=False) if token.strip('"')]
    return shlex.split(value)


class AlpacaMcpStdioClient:
    """Minimal newline-delimited JSON-RPC client for an MCP stdio server."""

    def __init__(self, *, command: list[str], env: dict, timeout: int = 60):
        self.command = command
        self.env = env
        self.timeout = timeout
        self.process = None
        self._next_id = 0

    def start(self) -> None:
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self.env,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise AlpacaMcpUnavailable(f"{self.command[0]}_not_installed") from exc
        except Exception as exc:
            raise AlpacaMcpUnavailable(type(exc).__name__) from exc

        initialize = self._request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "amanah-trader", "version": "1.0"},
            },
        )
        if "error" in initialize:
            raise AlpacaMcpUnavailable("mcp_initialize_failed")
        self._notify("notifications/initialized", {})

    def list_tools(self) -> dict:
        response = self._request("tools/list", {})
        if "error" in response:
            return {"ok": False, "reason": "mcp_tools_list_failed", "data": response["error"]}
        return {"ok": True, "data": (response.get("result") or {}).get("tools", [])}

    def call_tool(self, name: str, arguments: dict) -> dict:
        response = self._request("tools/call", {"name": name, "arguments": arguments})
        if "error" in response:
            return {
                "ok": False,
                "reason": mcp_error_reason(response["error"]),
                "data": response["error"],
            }
        result = response.get("result") or {}
        if result.get("isError"):
            return {"ok": False, "reason": mcp_text(result) or "mcp_tool_error", "data": result}
        return {"ok": True, "data": mcp_payload(result)}

    def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            try:
                self.process.kill()
            except Exception:
                pass
        finally:
            self.process = None

    def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        message = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params}
        self._write(message)
        while True:
            payload = self._read()
            if payload is None:
                raise AlpacaMcpUnavailable("mcp_server_closed_stream")
            if payload.get("id") == self._next_id:
                return payload

    def _notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, message: dict) -> None:
        if self.process is None or self.process.stdin is None:
            raise AlpacaMcpUnavailable("mcp_process_not_running")
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except Exception as exc:
            raise AlpacaMcpUnavailable(type(exc).__name__) from exc

    def _read(self) -> dict | None:
        if self.process is None or self.process.stdout is None:
            raise AlpacaMcpUnavailable("mcp_process_not_running")
        while True:
            line = self.process.stdout.readline()
            if line == "":
                return None
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue


def mcp_text(result: dict) -> str | None:
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text")
    return None


def mcp_payload(result: dict):
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return unwrap_mcp_envelope(structured)
    text = mcp_text(result)
    if isinstance(text, str):
        try:
            return unwrap_mcp_envelope(json.loads(text))
        except json.JSONDecodeError:
            return {"text": text}
    return {}


def unwrap_mcp_envelope(payload):
    """Strip the wrappers the Alpaca MCP server puts around real API data.

    Responses arrive as ``{"_alpaca_mcp_security": {...}, "data": {...}}``. The
    security block is a prompt-injection guard aimed at LLM callers; this adapter
    only ever reads named fields out of ``data``, so it is dropped here rather than
    interpreted. FastMCP additionally wraps scalar/array returns in ``{"result": ...}``.
    """
    if not isinstance(payload, dict):
        return payload
    if "_alpaca_mcp_security" in payload and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict):
        inner = payload.get("result")
        if isinstance(inner, (dict, list)) and "result" in payload and len(payload) == 1:
            return inner
    return payload


def mcp_error_reason(error) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "mcp_error")
    return "mcp_error"


def submit_alpaca_mcp_paper_order(*, approval: dict) -> dict:
    plan = build_order_plan(approval, adapter="alpaca_mcp")
    if plan["status"] != "PASS":
        return plan["error"]

    if alpaca_credentials() is None:
        return credentials_missing("alpaca_mcp", submitted=False)

    try:
        client = load_alpaca_mcp_client()
    except AlpacaMcpUnavailable as exc:
        return {
            "status": "MCP_CLIENT_UNAVAILABLE",
            "adapter": "alpaca_mcp",
            "broker_submission": False,
            "reason": str(exc),
        }

    try:
        account_response = client.call_tool("get_account_info", {})
        account = (
            paper_account_from_payload(account_response.get("data"))
            if account_response.get("ok")
            else None
        )
        if account is None:
            return {
                "status": "ALPACA_ACCOUNT_QUERY_FAILED",
                "adapter": "alpaca_mcp",
                "broker_submission": False,
                "reason": account_response.get("reason") or "mcp_account_payload_unparsed",
            }
        if account["blocked"]:
            return {
                "status": "ALPACA_PAPER_ACCOUNT_BLOCKED",
                "adapter": "alpaca_mcp",
                "broker_submission": False,
                "reason": "alpaca_paper_account_blocked",
            }
        if account["account_status"] != "ACTIVE":
            return {
                "status": "ALPACA_PAPER_ACCOUNT_MISSING",
                "adapter": "alpaca_mcp",
                "broker_submission": False,
                "reason": "active_paper_account_not_found",
            }
        if plan["asset_class"] == "option" and account["options_trading_level"] < 1:
            return {
                "status": "OPTIONS_NOT_ENABLED",
                "adapter": "alpaca_mcp",
                "broker_submission": False,
                "reason": "alpaca_options_trading_level_below_1",
                "environment": "PAPER",
                "account_type": account["account_type"],
                "account_suffix": account["account_suffix"],
            }

        tool = "place_option_order" if plan["asset_class"] == "option" else "place_stock_order"
        order_response = client.call_tool(tool, plan["body"])
        if not order_response.get("ok"):
            return {
                "status": "BROKER_REJECTED",
                "adapter": "alpaca_mcp",
                "broker_submission": False,
                "reason": order_response.get("reason") or "mcp_order_rejected",
                "environment": "PAPER",
                "account_type": account["account_type"],
                "account_suffix": account["account_suffix"],
            }

        order = order_response.get("data")
        if not isinstance(order, dict) or not extract_order_id(order):
            return {
                "status": "BROKER_RESPONSE_UNPARSED",
                "adapter": "alpaca_mcp",
                "broker_submission": False,
                "reason": "mcp_order_payload_missing_order_id",
                "raw_order": order,
            }
        return submission_result(
            approval=approval, plan=plan, account=account, order=order, adapter="alpaca_mcp"
        )
    except AlpacaMcpUnavailable as exc:
        return {
            "status": "MCP_CLIENT_UNAVAILABLE",
            "adapter": "alpaca_mcp",
            "broker_submission": False,
            "reason": str(exc),
        }
    except Exception as exc:
        return {
            "status": "BROKER_ERROR",
            "adapter": "alpaca_mcp",
            "broker_submission": False,
            "reason": type(exc).__name__,
        }
    finally:
        close_quietly(client)


def reconcile_alpaca_mcp_paper_order(*, approval: dict, broker_submission: dict) -> dict:
    guard = reconcile_preconditions(
        approval=approval, broker_submission=broker_submission, adapter="alpaca_mcp"
    )
    if guard["status"] != "PASS":
        return guard["error"]

    if alpaca_credentials() is None:
        return credentials_missing("alpaca_mcp", submitted=True)

    try:
        client = load_alpaca_mcp_client()
    except AlpacaMcpUnavailable as exc:
        return {
            "status": "MCP_CLIENT_UNAVAILABLE",
            "adapter": "alpaca_mcp",
            "broker_submission": True,
            "reason": str(exc),
        }

    broker_order_id = guard["broker_order_id"]
    try:
        account_response = client.call_tool("get_account_info", {})
        account = (
            paper_account_from_payload(account_response.get("data"))
            if account_response.get("ok")
            else None
        )
        if account is None:
            return {
                "status": "ALPACA_ACCOUNT_QUERY_FAILED",
                "adapter": "alpaca_mcp",
                "broker_submission": True,
                "reason": account_response.get("reason") or "mcp_account_payload_unparsed",
            }

        order_response = client.call_tool("get_order_by_id", {"order_id": broker_order_id})
        if not order_response.get("ok"):
            return {
                "status": "BROKER_RECONCILE_ERROR",
                "adapter": "alpaca_mcp",
                "broker_submission": True,
                "reason": order_response.get("reason") or "mcp_order_lookup_failed",
            }
        order = order_response.get("data")
        if not isinstance(order, dict) or not extract_order_id(order):
            return order_not_found(
                adapter="alpaca_mcp",
                broker_order_id=broker_order_id,
                code=guard["code"],
                account=account,
            )
        return reconciliation_result(
            approval=approval,
            order=order,
            account=account,
            broker_order_id=broker_order_id,
            code=guard["code"],
            adapter="alpaca_mcp",
        )
    except AlpacaMcpUnavailable as exc:
        return {
            "status": "MCP_CLIENT_UNAVAILABLE",
            "adapter": "alpaca_mcp",
            "broker_submission": True,
            "reason": str(exc),
        }
    except Exception as exc:
        return {
            "status": "BROKER_RECONCILE_ERROR",
            "adapter": "alpaca_mcp",
            "broker_submission": True,
            "reason": type(exc).__name__,
        }
    finally:
        close_quietly(client)


def close_quietly(client) -> None:
    try:
        client.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def fake_submit_paper_order(*, approval: dict, alpaca: dict) -> dict:
    submitted_at = datetime.now(timezone.utc).isoformat()
    return {
        "status": "BROKER_SUBMITTED",
        "adapter": "fake",
        "broker_submission": True,
        "broker_order_id": f"FAKE-PAPER-{approval['id']}",
        "submitted_at": submitted_at,
        "symbol": approval.get("symbol"),
        "side": approval.get("side"),
        "quantity": approval.get("quantity"),
        "price": approval.get("price"),
        "asset_class": (approval.get("asset_class") or "equity").lower(),
        "environment": alpaca.get("environment", "PAPER"),
        "account_type": alpaca.get("account_type", "CASH"),
        "account_suffix": alpaca.get("account_suffix"),
    }


def fake_reconcile_paper_order(*, approval: dict, broker_submission: dict) -> dict:
    reconciled_at = datetime.now(timezone.utc).isoformat()
    return {
        "status": "BROKER_SUBMITTED",
        "adapter": "fake",
        "broker_submission": True,
        "broker_order_id": broker_submission.get("broker_order_id")
        or f"FAKE-PAPER-{approval['id']}",
        "broker_code": broker_submission.get("broker_code") or approval.get("symbol"),
        "order_status": broker_submission.get("order_status", "SUBMITTED"),
        "environment": broker_submission.get("environment", "PAPER"),
        "account_type": broker_submission.get("account_type"),
        "account_suffix": broker_submission.get("account_suffix"),
        "reconciled_at": reconciled_at,
        "raw_order": broker_submission,
    }


# ---------------------------------------------------------------------------
# Order construction
# ---------------------------------------------------------------------------


def build_order_plan(approval: dict, *, adapter: str) -> dict:
    """Validate the approval row and build the Alpaca order body it maps to."""

    def reject(status: str, reason: str) -> dict:
        return {
            "status": "REJECT",
            "error": {
                "status": status,
                "adapter": adapter,
                "broker_submission": False,
                "reason": reason,
            },
        }

    market = (approval.get("shariah_market") or "").upper()
    if market not in SUPPORTED_REAL_MARKETS:
        return reject(
            "UNSUPPORTED_MARKET",
            f"paper adapter currently supports {', '.join(sorted(SUPPORTED_REAL_MARKETS))} only",
        )

    code = normalize_order_code(approval)
    side = (approval.get("side") or "BUY").upper()
    quantity = approval.get("quantity")
    price = approval.get("price")
    if code is None:
        return reject("INVALID_SYMBOL", "symbol_required")
    if side not in {"BUY", "SELL"}:
        return reject("INVALID_SIDE", "side_must_be_BUY_or_SELL")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        return reject("INVALID_QUANTITY", "positive_integer_quantity_required")
    if not isinstance(price, (int, float)) or isinstance(price, bool) or price <= 0:
        return reject("INVALID_PRICE", "positive_price_required")

    intent = order_intent_from_approval(approval)
    asset_class = intent["asset_class"]
    if asset_class not in {"equity", "option"}:
        return reject("UNSUPPORTED_ASSET_CLASS", "asset_class_must_be_equity_or_option")

    client_order_id = f"amanah-queue-{approval['id']}"
    if asset_class == "equity":
        return {
            "status": "PASS",
            "asset_class": "equity",
            "broker_code": code,
            "side": side,
            "quantity": quantity,
            "price": price,
            "option_strategy": None,
            "position_intent": None,
            "body": {
                "symbol": code,
                "qty": str(quantity),
                "side": side_to_alpaca_side(side),
                "type": "limit",
                "limit_price": decimal_text(price),
                "time_in_force": "day",
                "extended_hours": False,
                "client_order_id": client_order_id,
            },
        }

    leg = option_leg_from_approval(intent, side=side)
    if leg["status"] != "PASS":
        return reject(leg["status"], leg["reason"])

    return {
        "status": "PASS",
        "asset_class": "option",
        "broker_code": leg["occ_symbol"],
        "side": side,
        "quantity": quantity,
        "price": price,
        "option_strategy": leg["strategy"],
        "position_intent": leg["position_intent"],
        "body": {
            "symbol": leg["occ_symbol"],
            "qty": str(quantity),
            "side": side_to_alpaca_side(side),
            "type": "limit",
            "limit_price": decimal_text(price),
            # Alpaca only accepts time_in_force=day for options, and no extended hours.
            "time_in_force": "day",
            "position_intent": leg["position_intent"],
            "client_order_id": client_order_id,
        },
    }


def order_intent_from_approval(approval: dict) -> dict:
    """Resolve what kind of order this is, from the row or its stored preview.

    approval_queue persists symbol/side/quantity/price as columns but keeps
    everything else inside the JSON payload, so an option order's asset_class and
    contract arrive under payload.preview rather than at the top level.
    """
    asset_class = approval.get("asset_class")
    option_contract = approval.get("option_contract")
    option_legs = approval.get("option_legs")
    if asset_class is None or option_contract is None:
        preview = preview_from_approval(approval)
        asset_class = asset_class if asset_class is not None else preview.get("asset_class")
        option_contract = (
            option_contract if option_contract is not None else preview.get("option_contract")
        )
        option_legs = option_legs if option_legs is not None else preview.get("option_legs")
    return {
        "asset_class": str(asset_class or "equity").strip().lower(),
        "option_contract": option_contract,
        "option_legs": option_legs,
    }


def preview_from_approval(approval: dict) -> dict:
    payload = approval.get("payload")
    parsed = {}
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
    elif isinstance(payload, dict):
        parsed = payload
    preview = parsed.get("preview")
    return preview if isinstance(preview, dict) else {}


def option_leg_from_approval(approval: dict, *, side: str) -> dict:
    """Build the single Level 1 option leg an approval row describes."""
    legs = approval.get("option_legs")
    if legs:
        return {
            "status": "UNSUPPORTED_OPTION_STRATEGY",
            "reason": "multi_leg_option_orders_are_out_of_scope_for_level_1",
        }

    contract = approval.get("option_contract")
    if not isinstance(contract, dict):
        return {"status": "INVALID_OPTION_CONTRACT", "reason": "option_contract_required"}

    strategy = str(contract.get("strategy") or "").strip().upper()
    if strategy not in SUPPORTED_OPTION_STRATEGIES:
        return {
            "status": "UNSUPPORTED_OPTION_STRATEGY",
            "reason": f"strategy_must_be_one_of_{'_or_'.join(sorted(SUPPORTED_OPTION_STRATEGIES))}",
        }

    option_type = str(contract.get("option_type") or "").strip().upper()
    if option_type != SUPPORTED_OPTION_STRATEGIES[strategy]:
        return {
            "status": "OPTION_STRUCTURE_MISMATCH",
            "reason": f"{strategy}_requires_option_type_{SUPPORTED_OPTION_STRATEGIES[strategy]}",
        }

    underlying = str(contract.get("underlying") or approval.get("symbol") or "").strip().upper()
    occ_symbol = build_option_occ_symbol(
        underlying, contract.get("expiration"), option_type, contract.get("strike")
    )
    if occ_symbol is None:
        return {
            "status": "INVALID_OPTION_CONTRACT",
            "reason": "underlying_expiration_and_positive_strike_required",
        }

    return {
        "status": "PASS",
        "strategy": strategy,
        "occ_symbol": occ_symbol,
        "position_intent": position_intent_for(side),
    }


def build_option_occ_symbol(underlying, expiration, option_type: str, strike) -> str | None:
    """Build an OCC-21 symbol, e.g. AAPL + 2026-09-18 + CALL + 350 -> AAPL260918C00350000."""
    root = str(underlying or "").strip().upper()
    if not root or not root.isalnum() or len(root) > 6:
        return None

    kind = str(option_type or "").strip().upper()
    if kind not in {"CALL", "PUT"}:
        return None

    expiry = parse_expiration(expiration)
    if expiry is None:
        return None

    try:
        strike_value = float(strike)
    except (TypeError, ValueError):
        return None
    if strike_value <= 0:
        return None
    strike_thousandths = int(round(strike_value * 1000))
    if strike_thousandths <= 0 or strike_thousandths > 99999999:
        return None

    return f"{root}{expiry.strftime('%y%m%d')}{kind[0]}{strike_thousandths:08d}"


def parse_expiration(expiration) -> date | None:
    if isinstance(expiration, datetime):
        return expiration.date()
    if isinstance(expiration, date):
        return expiration
    text = str(expiration or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def normalize_order_code(approval: dict) -> str | None:
    """Alpaca uses bare tickers; tolerate the Moomoo-style ``US.AAPL`` form."""
    symbol = (approval.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    if "." in symbol:
        symbol = symbol.rsplit(".", 1)[-1].strip()
    return symbol or None


def side_to_alpaca_side(side: str) -> str:
    return "sell" if str(side).upper() == "SELL" else "buy"


def position_intent_for(side: str) -> str:
    """Level 1 shorts open on SELL and close on BUY; no long-side opening here."""
    return "buy_to_close" if str(side).upper() == "BUY" else "sell_to_open"


def decimal_text(value) -> str:
    text = f"{float(value):.4f}".rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------------------
# Account handling and shared result shapes
# ---------------------------------------------------------------------------


def fetch_paper_account(*, credentials: dict, adapter: str, submitted: bool) -> dict:
    response = alpaca_request("GET", "/v2/account", credentials=credentials)
    if not response.get("ok"):
        return {
            "status": "REJECT",
            "error": {
                "status": "ALPACA_ACCOUNT_QUERY_FAILED",
                "adapter": adapter,
                "broker_submission": submitted,
                "reason": error_message(response),
            },
        }

    account = paper_account_from_payload(response.get("data"))
    if account is None:
        return {
            "status": "REJECT",
            "error": {
                "status": "ALPACA_ACCOUNT_QUERY_FAILED",
                "adapter": adapter,
                "broker_submission": submitted,
                "reason": "account_payload_unparsed",
            },
        }
    if account["blocked"]:
        return {
            "status": "REJECT",
            "error": {
                "status": "ALPACA_PAPER_ACCOUNT_BLOCKED",
                "adapter": adapter,
                "broker_submission": submitted,
                "reason": "alpaca_paper_account_blocked",
            },
        }
    if account["account_status"] != "ACTIVE":
        return {
            "status": "REJECT",
            "error": {
                "status": "ALPACA_PAPER_ACCOUNT_MISSING",
                "adapter": adapter,
                "broker_submission": submitted,
                "reason": "active_paper_account_not_found",
            },
        }
    return {"status": "PASS", "account": account}


def paper_account_from_payload(payload) -> dict | None:
    if not isinstance(payload, dict):
        return None
    account_number = str(payload.get("account_number") or payload.get("id") or "")
    if not account_number:
        return None
    return {
        "account_number": account_number,
        "account_suffix": account_number[-4:],
        "account_status": str(payload.get("status") or "UNKNOWN").upper(),
        "account_type": account_type_from_multiplier(payload.get("multiplier")),
        "blocked": bool(payload.get("trading_blocked")) or bool(payload.get("account_blocked")),
        "options_trading_level": options_level(payload),
        # Settled cash only. buying_power is inflated by margin, so it can never
        # stand as collateral evidence for a cash-secured put.
        "cash": _float_or_zero(payload.get("cash")),
        # Mark-to-market fields, for display only -- never used as gate/collateral
        # evidence. equity/portfolio_value move with live prices; last_equity is
        # the prior trading day's close, which is what a daily-change figure is
        # measured against.
        "equity": _float_or_zero(payload.get("equity")),
        "last_equity": _float_or_zero(payload.get("last_equity")),
        "buying_power": _float_or_zero(payload.get("buying_power")),
        "portfolio_value": _float_or_zero(payload.get("portfolio_value")),
        "raw_account": payload,
    }


def _float_or_zero(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def account_type_from_multiplier(multiplier) -> str:
    try:
        value = float(multiplier)
    except (TypeError, ValueError):
        return "UNKNOWN"
    return "MARGIN" if value > 1 else "CASH"


def options_level(payload: dict) -> int:
    for field in ["options_trading_level", "options_approved_level"]:
        value = payload.get(field)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def credentials_missing(adapter: str, *, submitted: bool) -> dict:
    return {
        "status": "CREDENTIALS_MISSING",
        "adapter": adapter,
        "broker_submission": submitted,
        "reason": "ALPACA_API_KEY_ID and ALPACA_SECRET_KEY must be set in backend/.env",
    }


def submission_result(
    *, approval: dict, plan: dict, account: dict, order: dict, adapter: str
) -> dict:
    result = {
        "status": "BROKER_SUBMITTED",
        "adapter": adapter,
        "broker_submission": True,
        "broker_order_id": extract_order_id(order),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "symbol": approval.get("symbol"),
        "broker_code": plan["broker_code"],
        "side": plan["side"],
        "quantity": plan["quantity"],
        "price": plan["price"],
        "asset_class": plan["asset_class"],
        "environment": "PAPER",
        "account_type": account["account_type"],
        "account_suffix": account["account_suffix"],
        "order_status": order.get("status"),
        "client_order_id": order.get("client_order_id") or plan["body"].get("client_order_id"),
    }
    if plan["asset_class"] == "option":
        result["option_strategy"] = plan["option_strategy"]
        result["position_intent"] = plan["position_intent"]
    return result


def reconcile_preconditions(*, approval: dict, broker_submission: dict, adapter: str) -> dict:
    market = (approval.get("shariah_market") or "").upper()
    if market not in SUPPORTED_REAL_MARKETS:
        return {
            "status": "REJECT",
            "error": {
                "status": "UNSUPPORTED_MARKET",
                "adapter": adapter,
                "broker_submission": True,
                "reason": f"paper reconciliation currently supports {', '.join(sorted(SUPPORTED_REAL_MARKETS))} only",
            },
        }

    broker_order_id = broker_submission.get("broker_order_id")
    code = broker_submission.get("broker_code") or normalize_order_code(approval)
    if not broker_order_id:
        return {
            "status": "REJECT",
            "error": {
                "status": "BROKER_ORDER_ID_MISSING",
                "adapter": adapter,
                "broker_submission": True,
                "reason": "broker_order_id_missing",
            },
        }
    if not code:
        return {
            "status": "REJECT",
            "error": {
                "status": "INVALID_SYMBOL",
                "adapter": adapter,
                "broker_submission": True,
                "reason": "symbol_required",
            },
        }
    return {"status": "PASS", "broker_order_id": str(broker_order_id), "code": code}


def order_not_found(*, adapter: str, broker_order_id: str, code: str, account: dict) -> dict:
    return {
        "status": "BROKER_ORDER_NOT_FOUND",
        "adapter": adapter,
        "broker_submission": True,
        "broker_order_id": str(broker_order_id),
        "broker_code": code,
        "environment": "PAPER",
        "account_type": account["account_type"],
        "account_suffix": account["account_suffix"],
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "reason": "order not found for the active Alpaca paper account",
    }


def reconciliation_result(
    *, approval: dict, order: dict, account: dict, broker_order_id: str, code: str, adapter: str
) -> dict:
    order_status = str(order.get("status") or "UNKNOWN")
    return {
        "status": lifecycle_status(order_status),
        "adapter": adapter,
        "broker_submission": True,
        "broker_order_id": str(broker_order_id),
        "broker_code": order.get("symbol") or code,
        "source": "orders",
        "order_status": order_status,
        # portfolio_store keys off an uppercase BUY/SELL side.
        "side": str(order.get("side") or approval.get("side") or "BUY").upper(),
        "asset_class": normalize_asset_class(order.get("asset_class")),
        "quantity": numeric_or_original(order.get("qty")),
        "price": numeric_or_original(order.get("limit_price") or approval.get("price")),
        "dealt_qty": numeric_or_original(order.get("filled_qty")),
        "dealt_avg_price": numeric_or_original(order.get("filled_avg_price")),
        "last_err_msg": order.get("reject_reason") or order.get("message"),
        "created_at_broker": order.get("created_at"),
        "updated_at_broker": order.get("updated_at"),
        "filled_at_broker": order.get("filled_at"),
        "environment": "PAPER",
        "account_type": account["account_type"],
        "account_suffix": account["account_suffix"],
        "reconciled_at": datetime.now(timezone.utc).isoformat(),
        "raw_order": order,
    }


def normalize_asset_class(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"us_option", "option"}:
        return "option"
    if text in {"us_equity", "equity"}:
        return "equity"
    return text or "equity"


def extract_order_id(order) -> str | None:
    if not isinstance(order, dict):
        return None
    for field in ["id", "order_id", "orderId"]:
        value = order.get(field)
        if value not in {None, ""}:
            return str(value)
    return None


def error_message(response: dict) -> str:
    data = response.get("data")
    if isinstance(data, dict):
        message = data.get("message") or data.get("error")
        if message:
            return str(message)
    return str(response.get("reason") or "alpaca_request_failed")


def broker_submission_from_approval(approval: dict) -> dict | None:
    payload = approval.get("payload")
    parsed = {}
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
    elif isinstance(payload, dict):
        parsed = payload
    broker_submission = parsed.get("broker_submission")
    if isinstance(broker_submission, dict):
        return broker_submission
    return None


def lifecycle_status(order_status: str) -> str:
    normalized = str(order_status).strip().upper()
    if normalized == "FILLED":
        return "BROKER_FILLED"
    if normalized == "PARTIALLY_FILLED":
        return "BROKER_PARTIAL_FILL"
    if normalized in {"CANCELED", "CANCELLED", "PENDING_CANCEL"}:
        return "BROKER_CANCELLED"
    if normalized in {"REJECTED", "SUSPENDED", "STOPPED"}:
        return "BROKER_REJECTED"
    if normalized in {"EXPIRED", "DONE_FOR_DAY"}:
        return "BROKER_EXPIRED"
    if normalized in {
        "NEW",
        "ACCEPTED",
        "PENDING_NEW",
        "PENDING_REPLACE",
        "PENDING_REVIEW",
        "ACCEPTED_FOR_BIDDING",
        "REPLACED",
        "HELD",
        "CALCULATED",
    }:
        return "BROKER_SUBMITTED"
    return "BROKER_STATUS_UNKNOWN"


def numeric_or_original(value):
    if value in {None, ""}:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------------------
# Read-only status probe (mirrors moomoo_status.check_moomoo_status)
# ---------------------------------------------------------------------------


def check_market_clock() -> dict:
    """Is the market open right now? Read-only; places nothing.

    Exists so a caller can tell that a quote is stale. A limit priced off a
    closed-market quote is yesterday's number, and for a short-dated option
    overnight decay can leave a sell-to-open permanently non-marketable -- it
    rests all session and expires unfilled.

    ``is_open`` is None, never False, when the clock cannot be read. "Unknown"
    and "closed" are different claims, and guessing "open" would suppress
    exactly the warning this exists to produce.
    """
    credentials = alpaca_credentials()
    if credentials is None:
        return {"status": "credentials_missing", "is_open": None}

    response = alpaca_request("GET", "/v2/clock", credentials=credentials)
    if not response.get("ok"):
        return {"status": "unreachable", "is_open": None, "reason": error_message(response)}

    data = response.get("data")
    if not isinstance(data, dict) or "is_open" not in data:
        return {"status": "unparsed", "is_open": None}

    return {
        "status": "ok",
        "is_open": bool(data.get("is_open")),
        "next_open": data.get("next_open"),
        "next_close": data.get("next_close"),
        "timestamp": data.get("timestamp"),
    }


def check_alpaca_status() -> dict:
    settings = load_settings()
    base = {
        "base_url": ALPACA_PAPER_BASE_URL,
        "mode": settings.alpaca_mode,
        "adapter": settings.paper_execution_adapter,
        "paper_account_ready": False,
        "paper_execution_enabled": settings.paper_execution_enabled,
        "broker_submission": False,
        "live_trading": False,
    }

    credentials = alpaca_credentials()
    if credentials is None:
        return {**base, "status": "credentials_missing", "reason": "alpaca_credentials_missing"}

    response = alpaca_request("GET", "/v2/account", credentials=credentials)
    if not response.get("ok"):
        return {**base, "status": "unreachable", "reason": error_message(response)}

    account = paper_account_from_payload(response.get("data"))
    if account is None:
        return {**base, "status": "paper_account_missing", "reason": "account_payload_unparsed"}
    if account["blocked"]:
        return {
            **base,
            "status": "paper_account_blocked",
            "reason": "alpaca_paper_account_blocked",
            "account_status": account["account_status"],
            "account_suffix": account["account_suffix"],
        }
    if account["account_status"] != "ACTIVE":
        return {
            **base,
            "status": "paper_account_missing",
            "reason": "active_paper_account_not_found",
            "account_status": account["account_status"],
        }

    daily_change = round(account["equity"] - account["last_equity"], 2)
    daily_change_pct = (
        round(daily_change / account["last_equity"] * 100, 4) if account["last_equity"] else None
    )
    return {
        **base,
        "status": "paper_account_ready",
        "paper_account_ready": True,
        "environment": "PAPER",
        "account_type": account["account_type"],
        "account_status": account["account_status"],
        "account_suffix": account["account_suffix"],
        "options_trading_level": account["options_trading_level"],
        "cash": account["cash"],
        # Live mark-to-market, for dashboard display only -- account_shariah_gate
        # and the risk overlay still size off the static PAPER_ACCOUNT_EQUITY
        # baseline (see provision_cash_account.py), not these fields.
        "equity": account["equity"],
        "last_equity": account["last_equity"],
        "buying_power": account["buying_power"],
        "portfolio_value": account["portfolio_value"],
        "daily_change": daily_change,
        "daily_change_pct": daily_change_pct,
    }


def fetch_broker_positions() -> dict:
    """Live broker positions, straight from Alpaca -- read-only reporting only.

    Deliberately separate from portfolio_store's local position ledger: this
    never writes anything, and it includes options, which portfolio_store does
    not track as positions (see CLAUDE.md Known limitation 2 -- booking these
    into paper_positions at the equity share model would be exactly the bug
    that limitation warns against). This function only reads what the broker
    already knows and reports it.
    """
    credentials = alpaca_credentials()
    if credentials is None:
        return {"status": "credentials_missing", "positions": []}

    response = alpaca_request("GET", "/v2/positions", credentials=credentials)
    if not response.get("ok"):
        return {"status": "unreachable", "reason": error_message(response), "positions": []}

    raw = response.get("data")
    if not isinstance(raw, list):
        return {"status": "unreachable", "reason": "positions_payload_unparsed", "positions": []}

    positions = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        positions.append(
            {
                "symbol": entry.get("symbol"),
                "asset_class": entry.get("asset_class"),
                "side": entry.get("side"),
                "quantity": _float_or_zero(entry.get("qty")),
                "average_entry_price": _float_or_zero(entry.get("avg_entry_price")),
                "current_price": _float_or_zero(entry.get("current_price")),
                "market_value": _float_or_zero(entry.get("market_value")),
                "unrealized_pnl": _float_or_zero(entry.get("unrealized_pl")),
                "unrealized_pnl_pct": _float_or_zero(entry.get("unrealized_plpc")) * 100,
            }
        )
    return {"status": "ok", "positions": positions}
