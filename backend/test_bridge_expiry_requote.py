"""A proposal expires rather than resting on a price that has moved.

CLAUDE.md records queue 10 dying `BROKER_CANCELLED` because an option bid was 1.05 at
submission and 1.00 under two minutes later, with the instruction: "Re-quote and submit as
close together as possible -- an option bid is perishable in a way an equity bid is not."

These TTLs are that lesson written down. The assertion on the constant is deliberate: it
fails if someone widens the window without reading why it is narrow.
"""

import pytest
from bridge import proposals
from bridge.proposals import (
    EQUITY_PENDING_TTL_SECONDS,
    EQUITY_QUEUE_TTL_SECONDS,
    OPTION_PENDING_TTL_SECONDS,
    OPTION_QUEUE_TTL_SECONDS,
)

EQUITY_PREVIEW = {
    "status": "READY_FOR_APPROVAL",
    "symbol": "AAPL",
    "side": "BUY",
    "quantity": 1,
    "price": 207.60,
    "asset_class": "equity",
}

OPTION_PREVIEW = dict(EQUITY_PREVIEW, asset_class="option", symbol="AAPL260828P00305000")

APPROVED = {"queue_id": 21, "approval": {"status": "APPROVED_PAPER_READY"}}


@pytest.fixture
def db():
    connection = proposals.connect(":memory:")
    yield connection
    connection.close()


def test_an_option_window_is_shorter_than_the_failure_that_taught_it():
    """Queue 10's bid went stale in under two minutes. The window must stay inside that."""
    assert OPTION_PENDING_TTL_SECONDS < 120, (
        "an option bid went from 1.05 to 1.00 in under two minutes on a real order "
        "(CLAUDE.md, queue 10); widening this past that window reintroduces the failure"
    )
    assert OPTION_QUEUE_TTL_SECONDS < OPTION_PENDING_TTL_SECONDS
    assert OPTION_PENDING_TTL_SECONDS < EQUITY_PENDING_TTL_SECONDS, (
        "an option bid is perishable in a way an equity bid is not"
    )
    assert OPTION_QUEUE_TTL_SECONDS < EQUITY_QUEUE_TTL_SECONDS


def test_the_tap_two_window_is_shorter_than_the_tap_one_window():
    """Once queued the price is already older, so the second window must be tighter."""
    assert EQUITY_QUEUE_TTL_SECONDS < EQUITY_PENDING_TTL_SECONDS
    assert OPTION_QUEUE_TTL_SECONDS < OPTION_PENDING_TTL_SECONDS


def test_ttls_are_chosen_by_asset_class():
    assert proposals.ttls_for("option") == (OPTION_PENDING_TTL_SECONDS, OPTION_QUEUE_TTL_SECONDS)
    assert proposals.ttls_for("equity") == (EQUITY_PENDING_TTL_SECONDS, EQUITY_QUEUE_TTL_SECONDS)
    # Anything unrecognised gets the equity window rather than the longest one available.
    assert proposals.ttls_for("") == (EQUITY_PENDING_TTL_SECONDS, EQUITY_QUEUE_TTL_SECONDS)
    assert proposals.ttls_for(None) == (EQUITY_PENDING_TTL_SECONDS, EQUITY_QUEUE_TTL_SECONDS)


def test_a_pending_proposal_expires_at_its_deadline(db):
    record = proposals.create_proposal(db, preview=OPTION_PREVIEW, chat_id=1, market="US", now=0.0)
    assert record["expires_at"] == OPTION_PENDING_TTL_SECONDS

    assert proposals.expire_due(db, now=OPTION_PENDING_TTL_SECONDS - 1) == []
    moved = proposals.expire_due(db, now=OPTION_PENDING_TTL_SECONDS)

    assert moved == [record["proposal_id"]]
    stored = proposals.load_proposal(db, record["proposal_id"])
    assert stored["status"] == proposals.STATUS_EXPIRED
    assert "requote" in stored["status_reason"]


def test_a_queued_proposal_expires_on_the_shorter_clock(db):
    record = proposals.create_proposal(db, preview=OPTION_PREVIEW, chat_id=1, market="US", now=0.0)
    proposals.mark_queued(db, record["proposal_id"], approval=APPROVED, now=0.0)

    stored = proposals.load_proposal(db, record["proposal_id"])
    assert stored["queue_expires_at"] == OPTION_QUEUE_TTL_SECONDS

    proposals.expire_due(db, now=OPTION_QUEUE_TTL_SECONDS)
    stored = proposals.load_proposal(db, record["proposal_id"])
    assert stored["status"] == proposals.STATUS_QUEUE_EXPIRED


def test_expiry_does_not_touch_a_terminal_proposal(db):
    record = proposals.create_proposal(db, preview=EQUITY_PREVIEW, chat_id=1, market="US", now=0.0)
    proposals.hold_proposal(db, record["proposal_id"], reason="rejected_by_owner")

    proposals.expire_due(db, now=10_000.0)

    assert proposals.load_proposal(db, record["proposal_id"])["status"] == proposals.STATUS_HELD


def test_an_expired_proposal_cannot_be_approved(db):
    record = proposals.create_proposal(db, preview=EQUITY_PREVIEW, chat_id=1, market="US", now=0.0)
    proposals.expire_due(db, now=EQUITY_PENDING_TTL_SECONDS)

    outcome = proposals.mark_queued(db, record["proposal_id"], approval=APPROVED)

    assert outcome["status"] == "REJECT"
    assert outcome["reason"] == "not_pending_expired"


def test_a_requote_is_a_new_proposal_not_a_revived_one(db):
    """The whole point is a fresh quote. Reusing the old preview would defeat it."""
    first = proposals.create_proposal(db, preview=EQUITY_PREVIEW, chat_id=1, market="US", now=0.0)
    proposals.expire_due(db, now=EQUITY_PENDING_TTL_SECONDS)

    requoted_preview = dict(EQUITY_PREVIEW, price=209.10, preview_id="second")
    second = proposals.create_proposal(
        db, preview=requoted_preview, chat_id=1, market="US", now=1000.0
    )

    assert second["proposal_id"] != first["proposal_id"]
    assert second["callback_nonce"] != first["callback_nonce"]
    assert second["preview"]["price"] != first["preview"]["price"]
    assert second["status"] == proposals.STATUS_PENDING
    # The old one stays expired; nothing revives it.
    assert proposals.load_proposal(db, first["proposal_id"])["status"] == proposals.STATUS_EXPIRED


def test_open_proposals_lists_only_actionable_ones(db):
    live = proposals.create_proposal(db, preview=EQUITY_PREVIEW, chat_id=1, market="US", now=0.0)
    dead = proposals.create_proposal(db, preview=EQUITY_PREVIEW, chat_id=1, market="US", now=0.0)
    proposals.hold_proposal(db, dead["proposal_id"], reason="rejected_by_owner")

    ids = [record["proposal_id"] for record in proposals.open_proposals(db)]

    assert ids == [live["proposal_id"]]


def test_each_proposal_gets_its_own_unguessable_nonce(db):
    nonces = {
        proposals.create_proposal(db, preview=EQUITY_PREVIEW, chat_id=1, market="US")[
            "callback_nonce"
        ]
        for _ in range(25)
    }
    assert len(nonces) == 25
    for nonce in nonces:
        # Must fit Telegram's 64-byte callback_data limit alongside the action prefix.
        assert len(f"a1:{nonce}".encode()) <= 64
        assert len(nonce) >= 12
