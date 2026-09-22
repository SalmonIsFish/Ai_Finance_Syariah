"""Verify the append-only Shariah screening audit log records what it claims to.

No network and no real database: every case runs against an in-memory SQLite
connection, the same convention test_portfolio_store.py and test_watchlist_store.py
use. The verdict dicts below are the exact shapes sec_edgar_screen.check_us_symbol
returns from each of its six exits -- the point of the suite is that a row survives
whichever exit produced it, including the early ones that carry no ratios at all.
"""

import json
import sqlite3

from shariah_screen_store import (
    ensure_shariah_screen_tables,
    latest_screen_per_symbol,
    latest_shariah_screen,
    list_shariah_screens,
    record_shariah_screen,
)


def make_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def compliant_verdict(symbol="AAPL", debt=8.5, cash=12.25):
    return {
        "symbol": symbol,
        "exchange": "Nasdaq",
        "provider": "SEC_EDGAR",
        "methodology": "SC_MY_SAC",
        "company": "Apple Inc.",
        "status": "COMPLIANT",
        "report_date": "2025-09-27",
        "screen": "financial_ratios",
        "ratios": {
            "report_date": "2025-09-27",
            "form": "10-K",
            "debt_ratio_pct": debt,
            "cash_ratio_pct": cash,
            "limit_pct": 33.0,
            "debt_concepts_used": ["LongTermDebtNoncurrent"],
            "cash_concepts_used": ["CashAndCashEquivalentsAtCarryingValue"],
        },
        "reason": f"debt {debt}% and cash {cash}% of total assets, both under 33%",
    }


def excluded_verdict(symbol="JPM"):
    """Tier 1. A business-activity rejection computes no ratios at all."""
    return {
        "symbol": symbol,
        "exchange": "NYSE",
        "provider": "SEC_EDGAR",
        "methodology": "SC_MY_SAC",
        "company": "JPMorgan Chase & Co.",
        "status": "NON_COMPLIANT",
        "report_date": None,
        "screen": "business_activity",
        "reason": "excluded business activity: conventional banking (SIC 6021)",
    }


