"""Tests for re-screening held positions and the purification ledger.

The scenario that matters is the one this system previously could not see at
all: a security that was COMPLIANT when bought, reclassified NON_COMPLIANT by a
later SC publication, still sitting in the portfolio. test_reclassification_
across_real_publications drives that through the genuine SC store rather than a
stub, so it would catch the store and the sweep disagreeing.

No test reaches the network: Malaysian screening is a local store lookup, and
every US-shaped case swaps the `evaluate` seam.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import holdings_compliance
import portfolio_store
import sc_malaysia_store


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _seed_position(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    quantity: float = 10.0,
    average_cost: float = 5.0,
    account_suffix: str = "TEST",
) -> None:
    conn.execute(
        "INSERT INTO paper_positions (symbol, account_suffix, account_type, quantity, "
        "average_cost, cost_basis, realized_pnl, updated_at) "
        "VALUES (?, ?, 'CASH', ?, ?, ?, 0.0, '2026-09-21T00:00:00+00:00')",
        (symbol, account_suffix, quantity, average_cost, quantity * average_cost),
    )
    conn.commit()


def _stub(verdicts: dict):
    """Seam replacement returning a canned evaluate_shariah shape per symbol."""

    def _evaluate(symbol: str) -> dict:
        return {
            "agent": "shariah",
            "market": "US",
            "provider": "STUB",
            "symbol": symbol,
            **verdicts[symbol],
        }

    return _evaluate


def _seed_publication(
    conn: sqlite3.Connection, pub_id: str, *, publication_date: str, statuses: dict
) -> None:
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": pub_id,
            "publication_date": publication_date,
            "source_document_hash": f"hash-{pub_id}",
            "official_record_count": len(statuses),
            "extractable_record_count": len(statuses),
            "parsed_record_count": len(statuses),
            "parser_version": "test-v1",
        },
    )
    sc_malaysia_store.insert_securities(
        conn,
        pub_id,
        [
            {"ticker": t, "issuer_name": f"Issuer {t}", "shariah_status": s}
            for t, s in statuses.items()
        ],
    )
    sc_malaysia_store.approve_publication(conn, pub_id, reviewer="Tester")
    sc_malaysia_store.activate_publication(conn, pub_id, activated_by="Tester")


def test_compliant_holding_raises_no_alert():
    conn = _conn()
    _seed_position(conn, "AAPL")
    result = holdings_compliance.screen_holdings(
        conn, evaluate=_stub({"AAPL": {"status": "PASS", "reason": "compliant"}})
    )
    assert result["position_count"] == 1
    assert result["flagged_count"] == 0
    assert result["holdings"][0]["alert"] is None
    print("PASS: compliant_holding_raises_no_alert")


def test_non_compliant_holding_is_flagged():
    conn = _conn()
    _seed_position(conn, "BANK")
    result = holdings_compliance.screen_holdings(
        conn,
        evaluate=_stub({"BANK": {"status": "REJECT", "reason": "authoritative_non_compliant"}}),
    )
    assert result["flagged_count"] == 1
    assert result["non_compliant_count"] == 1
    assert result["flagged"][0]["alert"] == holdings_compliance.ALERT_NON_COMPLIANT
    print("PASS: non_compliant_holding_is_flagged")


def test_unknown_is_not_collapsed_into_non_compliant():
    """The gate's central promise, enforced on the holdings sweep too."""
    conn = _conn()
    _seed_position(conn, "MYST")
    result = holdings_compliance.screen_holdings(
        conn, evaluate=_stub({"MYST": {"status": "UNKNOWN", "reason": "screen_unavailable"}})
    )
    assert result["flagged_count"] == 1
    assert result["unconfirmed_count"] == 1
    assert result["non_compliant_count"] == 0, (
        "an unconfirmed holding was reported as non-compliant, which would raise a "
        "divestment duty the authority never imposed"
    )
    assert result["flagged"][0]["alert"] == holdings_compliance.ALERT_UNCONFIRMED
    print("PASS: unknown_is_not_collapsed_into_non_compliant")


