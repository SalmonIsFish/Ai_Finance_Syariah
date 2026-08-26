"""Read-only go/no-go check before running provision_cash_account.py --apply
against the dedicated hackathon account at kickoff.

This never patches the account and never writes to backend/.env or the local
database -- it only reads GET /v2/account, GET /v2/account/configurations (the
same two calls provision_cash_account.py already makes), and the local
paper_trading.db's row counts. The decision to actually run
`provision_cash_account.py --no-shorting --apply` afterwards is still a human
action; this script exists so that decision is made from a clear report instead
of from memory under time pressure.

The ledger question deliberately has three answers, not two: "empty",
"seeded", or "I don't know yet". This script refuses the third one rather than
guessing -- pass --expect-empty-ledger or --expect-seeded-ledger explicitly.
"""

import sqlite3
import sys
from pathlib import Path

from alpaca_paper_adapter import alpaca_credentials, check_alpaca_status
from backup_database import DB_PATH, table_row_counts
from config import load_settings
from provision_cash_account import current_configuration

# The test account CLAUDE.md documents. Cutover means moving away from it, so
# seeing this suffix during a preflight is a signal worth surfacing loudly --
# not a secret, and not a hard failure, since re-running this against the test
# account on purpose (e.g. to sanity-check the script itself) is legitimate.
KNOWN_TEST_ACCOUNT_SUFFIX = "0TCX"

LEDGER_TABLES = ("paper_positions", "paper_fills")


def evaluate_account_readiness(status: dict) -> dict:
    is_known_test_account = status.get("account_suffix") == KNOWN_TEST_ACCOUNT_SUFFIX
    checks = [
        {
            "name": "environment_is_paper",
            "ok": status.get("environment") == "PAPER",
            "detail": f"environment={status.get('environment')!r}",
        },
        {
            "name": "paper_account_ready",
            "ok": status.get("paper_account_ready") is True,
            "detail": f"paper_account_ready={status.get('paper_account_ready')!r}",
        },
        {
            "name": "account_active",
            "ok": status.get("account_status") == "ACTIVE",
            "detail": f"account_status={status.get('account_status')!r}",
        },
        {
            "name": "not_the_known_test_account",
            "ok": not is_known_test_account,
            "detail": (
                f"account_suffix={status.get('account_suffix')!r} matches the documented test "
                f"account ({KNOWN_TEST_ACCOUNT_SUFFIX}) -- confirm this is intentional"
                if is_known_test_account
                else f"account_suffix={status.get('account_suffix')!r}"
            ),
        },
    ]
    # The test-account check is informational, not a hard gate -- everything
    # else must pass for "ok".
    hard_checks = [c for c in checks if c["name"] != "not_the_known_test_account"]
    return {"ok": all(c["ok"] for c in hard_checks), "checks": checks}


def evaluate_margin_state(config: dict) -> dict:
    try:
        multiplier = float(config.get("max_margin_multiplier"))
    except (TypeError, ValueError):
        multiplier = None
    no_shorting = bool(config.get("no_shorting"))
    return {
        "max_margin_multiplier": config.get("max_margin_multiplier"),
        "no_shorting": no_shorting,
        "already_cash_equivalent": multiplier is not None and multiplier <= 1 and no_shorting,
    }


def evaluate_ledger_state(row_counts: dict, expectation: str | None) -> dict:
    relevant = {table: row_counts.get(table, 0) for table in LEDGER_TABLES}
    if expectation is None:
        return {
            "ok": False,
            "decision_required": True,
            "row_counts": relevant,
            "detail": (
                "the local ledger's intended starting state was not specified; pass "
                f"--expect-empty-ledger or --expect-seeded-ledger. Current counts: {relevant}"
            ),
        }
    if expectation == "empty":
        nonzero = {table: count for table, count in relevant.items() if count}
        return {
            "ok": not nonzero,
            "decision_required": False,
            "row_counts": relevant,
            "detail": (
                "ledger is empty, as expected"
                if not nonzero
                else f"expected an empty ledger but found rows in: {nonzero}"
            ),
        }
    if expectation == "seeded":
        return {
            "ok": True,
            "decision_required": False,
            "row_counts": relevant,
            "detail": f"ledger is intentionally seeded: {relevant}",
        }
    raise SystemExit(f"unknown ledger expectation: {expectation!r}")


def build_preflight_report(
    *, status: dict, config: dict, row_counts: dict, expectation: str | None
) -> dict:
    account = evaluate_account_readiness(status)
    margin = evaluate_margin_state(config)
    ledger = evaluate_ledger_state(row_counts, expectation)
    return {
        "go": account["ok"] and ledger["ok"],
        "account": account,
        "margin": margin,
        "ledger": ledger,
    }


def _local_row_counts(db_path: Path) -> dict:
    if not db_path.exists():
        return {table: 0 for table in LEDGER_TABLES}
    connection = sqlite3.connect(db_path)
    try:
        return table_row_counts(connection)
    finally:
        connection.close()


def main() -> None:
    settings = load_settings()
    if settings.alpaca_mode != "paper":
        raise SystemExit("refusing to run outside paper mode")

    expectation = None
    if "--expect-empty-ledger" in sys.argv:
        expectation = "empty"
    elif "--expect-seeded-ledger" in sys.argv:
        expectation = "seeded"

    credentials = alpaca_credentials()
    if credentials is None:
        raise SystemExit("ALPACA_API_KEY_ID / ALPACA_SECRET_KEY are not configured")

    status = check_alpaca_status()
    config = current_configuration(credentials)
    row_counts = _local_row_counts(DB_PATH)

    report = build_preflight_report(
        status=status, config=config, row_counts=row_counts, expectation=expectation
    )

    print(f"local ledger:  {DB_PATH}")
    print()
    print("account:")
    for check in report["account"]["checks"]:
        if check["ok"]:
            mark = "OK  "
        elif check["name"] == "not_the_known_test_account":
            mark = "WARN"
        else:
            mark = "FAIL"
        print(f"  [{mark}] {check['name']}: {check['detail']}")
    print()
    print("margin/multiplier (informational -- provision_cash_account.py --apply tightens this):")
    print(
        f"  max_margin_multiplier={report['margin']['max_margin_multiplier']} "
        f"no_shorting={report['margin']['no_shorting']} "
        f"already_cash_equivalent={report['margin']['already_cash_equivalent']}"
    )
    print()
    print("ledger:")
    print(f"  {report['ledger']['detail']}")
    print()
    print("=" * 60)
    if report["go"]:
        print("GO -- proceed with provision_cash_account.py --apply")
    else:
        print("NO-GO -- resolve the above first")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