def main() -> None:
    # ------------------------------------------------------------ tier 2 row
    connection = make_connection()
    recorded = record_shariah_screen(connection, compliant_verdict())
    assert recorded["id"] == 1, recorded
    assert recorded["status"] == "COMPLIANT", recorded
    assert recorded["symbol"] == "AAPL", recorded
    # Nothing came before it, so there is no change to report -- "changed" means
    # the verdict moved, not that this is the first time we looked.
    assert recorded["previous_status"] is None, recorded
    assert recorded["changed"] is False, recorded

    row = connection.execute("SELECT * FROM shariah_screens WHERE id = 1").fetchone()
    assert row["debt_ratio_pct"] == 8.5, dict(row)
    assert row["cash_ratio_pct"] == 12.25, dict(row)
    assert row["limit_pct"] == 33.0, dict(row)
    assert row["screen"] == "financial_ratios", dict(row)
    assert row["report_date"] == "2025-09-27", dict(row)
    assert row["provider"] == "SEC_EDGAR", dict(row)
    # The whole verdict survives, including the concepts each ratio was built
    # from -- that evidence is the difference between an audit log and a tally.
    payload = json.loads(row["payload"])
    assert payload["ratios"]["debt_concepts_used"] == ["LongTermDebtNoncurrent"], payload

    # ------------------------------------------------- tier 1 row, no ratios
    excluded = record_shariah_screen(connection, excluded_verdict())
    assert excluded["status"] == "NON_COMPLIANT", excluded
    bank_row = connection.execute("SELECT * FROM shariah_screens WHERE id = 2").fetchone()
    assert bank_row["screen"] == "business_activity", dict(bank_row)
    assert bank_row["debt_ratio_pct"] is None, dict(bank_row)
    assert bank_row["cash_ratio_pct"] is None, dict(bank_row)
    assert bank_row["limit_pct"] is None, dict(bank_row)
    assert bank_row["reason"].startswith("excluded business activity"), dict(bank_row)

    # ------------------------------------------- early exits still get a row
    # UNKNOWN and ERROR return before a company profile is fetched, so they carry
    # no provider. They are still recorded: "we could not tell" on a given day is
    # a fact about the screen worth keeping, and the reason distinguishes them.
    unknown = record_shariah_screen(
        connection,
        {"status": "UNKNOWN", "symbol": "ZZZZ", "reason": "ticker_not_found_in_sec_registry"},
    )
    error = record_shariah_screen(
        connection,
        {"status": "ERROR", "symbol": "YYYY", "reason": "http_503"},
    )
    unknown_row = connection.execute(
        "SELECT * FROM shariah_screens WHERE id = ?", (unknown["id"],)
    ).fetchone()
    error_row = connection.execute(
        "SELECT * FROM shariah_screens WHERE id = ?", (error["id"],)
    ).fetchone()
    assert unknown_row["provider"] is None, dict(unknown_row)
    assert unknown_row["screen"] is None, dict(unknown_row)
    assert unknown_row["reason"] == "ticker_not_found_in_sec_registry", dict(unknown_row)
    assert error_row["status"] == "ERROR", dict(error_row)

    # --------------------------------------------------- append, not replace
    append_connection = make_connection()
    record_shariah_screen(append_connection, compliant_verdict(debt=8.5))
    record_shariah_screen(append_connection, compliant_verdict(debt=9.75))
    count = append_connection.execute(
        "SELECT COUNT(*) FROM shariah_screens WHERE symbol = 'AAPL'"
    ).fetchone()[0]
    assert count == 2, count
    # Newest wins for "what do we believe now", and the older row is still there
    # for "what did we believe then". Overwrite-in-place would lose the second.
    assert latest_shariah_screen(append_connection, "AAPL")["debt_ratio_pct"] == 9.75
    assert latest_shariah_screen(append_connection, "aapl")["debt_ratio_pct"] == 9.75
    assert latest_shariah_screen(append_connection, "NOPE") is None

    # ------------------------------------------------------ change detection
    flip_connection = make_connection()
    record_shariah_screen(flip_connection, compliant_verdict(symbol="TSLA"))
    flipped = record_shariah_screen(
        flip_connection, {**compliant_verdict(symbol="TSLA"), "status": "NON_COMPLIANT"}
    )
    assert flipped["previous_status"] == "COMPLIANT", flipped
    assert flipped["changed"] is True, flipped
    # A repeated identical verdict is recorded but is not a change -- otherwise a
    # daily sweep would raise an alert every single day.
    unchanged = record_shariah_screen(
        flip_connection, {**compliant_verdict(symbol="TSLA"), "status": "NON_COMPLIANT"}
    )
    assert unchanged["previous_status"] == "NON_COMPLIANT", unchanged
    assert unchanged["changed"] is False, unchanged
    # Change is per symbol, not global: a different ticker screened in between
    # must not become the previous verdict for TSLA.
    record_shariah_screen(flip_connection, compliant_verdict(symbol="MSFT"))
    after_other = record_shariah_screen(
        flip_connection, {**compliant_verdict(symbol="TSLA"), "status": "COMPLIANT"}
    )
    assert after_other["previous_status"] == "NON_COMPLIANT", after_other
    assert after_other["changed"] is True, after_other

    # ----------------------------------------------------------------- reads
    listed = list_shariah_screens(flip_connection)
    assert len(listed) == 5, listed
    # Newest first, asserted on ids rather than symbols -- the symbols happen to
    # match at both ends of this fixture, so a reversed sort would slip past a
    # check on those alone.
    assert [item["id"] for item in listed] == [5, 4, 3, 2, 1], listed
    assert listed[0]["status"] == "COMPLIANT", listed[0]
    only_tsla = list_shariah_screens(flip_connection, symbol="tsla")
    assert len(only_tsla) == 4, only_tsla
    assert {item["symbol"] for item in only_tsla} == {"TSLA"}, only_tsla
    assert [item["id"] for item in only_tsla] == [5, 3, 2, 1], only_tsla
    assert isinstance(only_tsla[0]["payload"], dict), only_tsla[0]
    # A limit keeps the newest rows, not the oldest.
    assert [item["id"] for item in list_shariah_screens(flip_connection, limit=2)] == [5, 4]
    # limit is clamped rather than trusted, the same way portfolio_store does it.
    assert len(list_shariah_screens(flip_connection, limit=0)) == 1
    assert len(list_shariah_screens(flip_connection, limit=-5)) == 1

    # ------------------------------------------------ malformed input is kept
    # A ratio that cannot be read must not cost us the row recording the verdict
    # it belongs to; the column goes null and the payload keeps the raw value.
    messy_connection = make_connection()
    messy = record_shariah_screen(
        messy_connection,
        {
            "symbol": "weird",
            "status": "COMPLIANT",
            "ratios": {"debt_ratio_pct": "not-a-number", "cash_ratio_pct": None},
        },
    )
    messy_row = messy_connection.execute(
        "SELECT * FROM shariah_screens WHERE id = ?", (messy["id"],)
    ).fetchone()
    assert messy_row["symbol"] == "WEIRD", dict(messy_row)
    assert messy_row["debt_ratio_pct"] is None, dict(messy_row)
    assert json.loads(messy_row["payload"])["ratios"]["debt_ratio_pct"] == "not-a-number"

    # ------------------------------------------------------------ idempotent
    # ensure_* is called at the top of every public function, so a second call on
    # a populated table is a no-op rather than a reset.
    ensure_shariah_screen_tables(messy_connection)
    assert messy_connection.execute("SELECT COUNT(*) FROM shariah_screens").fetchone()[0] == 1

    print("PASS: shariah screening verdicts append with their ratios and detect change.")
    test_latest_screen_per_symbol_is_a_roster_not_a_log()


