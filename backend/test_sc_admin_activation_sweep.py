"""Activating a publication must tell the operator which holdings it affected.

`/portfolio/compliance` already answers "what does the authority say about what
I hold", but it has to be asked. The SC list changes on a schedule -- last Friday
of May and November -- not on a prompt, so the owner would have to remember to
check on exactly the days it matters. Activation is the instant reclassification
becomes real, so the alert belongs there.

Two failure modes get pinned here because they are the ones that would quietly
corrupt the meaning of an activation:

  1. A sweep that RAISES must not fail the activation. By the time the sweep
     runs the activation has committed; presenting a reporting failure as an
     activation failure would send an operator chasing a publication that is
     in fact live.
  2. A sweep that FINDS non-compliant holdings must not fail it either. The
     publication is the authority; flagged holdings are its consequence, not
     an error in it.

Nothing here reaches the network: the sweep is scoped to MY, which is a local
store lookup, and every position seeded is Malaysian.
"""

import argparse
import contextlib
import io
import json
import os
import sqlite3

import auth
import holdings_compliance
import portfolio_store
import sc_admin_cli
import sc_malaysia_store


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _seed_position(conn: sqlite3.Connection, symbol: str, *, quantity: float = 10.0) -> None:
    conn.execute(
        "INSERT INTO paper_positions (symbol, account_suffix, account_type, quantity, "
        "average_cost, cost_basis, realized_pnl, updated_at) "
        "VALUES (?, 'TEST', 'CASH', ?, 5.0, ?, 0.0, '2026-09-21T00:00:00+00:00')",
        (symbol, quantity, quantity * 5.0),
    )
    conn.commit()


def _seed_publication(conn, pub_id: str, *, publication_date: str, statuses: dict) -> None:
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


def _capture(fn, *args, **kwargs) -> str:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        fn(*args, **kwargs)
    return buffer.getvalue()


def test_sweep_reports_a_reclassified_holding():
    conn = _conn()
    _seed_publication(
        conn,
        "sc-sac-my-2026-11-27",
        publication_date="2026-11-27",
        statuses={"1155": "NON_COMPLIANT"},
    )
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-11-27", activated_by="Tester")
    _seed_position(conn, "1155")

    original = sc_malaysia_store.connect_default
    sc_malaysia_store.connect_default = lambda: _KeepOpen(conn)
    try:
        output = _capture(sc_admin_cli._report_holdings_after_activation, conn)
    finally:
        sc_malaysia_store.connect_default = original

    assert "NON-COMPLIANT (1)" in output, output
    assert "1155" in output, output
    assert "sc-sac-my-2026-11-27" in output, (
        "the alert must name the publication that caused the reclassification"
    )
    assert "baitulmal" in output, "the operator needs the disposal ruling surfaced with the alert"
    print("PASS: sweep_reports_a_reclassified_holding")


def test_finding_non_compliant_holdings_is_not_an_activation_failure():
    """A flagged holding is the publication's consequence, not an error in it."""
    conn = _conn()
    _seed_publication(
        conn,
        "sc-sac-my-2026-11-27",
        publication_date="2026-11-27",
        statuses={"1155": "NON_COMPLIANT"},
    )
    _seed_position(conn, "1155")

    original = sc_malaysia_store.connect_default
    sc_malaysia_store.connect_default = lambda: _KeepOpen(conn)
    try:
        result = sc_malaysia_store.activate_publication(
            conn, "sc-sac-my-2026-11-27", activated_by="Tester"
        )
        assert result["status"] == "activated", result
        output = _capture(sc_admin_cli._report_holdings_after_activation, conn)
    finally:
        sc_malaysia_store.connect_default = original

    assert "NON-COMPLIANT (1)" in output, output
    active = sc_malaysia_store.get_active_publication(conn)
    assert active is not None and active["id"] == "sc-sac-my-2026-11-27", (
        "a flagged holding must not have undone or blocked the activation"
    )
    print("PASS: finding_non_compliant_holdings_is_not_an_activation_failure")


