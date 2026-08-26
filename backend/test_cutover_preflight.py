"""Pure-function tests for cutover_preflight's report-building logic.

Network and disk are never touched here -- check_alpaca_status() and
current_configuration()'s *shapes* are captured as plain dicts, exactly the way
test_provision_cash_account.py tests tighten() as a pure function over a
configuration dict rather than hitting the network. table_row_counts() is
reused from backup_database, already covered by test_backup_restore.py.
"""

import cutover_preflight


def test_account_readiness_passes_on_a_healthy_paper_account() -> None:
    status = {
        "environment": "PAPER",
        "paper_account_ready": True,
        "account_status": "ACTIVE",
        "account_suffix": "XY9Z",
    }
    result = cutover_preflight.evaluate_account_readiness(status)
    assert result["ok"] is True, result


def test_account_readiness_fails_closed_on_bad_status() -> None:
    for bad in (
        {
            "environment": "PAPER",
            "paper_account_ready": False,
            "account_status": "ACTIVE",
            "account_suffix": "X",
        },
        {
            "environment": "PAPER",
            "paper_account_ready": True,
            "account_status": "SUBMITTED",
            "account_suffix": "X",
        },
        {
            "environment": None,
            "paper_account_ready": True,
            "account_status": "ACTIVE",
            "account_suffix": "X",
        },
    ):
        result = cutover_preflight.evaluate_account_readiness(bad)
        assert result["ok"] is False, (bad, result)


def test_account_readiness_warns_but_does_not_fail_on_the_known_test_account() -> None:
    status = {
        "environment": "PAPER",
        "paper_account_ready": True,
        "account_status": "ACTIVE",
        "account_suffix": cutover_preflight.KNOWN_TEST_ACCOUNT_SUFFIX,
    }
    result = cutover_preflight.evaluate_account_readiness(status)
    assert result["ok"] is True, result
    assert any("test account" in c["detail"].lower() for c in result["checks"]), result


def test_margin_state_reports_cash_equivalent_correctly() -> None:
    tight = cutover_preflight.evaluate_margin_state(
        {"max_margin_multiplier": "1", "no_shorting": True}
    )
    assert tight["already_cash_equivalent"] is True, tight

    loose = cutover_preflight.evaluate_margin_state(
        {"max_margin_multiplier": "4", "no_shorting": False}
    )
    assert loose["already_cash_equivalent"] is False, loose


def test_ledger_state_refuses_to_guess_with_no_expectation() -> None:
    result = cutover_preflight.evaluate_ledger_state({"paper_positions": 0, "paper_fills": 0}, None)
    assert result["ok"] is False, result
    assert result["decision_required"] is True, result


def test_ledger_state_expect_empty() -> None:
    empty = cutover_preflight.evaluate_ledger_state(
        {"paper_positions": 0, "paper_fills": 0}, "empty"
    )
    assert empty["ok"] is True, empty

    not_empty = cutover_preflight.evaluate_ledger_state(
        {"paper_positions": 1, "paper_fills": 0}, "empty"
    )
    assert not_empty["ok"] is False, not_empty
    assert "paper_positions" in not_empty["detail"], not_empty


def test_ledger_state_expect_seeded_always_ok() -> None:
    result = cutover_preflight.evaluate_ledger_state(
        {"paper_positions": 5, "paper_fills": 12}, "seeded"
    )
    assert result["ok"] is True, result


def test_build_preflight_report_go_only_when_account_and_ledger_ok() -> None:
    status = {
        "environment": "PAPER",
        "paper_account_ready": True,
        "account_status": "ACTIVE",
        "account_suffix": "XY9Z",
    }
    config = {"max_margin_multiplier": "4", "no_shorting": False}
    row_counts = {"paper_positions": 0, "paper_fills": 0}

    go_report = cutover_preflight.build_preflight_report(
        status=status, config=config, row_counts=row_counts, expectation="empty"
    )
    assert go_report["go"] is True, go_report

    no_go_report = cutover_preflight.build_preflight_report(
        status=status, config=config, row_counts=row_counts, expectation=None
    )
    assert no_go_report["go"] is False, no_go_report


def main() -> None:
    test_account_readiness_passes_on_a_healthy_paper_account()
    test_account_readiness_fails_closed_on_bad_status()
    test_account_readiness_warns_but_does_not_fail_on_the_known_test_account()
    test_margin_state_reports_cash_equivalent_correctly()
    test_ledger_state_refuses_to_guess_with_no_expectation()
    test_ledger_state_expect_empty()
    test_ledger_state_expect_seeded_always_ok()
    test_build_preflight_report_go_only_when_account_and_ledger_ok()
    print(
        "PASS: cutover_preflight evaluates account readiness, margin state, and "
        "ledger state as pure functions."
    )
    print("PASS: cutover_preflight refuses to guess the ledger's intended starting state.")


if __name__ == "__main__":
    main()
