"""The two-tap flow: one order per approval, whatever Telegram does.

Telegram redelivers callbacks. A double tap is the normal case, and the cost of losing
that race is two orders on a real broker. So the assertions here are mostly about what
does NOT happen: no second request, no resend after a timeout, no execute button on a
proposal that was never approved.

Nothing reaches Telegram or the API -- both are seams.
"""

import json

import pytest
from bridge import proposals
from bridge.relay import ACTION_APPROVE, ACTION_EXECUTE, ACTION_REJECT, EXECUTE_PHRASE, Relay

OWNER_ID = 42424242
CHAT_ID = -100123456789

READY_PREVIEW = {
    "status": "READY_FOR_APPROVAL",
    "symbol": "AAPL",
    "side": "BUY",
    "quantity": 1,
    "price": 207.60,
    "notional": 207.60,
    "asset_class": "equity",
    "blockers": [],
    "blocker_messages": [],
    "agent_summary": {
        "shariah": {"status": "PASS", "market": "US", "provider": "SEC_EDGAR", "reason": "ok"},
        "quant": {"signal": "BUY", "reason": "breakout", "price_source": "alpaca", "bars": 220},
        "risk": {"status": "PASS", "reason": "risk_limits_passed"},
    },
}

APPROVED = {
    "approval_id": 1,
    "queue_id": 14,
    "broker_submission": False,
    "approval": {"status": "APPROVED_PAPER_READY", "shariah_trace": "AAPL: underlying=PASS"},
}


class FakeClient:
    """Records API calls and returns canned responses keyed by route name."""

    def __init__(self, responses=None, operator_key="f" * 64):
        self.calls = []
        self._responses = responses or {}

        class _Config:
            pass

        self._config = _Config()
        self._config.operator_key = operator_key

    # The relay reads which markets are executable from /paper/status rather than
    # restating the rule, so a client that cannot answer this makes it fail closed --
    # which is correct, and would make every test below assert the same refusal.
    EXECUTABLE = {
        "status": "OK",
        "route": "paper_status",
        "data": {
            "execution_markets": {
                "US": {"adapter": "alpaca_mcp", "enabled": True, "reason": None},
                "MY": {"adapter": "moomoo", "enabled": True, "reason": None},
            }
        },
    }

    def call(self, route_name, *, path_params=None, query=None, body=None, bypass_cache=False):
        self.calls.append({"route": route_name, "path_params": path_params, "body": body})
        canned = self._responses.get(route_name)
        if canned is None and route_name == "paper_status":
            canned = self.EXECUTABLE
        if callable(canned):
            canned = canned()
        if canned is None:
            canned = {"status": "OK", "route": route_name, "data": {}}
        return canned

    def routes_called(self):
        """Route names, minus the capability lookup, which is not an action."""
        return [call["route"] for call in self.calls if call["route"] != "paper_status"]


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self._next_message_id = 1000

    def __call__(self, method, token, payload, *, timeout=None):
        self.sent.append({"method": method, "payload": payload})
        if method == "sendMessage":
            self._next_message_id += 1
            return {"ok": True, "data": {"result": {"message_id": self._next_message_id}}}
        return {"ok": True, "data": {"result": True}}

    def texts(self):
        return [s["payload"].get("text", "") for s in self.sent]

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


def make_relay(db, client=None, telegram=None):
    return Relay(
        client if client is not None else FakeClient(),
        db,
        token="test-token",
        owner_user_id=OWNER_ID,
        chat_id=CHAT_ID,
        transport=telegram if telegram is not None else FakeTelegram(),
    )


def callback(action, nonce, *, user_id=OWNER_ID, chat_id=CHAT_ID):
    return {
        "id": "cb1",
        "from": {"id": user_id},
        "message": {"chat": {"id": chat_id}, "message_id": 1001},
        "data": f"{action}:{nonce}",
    }


def seed(db, preview=None, market="US"):
    return proposals.create_proposal(
        db, preview=preview or READY_PREVIEW, chat_id=CHAT_ID, market=market
    )


# --- identity ------------------------------------------------------------------------


def test_a_stranger_in_the_chat_cannot_press_the_button():
    """Chat membership is not identity; the presser must be the owner."""
    telegram = FakeTelegram()
    client = FakeClient()
    connection = proposals.connect(":memory:")
    relay = make_relay(connection, client, telegram)
    record = seed(connection)

    result = relay.handle_callback(callback(ACTION_APPROVE, record["callback_nonce"], user_id=999))

    assert result["reason"] == "unauthorised"
    assert client.calls == [], "an unauthorised press must not reach the API at all"
    connection.close()