def test_unrecognised_status_fails_closed():
    conn = _conn()
    _seed_position(conn, "WEIRD")
    result = holdings_compliance.screen_holdings(
        conn, evaluate=_stub({"WEIRD": {"status": "DEFINITELY_FINE", "reason": "made up"}})
    )
    assert result["holdings"][0]["status"] == "UNKNOWN"
    assert result["flagged_count"] == 1
    print("PASS: unrecognised_status_fails_closed")


def test_closed_position_is_not_screened():
    conn = _conn()
    _seed_position(conn, "SOLD", quantity=0.0)
    result = holdings_compliance.screen_holdings(conn, evaluate=_stub({}))
    assert result["position_count"] == 0
    print("PASS: closed_position_is_not_screened")


def test_reclassification_across_real_publications():
    """The scenario this module exists for, through the real SC store.

    1155 is COMPLIANT in the May publication and bought on that basis. The
    November publication reclassifies it NON_COMPLIANT. Nothing about the
    position changes -- only the authority does.
    """
    from agents.shariah_agent import evaluate_shariah

    conn = _conn()
    original_db_path = sc_malaysia_store.DB_PATH
    original_connect = sc_malaysia_store.connect_default

    class _KeepOpen:
        """shariah_gate closes the connection it is handed. This fixture's
        connection is in-memory and shared across the whole test, so closing it
        would destroy the database mid-test."""

        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

        def close(self):
            pass

    sc_malaysia_store.connect_default = lambda: _KeepOpen(conn)
    try:
        _seed_publication(
            conn,
            "sc-sac-my-2026-05-29",
            publication_date="2026-05-29",
            statuses={"1155": "COMPLIANT", "5225": "COMPLIANT"},
        )
        _seed_position(conn, "1155")

        before = holdings_compliance.screen_holdings(conn, evaluate=evaluate_shariah)
        assert before["flagged_count"] == 0, before
        assert before["holdings"][0]["status"] == "PASS"
        assert before["holdings"][0]["publication_id"] == "sc-sac-my-2026-05-29"

        _seed_publication(
            conn,
            "sc-sac-my-2026-11-27",
            publication_date="2026-11-27",
            statuses={"1155": "NON_COMPLIANT", "5225": "COMPLIANT"},
        )

        after = holdings_compliance.screen_holdings(conn, evaluate=evaluate_shariah)
        assert after["non_compliant_count"] == 1, after
        flagged = after["flagged"][0]
        assert flagged["symbol"] == "1155"
        assert flagged["alert"] == holdings_compliance.ALERT_NON_COMPLIANT
        assert flagged["publication_id"] == "sc-sac-my-2026-11-27", (
            "the alert must name the publication that caused the reclassification"
        )
        assert flagged["market"] == "MY"
    finally:
        sc_malaysia_store.connect_default = original_connect
        sc_malaysia_store.DB_PATH = original_db_path
    print("PASS: reclassification_across_real_publications")


def _spy_evaluate(called: list):
    def _evaluate(symbol: str) -> dict:
        called.append(symbol)
        return {
            "agent": "shariah",
            "market": "?",
            "provider": "STUB",
            "symbol": symbol,
            "status": "PASS",
            "reason": "compliant",
        }

    return _evaluate


def test_market_filter_does_not_evaluate_the_excluded_market():
    """Filtering must stop the evaluator being *called*, not just drop its result.

    For a US holding that call goes through sec_edgar_screen -- a live SEC fetch
    of up to ~4.7 MB. An SC activation cannot have reclassified a US holding, so
    reacting to one must not touch the network.
    """
    conn = _conn()
    _seed_position(conn, "1155")  # all digits -> MY
    _seed_position(conn, "AAPL")  # -> US
    called: list = []

    result = holdings_compliance.screen_holdings(conn, evaluate=_spy_evaluate(called), market="MY")

    assert result["market_filter"] == "MY"
    assert result["position_count"] == 1
    assert result["holdings"][0]["symbol"] == "1155"
    assert called == ["1155"], (
        f"the evaluator ran for an excluded market: {called} -- for a US symbol that is a "
        f"live SEC EDGAR fetch fired by an SC publication that cannot affect it"
    )
    print("PASS: market_filter_does_not_evaluate_the_excluded_market")


