"""A Malaysian order may be previewed and approved, and must never reach execute.

`paper_execution.py:139` picks the broker adapter **globally** --
`use_alpaca = settings.paper_execution_adapter in ALPACA_ADAPTERS` -- and production runs
`alpaca_mcp`. Alpaca has no Bursa access of any kind, so a Malaysian order submitted today
would be sent to the wrong broker entirely.

The Malaysian screening and pricing path is genuinely working: 4197 reached
READY_FOR_APPROVAL on 312 real Bursa bars from Yahoo, with the SC document hash on the
record. So the refusal is scoped exactly to execution -- tap 1 is offered and real, tap 2
is not offered at all.

This is enforced in code and asserted here rather than left as a comment, because the
difference between "we chose not to" and "it cannot" is the whole point of a gate.
"""

import pytest
from bridge import proposals
from bridge.markets import MARKET_MY, MARKET_US, detect_market
from bridge.relay import ACTION_APPROVE, ACTION_EXECUTE, MY_EXECUTION_NOTE, Relay

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


class FakeClient:
    def __init__(self):
        self.calls = []

        class _Config:
            operator_key = "f" * 64

        self._config = _Config()

    def call(self, route_name, *, path_params=None, query=None, body=None, bypass_cache=False):
        self.calls.append(route_name)
        if route_name == "paper_approval":
            return {"status": "OK", "route": route_name, "data": APPROVED}
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


def make(db):
    client = FakeClient()
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
        "no execute button may be offered for a Bursa order -- the adapter is global and "
        "production runs Alpaca, which has no Bursa access"
    )


def test_the_reason_is_stated_rather_than_the_button_silently_missing(db):
    """A missing button with no explanation reads as a bug, not as a refusal."""
    relay, client, telegram = make(db)
    record = proposals.create_proposal(db, preview=MY_PREVIEW, chat_id=CHAT_ID, market=MARKET_MY)

    relay.handle_callback(press(ACTION_APPROVE, record["callback_nonce"]))

    assert MY_EXECUTION_NOTE in telegram.texts()
    assert "no Bursa access" in telegram.texts()


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
    assert outcome["reason"] == "malaysian_execution_unavailable"


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