def test_sweep_failure_does_not_escape_or_imply_activation_failed():
    conn = _conn()
    original = holdings_compliance.screen_holdings

    def _explode(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    holdings_compliance.screen_holdings = _explode
    try:
        output = _capture(sc_admin_cli._report_holdings_after_activation, conn)
    finally:
        holdings_compliance.screen_holdings = original

    assert "WARNING" in output, output
    assert "OperationalError" in output, output
    assert "SUCCEEDED" in output, (
        "a reporting failure must say plainly that the activation itself committed, or an "
        "operator will chase a publication that is actually live"
    )
    print("PASS: sweep_failure_does_not_escape_or_imply_activation_failed")


def test_unconfirmed_holding_is_not_labelled_a_ruling():
    conn = _conn()
    _seed_publication(
        conn, "sc-sac-my-2026-11-27", publication_date="2026-11-27", statuses={"5225": "COMPLIANT"}
    )
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-11-27", activated_by="Tester")
    _seed_position(conn, "9999")  # absent from the publication entirely

    original = sc_malaysia_store.connect_default
    sc_malaysia_store.connect_default = lambda: _KeepOpen(conn)
    try:
        output = _capture(sc_admin_cli._report_holdings_after_activation, conn)
    finally:
        sc_malaysia_store.connect_default = original

    assert "UNCONFIRMED (1)" in output, output
    assert "NON-COMPLIANT" not in output, (
        "an absent ticker was reported as non-compliant, inventing a divestment duty "
        "no authority imposed"
    )
    assert "not a ruling" in output.lower() or "NOT a ruling" in output, output
    print("PASS: unconfirmed_holding_is_not_labelled_a_ruling")


def test_no_malaysian_positions_reports_cleanly():
    conn = _conn()
    _seed_publication(
        conn, "sc-sac-my-2026-11-27", publication_date="2026-11-27", statuses={"5225": "COMPLIANT"}
    )
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-11-27", activated_by="Tester")

    original = sc_malaysia_store.connect_default
    sc_malaysia_store.connect_default = lambda: _KeepOpen(conn)
    try:
        output = _capture(sc_admin_cli._report_holdings_after_activation, conn)
    finally:
        sc_malaysia_store.connect_default = original

    assert "No open Malaysian positions" in output, output
    print("PASS: no_malaysian_positions_reports_cleanly")


def test_cmd_activate_actually_invokes_the_sweep():
    """The wiring itself, not just the helper.

    Drives cmd_activate end to end with a real authenticated admin actor, and
    asserts the sweep ran. Without this the helper could be perfect and never
    called, which is exactly the gap this phase exists to close.
    """
    conn = _conn()
    _seed_publication(
        conn, "sc-sac-my-2026-11-27", publication_date="2026-11-27", statuses={"1155": "COMPLIANT"}
    )

    saved_users = os.environ.get("SC_ADMIN_AUTH_USERS")
    saved_password = os.environ.get("SC_ADMIN_CLI_PASSWORD")
    os.environ["SC_ADMIN_AUTH_USERS"] = json.dumps(
        [
            {
                "username": "admin_user",
                "password_hash": auth.hash_password("admin_pass", iterations=1000),
                "role": "admin",
            }
        ]
    )
    os.environ["SC_ADMIN_CLI_PASSWORD"] = "admin_pass"

    original_connect = sc_admin_cli._connect
    sc_admin_cli._connect = lambda: conn
    called: list = []
    original_report = sc_admin_cli._report_holdings_after_activation
    sc_admin_cli._report_holdings_after_activation = lambda c: called.append(c)
    try:
        args = argparse.Namespace(
            publication_id="sc-sac-my-2026-11-27", username="admin_user", apply=True
        )
        output = _capture(sc_admin_cli.cmd_activate, args)
    finally:
        sc_admin_cli._connect = original_connect
        sc_admin_cli._report_holdings_after_activation = original_report
        for key, value in (
            ("SC_ADMIN_AUTH_USERS", saved_users),
            ("SC_ADMIN_CLI_PASSWORD", saved_password),
        ):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert "activated" in output, output
    assert len(called) == 1, (
        "cmd_activate did not run the holdings sweep after a successful activation -- "
        "the alert would never fire when a new SC list lands"
    )
    print("PASS: cmd_activate_actually_invokes_the_sweep")


class _KeepOpen:
    """shariah_gate closes the connection it is handed; these fixtures share one
    in-memory connection, so closing it would destroy the database mid-test."""

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def close(self):
        pass


def main():
    test_sweep_reports_a_reclassified_holding()
    test_finding_non_compliant_holdings_is_not_an_activation_failure()
    test_sweep_failure_does_not_escape_or_imply_activation_failed()
    test_unconfirmed_holding_is_not_labelled_a_ruling()
    test_no_malaysian_positions_reports_cleanly()
    test_cmd_activate_actually_invokes_the_sweep()
    print()
    print("All activation-sweep tests passed.")


if __name__ == "__main__":
    main()
