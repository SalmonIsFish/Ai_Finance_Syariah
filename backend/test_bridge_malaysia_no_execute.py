"""A market the backend will not execute gets no execute button, and no execute call.

Malaysian screening and pricing genuinely work: 4197 reaches READY_FOR_APPROVAL on 312
real Bursa bars with the SC document hash on the record. Execution is the part that
depends on configuration, so the refusal is scoped exactly there -- tap 1 is offered and
real, tap 2 depends on what the backend says it will submit.

**An earlier version of this file said a Malaysian order "would be sent to the wrong
broker entirely". That was wrong.** `alpaca_paper_adapter.SUPPORTED_REAL_MARKETS` is
`{"US"}` and a non-US approval was refused with `UNSUPPORTED_MARKET` before anything was
built, so the outcome was always safe. Corrected here rather than quietly dropped,
because a confident wrong claim costs more than a gap.

**The relay no longer decides this itself.** It used to hardcode "Malaysia cannot
execute", which was true and was still a second copy of a rule owned by
`broker_routing.py`. It now reads `execution_markets` from `/paper/status`, so enabling
Bursa in the backend is all it takes -- and a test below proves the button appears.
"""

import pytest
from bridge import proposals
from bridge.markets import MARKET_MY, MARKET_US, detect_market
from bridge.relay import ACTION_APPROVE, ACTION_EXECUTE, Relay

OWNER_ID = 42424242
CHAT_ID = -100123456789

MY_PREVIEW = {
    "status": "READY_FOR_APPROVAL",
    "symbol": "4197",
    "side": "BUY",
    "quantity": 100,
    "price": 2.47,
    "notional": 247.0,
    "asset_class": "equity",
    "blockers": [],
    "blocker_messages": [],
    "agent_summary": {
        "shariah": {
            "status": "PASS",
            "market": "MY",
            "provider": "SC_MY_APPROVED_PUBLICATION",
            "reason": "authoritative_compliant",
            "details": {"status": "PASS", "publication_date": "2026-05-29"},
        },
        "quant": {"signal": "BUY", "reason": "pullback", "price_source": "yahoo", "bars": 312},
        "risk": {"status": "PASS", "reason": "risk_limits_passed"},
    },
}

US_PREVIEW = dict(MY_PREVIEW, symbol="AAPL", price=207.60)

APPROVED = {"queue_id": 31, "approval": {"status": "APPROVED_PAPER_READY", "shariah_trace": "ok"}}


MY_DISABLED = {
    "US": {"adapter": "alpaca_mcp", "enabled": True, "reason": None},
    "MY": {
        "adapter": "disabled",
        "enabled": False,
        "reason": "no execution adapter is configured for MY; set PAPER_EXECUTION_ADAPTER_MY to enable it",
    },
}

MY_ENABLED = {
    "US": {"adapter": "alpaca_mcp", "enabled": True, "reason": None},
    "MY": {"adapter": "moomoo", "enabled": True, "reason": None},
}


class FakeClient:
    """The relay asks /paper/status which markets are executable; this answers."""

    def __init__(self, markets=None, status_ok=True):
        self.calls = []
        self._markets = markets if markets is not None else MY_DISABLED
        self._status_ok = status_ok

        class _Config:
            operator_key = "f" * 64

        self._config = _Config()

    def call(self, route_name, *, path_params=None, query=None, body=None, bypass_cache=False):
        self.calls.append(route_name)
        if route_name == "paper_approval":
            return {"status": "OK", "route": route_name, "data": APPROVED}
        if route_name == "paper_status":
            if not self._status_ok:
                return {
                    "status": "UNAVAILABLE",
                    "route": route_name,
                    "data": {},
                    "reason": "http_503",
                }
            return {
                "status": "OK",
                "route": route_name,
                "data": {"execution_markets": self._markets},
            }
        return {"status": "OK", "route": route_name, "data": {}}


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def __call__(self, method, token, payload, *, timeout=None):
        self.sent.append({"method": method, "payload": payload})
        if method == "sendMessage":
            return {"ok": True, "data": {"result": {"message_id": 7}}}
        return {"ok": True, "data": {"result": True}}

    def texts(self):
        return "\n".join(s["payload"].get("text", "") for s in self.sent)

    def last_buttons(self):
        for entry in reversed(self.sent):
            markup = entry["payload"].get("reply_markup")
            if markup is not None:
                return markup.get("inline_keyboard")
        return None


@pytest.fixture
def db():
    connection = proposals.connect(":memory:")
    yield connection
    connection.close()