def test_latest_screen_per_symbol_is_a_roster_not_a_log():
    """One current row per symbol -- and crucially, no symbol left out.

    The failure this guards is specific and was observed in production on
    2026-09-22: the log held 14,213 rows across 16 symbols, AAPL alone 1,301 of
    them. Anything reading the log's newest N rows to build a list of companies
    sees the same few symbols repeatedly and silently omits the rest. A UI built
    on that would under-report which companies had been screened, while looking
    entirely plausible.

    So the load below is deliberately lopsided: one symbol screened many times,
    another screened once and long ago. A naive `ORDER BY id DESC LIMIT n` drops
    the quiet one, which is exactly the bug.
    """
    connection = make_connection()

    record_shariah_screen(connection, compliant_verdict(symbol="QUIET"))
    for _ in range(12):
        record_shariah_screen(connection, compliant_verdict(symbol="NOISY", debt=8.5))
    # NOISY's verdict changes on its final screen. The roster must show the new
    # one -- a stale verdict is worse than no verdict, since it reads as current.
    record_shariah_screen(
        connection,
        {
            "symbol": "NOISY",
            "status": "NON_COMPLIANT",
            "reason": "debt 41.0% over 33%",
            "ratios": {"debt_ratio_pct": 41.0, "cash_ratio_pct": 3.0, "limit_pct": 33.0},
        },
    )
    # A third symbol whose verdict is UNKNOWN, to prove the three states survive
    # a roster read. Collapsing UNKNOWN into NON_COMPLIANT here would invent a
    # divestment duty the screen never asserted.
    record_shariah_screen(connection, {"symbol": "MURKY", "status": "UNKNOWN"})

    roster = latest_screen_per_symbol(connection)

    assert [row["symbol"] for row in roster] == ["MURKY", "NOISY", "QUIET"], roster
    by_symbol = {row["symbol"]: row for row in roster}
    assert by_symbol["NOISY"]["status"] == "NON_COMPLIANT", "roster served a stale verdict"
    assert by_symbol["NOISY"]["debt_ratio_pct"] == 41.0
    assert by_symbol["QUIET"]["status"] == "COMPLIANT", "the quiet symbol was dropped"
    assert by_symbol["MURKY"]["status"] == "UNKNOWN", "UNKNOWN must survive as UNKNOWN"

    # The log itself is untouched: the roster is a view, not a compaction.
    assert connection.execute("SELECT COUNT(*) FROM shariah_screens").fetchone()[0] == 15

    # Same row shape as the log, so one renderer can serve both.
    assert set(roster[0]) == set(list_shariah_screens(connection, limit=1)[0])
    assert isinstance(by_symbol["NOISY"]["payload"], dict), "payload must arrive parsed"

    print("PASS: latest_screen_per_symbol returns one current row per symbol, omitting none.")


if __name__ == "__main__":
    main()