def test_no_market_filter_screens_every_holding():
    conn = _conn()
    _seed_position(conn, "1155")
    _seed_position(conn, "AAPL")
    called: list = []

    result = holdings_compliance.screen_holdings(conn, evaluate=_spy_evaluate(called))

    assert result["market_filter"] is None
    assert result["position_count"] == 2
    assert sorted(called) == ["1155", "AAPL"]
    print("PASS: no_market_filter_screens_every_holding")


def test_purification_due_donates_only_the_gain():
    assert holdings_compliance.purification_due(cost_basis=1000.0, proceeds=1250.0) == 250.0
    print("PASS: purification_due_donates_only_the_gain")


def test_purification_due_is_zero_at_a_loss():
    assert holdings_compliance.purification_due(cost_basis=1000.0, proceeds=800.0) == 0.0
    assert holdings_compliance.purification_due(cost_basis=1000.0, proceeds=1000.0) == 0.0
    print("PASS: purification_due_is_zero_at_a_loss")


def test_purification_due_rejects_negative_inputs():
    for kwargs in ({"cost_basis": -1.0, "proceeds": 10.0}, {"cost_basis": 10.0, "proceeds": -1.0}):
        try:
            holdings_compliance.purification_due(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"negative input silently accepted: {kwargs}")
    print("PASS: purification_due_rejects_negative_inputs")


def test_purification_ledger_excludes_unconfirmed_holdings():
    conn = _conn()
    _seed_position(conn, "BANK", quantity=10.0, average_cost=5.0)
    _seed_position(conn, "MYST", quantity=10.0, average_cost=5.0)
    screening = holdings_compliance.screen_holdings(
        conn,
        evaluate=_stub(
            {
                "BANK": {"status": "REJECT", "reason": "authoritative_non_compliant"},
                "MYST": {"status": "UNKNOWN", "reason": "screen_unavailable"},
            }
        ),
    )
    ledger = holdings_compliance.purification_ledger(screening, price_lookup=lambda s: 8.0)
    assert [e["symbol"] for e in ledger["entries"]] == ["BANK"], (
        "an unconfirmed holding created a purification obligation no authority imposed"
    )
    # 10 @ 8.00 = 80.00 proceeds against 50.00 cost -> 30.00 owed.
    assert ledger["total_purification_due"] == 30.0
    print("PASS: purification_ledger_excludes_unconfirmed_holdings")


def test_purification_ledger_reports_unpriced_rather_than_assuming_zero():
    conn = _conn()
    _seed_position(conn, "BANK")
    screening = holdings_compliance.screen_holdings(
        conn,
        evaluate=_stub({"BANK": {"status": "REJECT", "reason": "authoritative_non_compliant"}}),
    )
    ledger = holdings_compliance.purification_ledger(screening, price_lookup=lambda s: None)
    assert ledger["unpriced"] == ["BANK"]
    assert ledger["complete"] is False, (
        "an unpriced non-compliant holding was reported as a complete ledger, "
        "understating what is owed"
    )
    assert ledger["total_purification_due"] == 0.0
    print("PASS: purification_ledger_reports_unpriced_rather_than_assuming_zero")


def _sweep(conn, verdicts):
    screening = holdings_compliance.screen_holdings(conn, evaluate=_stub(verdicts))
    return holdings_compliance.apply_disposal_clock(conn, screening)


REJECT_VERDICT = {
    "status": "REJECT",
    "reason": "authoritative_non_compliant",
    "publication_id": "sc-sac-my-2026-05-29",
}


def test_the_clock_starts_and_persists_across_sweeps():
    """The first-flagged date must survive, or no deadline can ever be measured.

    Before this, screen_holdings stamped `screened_at` on the response and
    nothing on the holding, so every sweep looked like the first one: a position
    flagged a year ago rendered identically to one flagged this morning.
    """
    conn = _conn()
    _seed_position(conn, "0026")

    first = _sweep(conn, {"0026": REJECT_VERDICT})
    flagged = first["flagged"][0]
    assert flagged["first_flagged_at"], flagged
    assert flagged["disposal_deadline"], flagged
    assert flagged["days_remaining"] is not None

    original_flagged_at = flagged["first_flagged_at"]

    # A later sweep must not restart the clock -- that would hand back a fresh
    # month every time anyone opened the page, which is the permissive direction.
    second = _sweep(conn, {"0026": REJECT_VERDICT})
    assert second["flagged"][0]["first_flagged_at"] == original_flagged_at
    print("PASS: the disposal clock starts once and survives later sweeps")