def make(db, markets=None, status_ok=True):
    client = FakeClient(markets, status_ok)
    telegram = FakeTelegram()
    relay = Relay(
        client,
        db,
        token="t",
        owner_user_id=OWNER_ID,
        chat_id=CHAT_ID,
        transport=telegram,
    )
    return relay, client, telegram


def press(action, nonce):
    return {
        "id": "cb",
        "from": {"id": OWNER_ID},
        "message": {"chat": {"id": CHAT_ID}, "message_id": 7},
        "data": f"{action}:{nonce}",
    }


def test_a_bursa_code_is_detected_as_malaysian():
    assert detect_market("4197") == MARKET_MY
    assert detect_market("AAPL") == MARKET_US


def test_a_malaysian_proposal_is_approvable(db):
    """The refusal is scoped to execution. Screening and approval genuinely work."""
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    result = relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert result["status"] == "OK"
    assert "paper_approval" in client.calls
    assert proposals.load_proposal(db, record["proposal_id"])["status"] == proposals.STATUS_QUEUED


def test_a_malaysian_proposal_gets_no_execute_button(db):
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert telegram.last_buttons() in ([], None), (
        "no execute button may be offered for a market the backend will not submit for"
    )


def test_the_reason_is_stated_rather_than_the_button_silently_missing(db):
    """A missing button with no explanation reads as a bug, not as a refusal."""
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert "MY execution is not available" in telegram.texts()
    # The reason comes from the backend, not from a sentence the relay made up.
    assert "PAPER_EXECUTION_ADAPTER_MY" in telegram.texts()


def test_the_tap_two_payload_is_not_shown_for_a_malaysian_order(db):
    """Showing a payload that cannot be sent invites someone to send it by hand."""
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert "/paper/execute/" not in telegram.texts()


def test_execute_is_refused_even_if_the_callback_is_replayed(db):
    """Belt and braces: not offering a button is not the same as refusing the action."""
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)
    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    relay.handle_callback(press(ACTION_EXECUTE, record["callback_nonce"]))

    assert "paper_execute" not in client.calls, (
        "a replayed execute callback on a Malaysian order must still be refused"
    )


def test_the_refusal_is_a_named_reason_not_a_crash(db):
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)
    proposals.mark_queued(db, record["proposal_id"], approval=APPROVED)
    stored = proposals.load_proposal(db, record["proposal_id"])

    outcome = relay.execute(stored)

    assert outcome["status"] == "REJECT"
    assert outcome["reason"] == "market_execution_unavailable"
    assert outcome["market"] == MARKET_MY


def test_a_us_order_still_gets_its_execute_button(db):
    """The refusal must be scoped to Malaysia, not a blanket freeze."""
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=US_PREVIEW, chat_id=CHAT_ID, market=MARKET_US)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    buttons = telegram.last_buttons()
    assert buttons, "a US order must still be executable"
    labels = [button["text"] for row in buttons for button in row]
    assert "Confirm EXECUTE" in labels
    assert "/paper/execute/31" in telegram.texts()


# --- and once Malaysia is enabled, the same path opens -------------------------------


def test_enabling_malaysia_in_the_backend_produces_an_execute_button(db):
    """The refusal must be a live reading, not a permanent property of the relay.

    Before per-market routing existed the relay hardcoded "Malaysia cannot execute".
    That was true and was still a second copy of a rule owned by broker_routing.py. Now
    it asks, so turning Bursa on in the backend is all it takes.
    """
    relay, client, telegram = make(db, markets=MY_ENABLED)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    labels = [b["text"] for row in (telegram.last_buttons() or []) for b in row]
    assert "Confirm EXECUTE" in labels
    assert "/paper/execute/31" in telegram.texts()


def test_an_enabled_malaysian_order_actually_executes(db):
    relay, client, telegram = make(db, markets=MY_ENABLED)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)
    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    relay.handle_callback(press(ACTION_EXECUTE, record["callback_nonce"]))

    assert "paper_execute" in client.calls


def test_an_unreachable_backend_means_no_execute_button(db):
    """Fail closed: a client that assumed yes would offer a button the gates then refuse."""
    relay, client, telegram = make(db, status_ok=False)
    record = proposals.create_proposal(db, preview=US_PREVIEW, chat_id=CHAT_ID, market=MARKET_US)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert telegram.last_buttons() in ([], None)
    assert "not available" in telegram.texts()


def test_a_market_the_backend_does_not_mention_is_refused(db):
    """An unknown market must not inherit another market's permission."""
    relay, client, telegram = make(db, markets={"US": MY_ENABLED["US"]})
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert telegram.last_buttons() in ([], None)
