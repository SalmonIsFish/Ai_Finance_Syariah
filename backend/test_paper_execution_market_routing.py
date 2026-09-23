"""A Bursa order reaches Moomoo and a US order reaches Alpaca, through the real path.

test_broker_routing.py checks the decision in isolation. This drives
`execute_paper_order` itself -- the real gate chain, the real status probe selection, the
real submit dispatch -- with only the two network seams replaced, because the routing
decision is worth little if the thing that acts on it reads a different variable.

The assertion that matters most is negative: with Malaysia enabled, a US order must still
go to Alpaca. A change that routed everything to Moomoo would satisfy "Bursa works" and be
a serious regression.
"""

import json
import os
import sqlite3

import paper_execution
import pytest
from approval_queue import record_approval
from paper_execution import execute_paper_order

READY = {
    "status": "paper_account_ready",
    "paper_account_ready": True,
    "environment": "SIMULATE",
    "account_type": "CASH",
    "account_status": "ACTIVE",
    "account_suffix": "1234",
    "broker_submission": False,
}

_ENV_KEYS = [
    "PAPER_EXECUTION_ADAPTER",
    "PAPER_EXECUTION_ADAPTER_MY",
    "PAPER_EXECUTION_ENABLED",
    "TRADING_MODE",
    "MOOMOO_MODE",
    "PAPER_ACCOUNT_EQUITY",
]


@pytest.fixture(autouse=True)
def _env():
    original = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["PAPER_EXECUTION_ENABLED"] = "true"
    os.environ["TRADING_MODE"] = "approval"
    os.environ["MOOMOO_MODE"] = "paper"
    os.environ["PAPER_ACCOUNT_EQUITY"] = "100000"
    yield
    for key, value in original.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def add_approval(conn, *, symbol, market):
    preview = {
        "status": "READY_FOR_APPROVAL",
        "execution": "PAPER_ONLY",
        "broker_submission": False,
        "symbol": symbol,
        "side": "BUY",
        "quantity": 1,
        "asset_class": "equity",
        "price": 100.0,
        "notional": 100.0,
        "blockers": [],
        "quote_snapshot": {"symbol": symbol, "latest_close": 100.0, "source": "test"},
        "agent_summary": {
            "shariah": {"status": "PASS", "market": market},
            "quant": {"signal": "BUY"},
            "risk": {"status": "PASS"},
        },
        "shariah": {"status": "PASS", "market": market},
        "risk": {"status": "PASS"},
    }
    approval = {
        "status": "APPROVED_PAPER_READY",
        "broker_submission": False,
        "shariah_trace": "test",
        # The audit cross-checks the candidate against the row, so these must agree --
        # which is the gate doing its job, not fixture ceremony.
        "candidate": {
            "symbol": symbol,
            "signal": "BUY",
            "side": "BUY",
            "quantity": 1,
            "price": 100.0,
            "notional": 100.0,
        },
    }
    # shariah_market is derived from the verdict dict, which is exactly how the real
    # /paper/approval path fills it -- so the routing reads the same field in the test
    # as it does in production.
    return record_approval(conn, preview=preview, approval=approval, approved_by_user=True)["id"]