def test_the_deadline_counts_from_the_publication_not_our_sweep():
    """The SC's month runs from its ruling, not from when we happened to look.

    Taking our own observation as the start silently extends a religious
    deadline by however long the software was not running, always in the
    permissive direction. A publication dated 40 days ago is already overdue.
    """
    conn = _conn()
    _seed_position(conn, "0026")
    long_ago = (datetime.now(timezone.utc) - timedelta(days=40)).date().isoformat()

    screening = _sweep(
        conn,
        {"0026": {**REJECT_VERDICT, "publication_date": long_ago}},
    )
    flagged = screening["flagged"][0]

    assert flagged["deadline_basis"] == "publication_date", flagged
    assert flagged["days_remaining"] < 0, (
        f"a 40-day-old ruling must already be overdue, got {flagged['days_remaining']}"
    )
    assert flagged["overdue"] is True
    assert screening["overdue_count"] == 1
    print("PASS: the deadline counts from the publication date, not from our sweep")


def test_only_a_ruling_starts_a_clock():
    """UNCONFIRMED is not a finding of ineligibility, so it owes no disposal.

    Attaching a countdown to it would invent an obligation the authority never
    stated -- the same conflation the three-state gate exists to prevent.
    """
    conn = _conn()
    _seed_position(conn, "9999")
    screening = _sweep(conn, {"9999": {"status": "UNKNOWN", "reason": "not_in_publication"}})

    flagged = screening["flagged"][0]
    assert flagged["alert"] == holdings_compliance.ALERT_UNCONFIRMED
    assert flagged["disposal_deadline"] is None, flagged
    assert flagged["days_remaining"] is None, flagged
    # It is still tracked -- it needs attention, just not a divestment clock.
    assert flagged["first_flagged_at"]
    print("PASS: an unconfirmed holding is tracked but owes no disposal deadline")


def test_a_cleared_flag_starts_a_fresh_month_if_it_returns():
    """Sold, re-bought and reclassified again owes a new month, not a remainder."""
    conn = _conn()
    _seed_position(conn, "0026")

    first = _sweep(conn, {"0026": REJECT_VERDICT})
    first_flagged_at = first["flagged"][0]["first_flagged_at"]

    # Reinstated by a later publication: the alert clears.
    cleared = _sweep(conn, {"0026": {"status": "PASS", "reason": "authoritative_compliant"}})
    assert cleared["flagged"] == []
    row = conn.execute(
        "SELECT cleared_at FROM holdings_compliance_flags WHERE symbol = '0026'"
    ).fetchone()
    assert row[0] is not None, "an alert that no longer applies must be cleared, not left open"

    # Reclassified again later.
    again = _sweep(conn, {"0026": REJECT_VERDICT})
    assert again["flagged"][0]["first_flagged_at"] != first_flagged_at, (
        "a re-raised alert must start a new clock, not resume the old one"
    )
    print("PASS: a cleared alert that returns starts a fresh disposal month")


def main():
    test_compliant_holding_raises_no_alert()
    test_non_compliant_holding_is_flagged()
    test_unknown_is_not_collapsed_into_non_compliant()
    test_unrecognised_status_fails_closed()
    test_closed_position_is_not_screened()
    test_reclassification_across_real_publications()
    test_market_filter_does_not_evaluate_the_excluded_market()
    test_no_market_filter_screens_every_holding()
    test_purification_due_donates_only_the_gain()
    test_purification_due_is_zero_at_a_loss()
    test_purification_due_rejects_negative_inputs()
    test_purification_ledger_excludes_unconfirmed_holdings()
    test_purification_ledger_reports_unpriced_rather_than_assuming_zero()
    test_the_clock_starts_and_persists_across_sweeps()
    test_the_deadline_counts_from_the_publication_not_our_sweep()
    test_only_a_ruling_starts_a_clock()
    test_a_cleared_flag_starts_a_fresh_month_if_it_returns()
    print()
    print("All holdings-compliance tests passed.")


if __name__ == "__main__":
    main()
