"""Regression test: activating an already-active publication must not kill it.

The live database hit exactly this fault. `sc-sac-my-2026-05-29` was recorded
with `activated_at == deactivated_at` and
`deactivation_reason = "superseded_by:sc-sac-my-2026-05-29"` -- it superseded
itself. `activate_publication` marked every currently-active publication
superseded before activating the target, and the WHERE clause did not exclude
the target, so a second activation of the active publication caught it.

`get_active_publication` requires `deactivated_at IS NULL`, so it then returned
None and every Malaysian ticker resolved UNKNOWN / no_approved_publication --
the entire SC pipeline inert while every individual component still looked
healthy. It fails safe rather than dangerous, which is precisely why it went
unnoticed: nothing non-compliant could trade, because nothing could trade.

Also pins the behaviour that must NOT regress in the other direction: a genuine
second publication still supersedes the first, auditably.
"""

import sqlite3

import sc_malaysia_store


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _seed(conn: sqlite3.Connection, pub_id: str, *, publication_date: str, ticker: str) -> None:
    sc_malaysia_store.insert_publication(
        conn,
        {
            "id": pub_id,
            "publication_date": publication_date,
            "source_document_hash": "testhash",
            "official_record_count": 1,
            "extractable_record_count": 1,
            "parsed_record_count": 1,
            "parser_version": "test-v1",
        },
    )
    sc_malaysia_store.insert_securities(
        conn,
        pub_id,
        [{"ticker": ticker, "issuer_name": "Test Bhd", "shariah_status": "COMPLIANT"}],
    )


def _approve_and_activate(conn: sqlite3.Connection, pub_id: str) -> dict:
    sc_malaysia_store.approve_publication(conn, pub_id, reviewer="Tester")
    return sc_malaysia_store.activate_publication(conn, pub_id, activated_by="Tester")


def test_reactivating_active_publication_does_not_supersede_itself():
    conn = _conn()
    _seed(conn, "sc-sac-my-test", publication_date="2026-01-01", ticker="1155")
    first = _approve_and_activate(conn, "sc-sac-my-test")
    assert first["status"] == "activated", first

    second = sc_malaysia_store.activate_publication(conn, "sc-sac-my-test", activated_by="Tester")
    assert second["status"] == "activated", second

    pub = sc_malaysia_store.get_publication(conn, "sc-sac-my-test")
    assert pub["deactivated_at"] is None, (
        f"publication superseded itself: deactivated_at={pub['deactivated_at']!r} "
        f"reason={pub['deactivation_reason']!r}"
    )
    assert pub["activated_at"] == first["activated_at"], (
        "re-activation rewrote activated_at, destroying the record of when this "
        "publication actually became authoritative"
    )

    active = sc_malaysia_store.get_active_publication(conn)
    assert active is not None, "no active publication after re-activation"
    assert active["id"] == "sc-sac-my-test"

    elig = sc_malaysia_store.check_eligibility(conn, "1155")
    assert elig["status"] == "PASS", elig
    print("PASS: reactivating_active_publication_does_not_supersede_itself")


def test_genuine_second_publication_still_supersedes_first():
    conn = _conn()
    _seed(conn, "sc-sac-my-a", publication_date="2026-01-01", ticker="1155")
    _seed(conn, "sc-sac-my-b", publication_date="2026-02-01", ticker="7084")
    _approve_and_activate(conn, "sc-sac-my-a")
    _approve_and_activate(conn, "sc-sac-my-b")

    old = sc_malaysia_store.get_publication(conn, "sc-sac-my-a")
    assert old["deactivated_at"] is not None
    assert old["deactivation_reason"] == "superseded_by:sc-sac-my-b"

    active = sc_malaysia_store.get_active_publication(conn)
    assert active["id"] == "sc-sac-my-b"
    print("PASS: genuine_second_publication_still_supersedes_first")


def main():
    test_reactivating_active_publication_does_not_supersede_itself()
    test_genuine_second_publication_still_supersedes_first()


if __name__ == "__main__":
    main()