class Spy:
    """Records which adapter was asked to submit, and with what adapter name."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def __call__(self, approval, broker, adapter=None):
        self.calls.append({"symbol": approval.get("symbol"), "adapter": adapter})
        return {
            "status": "PASS",
            "adapter": adapter or self.name,
            "broker_submission": True,
            "broker_order_id": "test-order",
            "environment": "SIMULATE",
        }


@pytest.fixture
def spies(monkeypatch):
    alpaca, moomoo = Spy("alpaca_mcp"), Spy("moomoo")
    monkeypatch.setattr(paper_execution, "submit_alpaca_order", alpaca)
    monkeypatch.setattr(paper_execution, "submit_paper_order", moomoo)
    monkeypatch.setattr(paper_execution, "check_alpaca_status", lambda: dict(READY))
    monkeypatch.setattr(paper_execution, "check_moomoo_status", lambda market="US": dict(READY))
    return {"alpaca": alpaca, "moomoo": moomoo}


def test_a_malaysian_order_reaches_moomoo_not_alpaca(connection, spies):
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"

    result = execute_paper_order(connection, add_approval(connection, symbol="4197", market="MY"))

    assert result["broker_submission"] is True
    assert spies["moomoo"].calls == [{"symbol": "4197", "adapter": "moomoo"}]
    assert spies["alpaca"].calls == [], "a Bursa order must never be offered to Alpaca"


def test_a_us_order_still_reaches_alpaca_with_malaysia_enabled(connection, spies):
    """The regression that would look like success: everything routed to one broker."""
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"

    result = execute_paper_order(connection, add_approval(connection, symbol="AAPL", market="US"))

    assert result["broker_submission"] is True
    assert spies["alpaca"].calls == [{"symbol": "AAPL", "adapter": "alpaca_mcp"}]
    assert spies["moomoo"].calls == []


def test_a_malaysian_order_is_refused_while_malaysia_is_off(connection, spies):
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ.pop("PAPER_EXECUTION_ADAPTER_MY", None)

    result = execute_paper_order(connection, add_approval(connection, symbol="4197", market="MY"))

    assert result["status"] == "ADAPTER_NOT_CONFIGURED_FOR_MARKET"
    assert result["broker_submission"] is False
    assert spies["alpaca"].calls == []
    assert spies["moomoo"].calls == []
    # The reason must name the real cause and the fix, not Alpaca's own limits.
    assert "PAPER_EXECUTION_ADAPTER_MY" in result["routing"]["reason"]


def test_the_status_probe_matches_the_adapter_that_will_submit(connection, monkeypatch):
    """A Bursa order gated on the Alpaca account is a precondition about the wrong thing."""
    probed = []
    monkeypatch.setattr(
        paper_execution, "check_alpaca_status", lambda: probed.append("alpaca") or dict(READY)
    )
    monkeypatch.setattr(
        paper_execution,
        "check_moomoo_status",
        lambda market="US": probed.append(f"moomoo:{market}") or dict(READY),
    )
    monkeypatch.setattr(paper_execution, "submit_alpaca_order", Spy("alpaca_mcp"))
    monkeypatch.setattr(paper_execution, "submit_paper_order", Spy("moomoo"))

    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"

    execute_paper_order(connection, add_approval(connection, symbol="4197", market="MY"))
    assert probed == ["moomoo:MY"]

    probed.clear()
    execute_paper_order(connection, add_approval(connection, symbol="AAPL", market="US"))
    assert probed == ["alpaca"]


def test_the_recorded_adapter_lets_reconcile_route_itself(connection, spies):
    """reconcile_for_approval already dispatches on the stored adapter, not the setting.

    That is what makes a migration period safe: an order submitted through Moomoo
    reconciles through Moomoo even after the configuration changes.
    """
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca_mcp"
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"
    queue_id = add_approval(connection, symbol="4197", market="MY")

    execute_paper_order(connection, queue_id)

    from approval_queue import get_approval

    stored = json.loads(get_approval(connection, queue_id)["payload"])
    assert stored["broker_submission"]["adapter"] == "moomoo"


@pytest.mark.parametrize("switch", ["fake", "disabled"])
def test_a_global_switch_still_answers_for_both_markets(connection, spies, switch):
    os.environ["PAPER_EXECUTION_ADAPTER"] = switch
    os.environ["PAPER_EXECUTION_ADAPTER_MY"] = "moomoo"

    result = execute_paper_order(connection, add_approval(connection, symbol="4197", market="MY"))

    if switch == "disabled":
        assert result["status"] == "ADAPTER_NOT_CONFIGURED"
        assert spies["moomoo"].calls == []
    else:
        assert spies["moomoo"].calls[0]["adapter"] == "fake"