def test_a_press_from_another_chat_is_refused(db):
    client = FakeClient()
    relay = make_relay(db, client)
    record = seed(db)
    result = relay.handle_callback(callback(ACTION_APPROVE, record["callback_nonce"], chat_id=-1))
    assert result["reason"] == "unauthorised"
    assert client.calls == []


def test_an_unknown_nonce_is_refused(db):
    client = FakeClient()
    relay = make_relay(db, client)
    assert relay.handle_callback(callback(ACTION_APPROVE, "made-up"))["reason"] == "unknown_nonce"
    assert client.calls == []


# --- tap 1 ---------------------------------------------------------------------------


def test_tap_one_queues_and_uses_no_operator_route(db):
    client = FakeClient(
        {"paper_approval": {"status": "OK", "route": "paper_approval", "data": APPROVED}}
    )
    telegram = FakeTelegram()
    relay = make_relay(db, client, telegram)
    record = seed(db)

    result = relay.handle_callback(callback(ACTION_APPROVE, record["callback_nonce"]))

    assert result["status"] == "OK"
    assert client.routes_called() == ["paper_approval"]
    assert "paper_execute" not in client.routes_called(), "tap 1 must never execute"
    stored = proposals.load_proposal(db, record["proposal_id"])
    assert stored["status"] == proposals.STATUS_QUEUED
    assert stored["queue_id"] == 14


def test_tap_one_reply_shows_the_exact_tap_two_payload(db):
    client = FakeClient(
        {"paper_approval": {"status": "OK", "route": "paper_approval", "data": APPROVED}}
    )
    telegram = FakeTelegram()
    relay = make_relay(db, client, telegram)
    record = seed(db)

    relay.handle_callback(callback(ACTION_APPROVE, record["callback_nonce"]))

    text = "\n".join(telegram.texts())
    assert "Queued as #14" in text
    assert json.dumps({"confirmation_phrase": EXECUTE_PHRASE}) in text
    assert "/paper/execute/14" in text


def test_an_unapproved_result_renders_no_execute_button(db):
    """PENDING_APPROVAL and REJECT both terminate. No button, no second chance."""
    refused = {"approval_id": 2, "queue_id": None, "approval": {"status": "PENDING_APPROVAL"}}
    client = FakeClient(
        {"paper_approval": {"status": "OK", "route": "paper_approval", "data": refused}}
    )
    telegram = FakeTelegram()
    relay = make_relay(db, client, telegram)
    record = seed(db)

    relay.handle_callback(callback(ACTION_APPROVE, record["callback_nonce"]))

    assert telegram.last_buttons() in ([], None)
    assert proposals.load_proposal(db, record["proposal_id"])["status"] == proposals.STATUS_REJECTED


# --- tap 2 and idempotency -----------------------------------------------------------


def _queued(db, client, telegram):
    relay = make_relay(db, client, telegram)
    record = seed(db)
    relay.handle_callback(callback(ACTION_APPROVE, record["callback_nonce"]))
    return relay, proposals.load_proposal(db, record["proposal_id"])


def test_two_execute_presses_submit_exactly_once(db):
    client = FakeClient(
        {
            "paper_approval": {"status": "OK", "route": "paper_approval", "data": APPROVED},
            "paper_execute": {
                "status": "OK",
                "route": "paper_execute",
                "data": {"status": "BROKER_SUBMITTED"},
            },
        }
    )
    telegram = FakeTelegram()
    relay, record = _queued(db, client, telegram)

    first = relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))
    second = relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))

    assert first["outcome"]["status"] == "OK"
    assert second["reason"].startswith("stale_") or second["outcome"]["status"] == "ALREADY_CLAIMED"
    assert client.routes_called().count("paper_execute") == 1, (
        "a redelivered callback must not produce a second broker submission"
    )


def test_the_claim_is_atomic_at_the_database_level(db):
    """claim_execution is one conditional UPDATE; the second caller loses, not both."""
    record = seed(db)
    proposals.mark_queued(db, record["proposal_id"], approval=APPROVED)

    first = proposals.claim_execution(db, record["proposal_id"])
    second = proposals.claim_execution(db, record["proposal_id"])

    assert first["status"] == "OK"
    assert second["status"] == "ALREADY_CLAIMED"


def test_a_second_proposal_cannot_claim_the_same_queue_id(db):
    """UNIQUE(queue_id) is the belt to claim_execution's brace."""
    first = seed(db)
    second = seed(db)
    assert proposals.mark_queued(db, first["proposal_id"], approval=APPROVED)["status"] == "OK"
    outcome = proposals.mark_queued(db, second["proposal_id"], approval=APPROVED)
    assert outcome["status"] == "REJECT"
    assert outcome["reason"] == "queue_id_already_claimed"


@pytest.mark.parametrize(
    "status",
    [
        proposals.STATUS_PENDING,
        proposals.STATUS_EXPIRED,
        proposals.STATUS_HELD,
        proposals.STATUS_QUEUE_EXPIRED,
        proposals.STATUS_EXECUTED,
    ],
)
def test_execute_never_reaches_the_client_from_a_non_queued_state(db, status):
    client = FakeClient()
    relay = make_relay(db, client)
    record = seed(db)
    db.execute(
        "UPDATE proposals SET status = ? WHERE proposal_id = ?", (status, record["proposal_id"])
    )
    db.commit()

    relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))

    assert "paper_execute" not in client.routes_called(), status


def test_a_timeout_is_recorded_as_uncertain_and_never_retried(db):
    """The request may have reached the broker. Resending to find out is the bug."""
    client = FakeClient(
        {
            "paper_approval": {"status": "OK", "route": "paper_approval", "data": APPROVED},
            "paper_execute": {
                "status": "UNKNOWN",
                "route": "paper_execute",
                "data": {},
                "reason": "execute_timeout",
            },
        }
    )
    telegram = FakeTelegram()
    relay, record = _queued(db, client, telegram)

    outcome = relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))

    assert outcome["outcome"]["status"] == "EXECUTION_UNCERTAIN"
    stored = proposals.load_proposal(db, record["proposal_id"])
    assert stored["status"] == proposals.STATUS_EXECUTION_UNCERTAIN

    # And a further tap must still refuse.
    relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))
    assert client.routes_called().count("paper_execute") == 1

    text = "\n".join(telegram.texts())
    assert "UNKNOWN" in text
    assert "will not be retried" in text


def test_without_an_operator_key_tap_two_is_a_dry_run(db):
    """The Phase 3 default: the whole flow works, and nothing reaches a broker."""
    client = FakeClient(
        {"paper_approval": {"status": "OK", "route": "paper_approval", "data": APPROVED}},
        operator_key=None,
    )
    telegram = FakeTelegram()
    relay, record = _queued(db, client, telegram)

    outcome = relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))

    assert outcome["outcome"]["status"] == "DRY_RUN"
    assert "paper_execute" not in client.routes_called()
    assert "Dry run" in "\n".join(telegram.texts())


def test_rejecting_holds_the_proposal_and_submits_nothing(db):
    client = FakeClient()
    relay = make_relay(db, client)
    record = seed(db)

    relay.handle_callback(callback(ACTION_REJECT, record["callback_nonce"]))

    assert proposals.load_proposal(db, record["proposal_id"])["status"] == proposals.STATUS_HELD
    assert client.calls == []


# --- text is never an action ---------------------------------------------------------


def test_a_text_message_never_triggers_a_write(db):
    """There is no /execute command, and a message must not become one."""
    client = FakeClient()
    telegram = FakeTelegram()
    relay = make_relay(db, client, telegram)
    seed(db)

    def updates(method, token, payload, *, timeout=None):
        if method == "getUpdates":
            return {
                "ok": True,
                "data": {
                    "result": [
                        {
                            "update_id": 1,
                            "message": {
                                "chat": {"id": CHAT_ID},
                                "from": {"id": OWNER_ID},
                                "text": f"{EXECUTE_PHRASE} 14",
                            },
                        }
                    ]
                },
            }
        return telegram(method, token, payload, timeout=timeout)

    relay._send = updates
    handled = relay.poll_once(timeout=0)

    assert handled == []
    assert client.calls == [], "a text message -- even the exact phrase -- must write nothing"


def test_a_dry_run_is_not_recorded_as_executed(db):
    """The store must not claim an order was submitted when nothing reached a broker.

    Found by running the flow end to end: a dry run left the proposal in EXECUTED, which
    would tell anyone reading the store later that an order went out. A quiet untruth in
    a record is worse than a gap in it.
    """
    client = FakeClient(
        {"paper_approval": {"status": "OK", "route": "paper_approval", "data": APPROVED}},
        operator_key=None,
    )
    relay, record = _queued(db, client, FakeTelegram())

    relay.handle_callback(callback(ACTION_EXECUTE, record["callback_nonce"]))

    stored = proposals.load_proposal(db, record["proposal_id"])
    assert stored["status"] == proposals.STATUS_DRY_RUN
    assert stored["status"] != proposals.STATUS_EXECUTED
    # And it is terminal: a dry run does not leave the proposal re-executable.
    assert stored["status"] in proposals.TERMINAL
