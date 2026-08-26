"""Prove the audit export actually surfaces the evidence, not just that it runs.

Everything here is against a scratch SQLite file seeded with sample rows --
never the real paper_trading.db. The assertions check for the presence of
specific numbers (a debt ratio, a row count) in the rendered Markdown, because
a judge reading the export needs those numbers verbatim, not a summary that
dropped them.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import audit_export


def seed_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE paper_positions (id INTEGER PRIMARY KEY, symbol TEXT, "
            "account_suffix TEXT, quantity REAL, average_cost REAL, cost_basis REAL, "
            "realized_pnl REAL, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO paper_positions VALUES "
            "(1, 'CVX', '0TCX', 1.0, 206.89, 206.89, 0.0, '2026-08-19T00:00:00+00:00')"
        )
        connection.execute(
            "CREATE TABLE paper_fills (id INTEGER PRIMARY KEY, queue_id INTEGER, "
            "broker_order_id TEXT, symbol TEXT, side TEXT, quantity REAL, avg_price REAL, "
            "notional REAL, filled_at TEXT, account_suffix TEXT, account_type TEXT, payload TEXT)"
        )
        connection.execute(
            "INSERT INTO paper_fills VALUES "
            "(1, 11, 'order-1', 'AAPL260828P00305000', 'SELL', 1.0, 1.02, 102.0, "
            "'2026-08-20T00:00:00+00:00', '0TCX', 'CASH', '{}')"
        )
        connection.execute(
            """
            CREATE TABLE shariah_screens (
                id INTEGER PRIMARY KEY AUTOINCREMENT, screened_at TEXT NOT NULL,
                symbol TEXT NOT NULL, status TEXT NOT NULL, screen TEXT, report_date TEXT,
                debt_ratio_pct REAL, cash_ratio_pct REAL, limit_pct REAL, provider TEXT,
                reason TEXT, previous_status TEXT, payload TEXT NOT NULL
            )
            """
        )
        verdict = {
            "symbol": "CVX",
            "status": "COMPLIANT",
            "screen": "financial_ratios",
            "provider": "SEC_EDGAR",
            "company": "Chevron Corp",
            "sic": "2911",
            "sic_description": "Petroleum Refining",
            "report_date": "2025-12-31",
            "reason": "debt 12.3% and cash 4.1% of total assets, both under 33%",
            "ratios": {
                "total_assets": 250000000000.0,
                "interest_bearing_debt": 30750000000.0,
                "conventional_cash": 10250000000.0,
                "debt_ratio_pct": 12.3,
                "cash_ratio_pct": 4.1,
                "limit_pct": 33.0,
                "debt_concepts_used": ["LongTermDebt"],
                "cash_concepts_used": ["CashAndCashEquivalentsAtCarryingValue"],
                "form": "10-K",
            },
        }
        connection.execute(
            "INSERT INTO shariah_screens (screened_at, symbol, status, screen, report_date, "
            "debt_ratio_pct, cash_ratio_pct, limit_pct, provider, reason, previous_status, "
            "payload) VALUES ('2026-08-19T00:00:00+00:00', 'CVX', 'COMPLIANT', "
            "'financial_ratios', '2025-12-31', 12.3, 4.1, 33.0, 'SEC_EDGAR', ?, NULL, ?)",
            (verdict["reason"], json.dumps(verdict)),
        )
        connection.commit()
    finally:
        connection.close()


def test_gather_ledger_and_distinct_symbols() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        seed_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            ledger = audit_export.gather_ledger(connection)
            symbols = audit_export.distinct_symbols(ledger)
        finally:
            connection.close()

        assert len(ledger["positions"]) == 1, ledger
        assert ledger["positions"][0]["symbol"] == "CVX", ledger
        assert len(ledger["fills"]) == 1, ledger
        assert symbols == ["AAPL260828P00305000", "CVX"], symbols


def test_gather_shariah_evidence_includes_edgar_ratios() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        seed_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            evidence = audit_export.gather_shariah_evidence(
                connection, ["CVX", "AAPL260828P00305000"]
            )
        finally:
            connection.close()

        by_symbol = {row["symbol"]: row for row in evidence}
        assert by_symbol["CVX"]["status"] == "COMPLIANT", by_symbol
        assert by_symbol["CVX"]["payload"]["ratios"]["debt_ratio_pct"] == 12.3, by_symbol
        assert by_symbol["AAPL260828P00305000"]["status"] == "NO_SCREEN_ON_RECORD", by_symbol


def test_render_markdown_contains_the_actual_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        seed_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            ledger = audit_export.gather_ledger(connection)
            symbols = audit_export.distinct_symbols(ledger)
            evidence = audit_export.gather_shariah_evidence(connection, symbols)
        finally:
            connection.close()

        markdown = audit_export.render_markdown(
            generated_at="2026-08-26T12:00:00+00:00",
            db_path=str(db_path),
            ledger=ledger,
            evidence=evidence,
        )

        # the numbers a scholar needs must appear verbatim, not just a status
        assert "CVX" in markdown
        assert "COMPLIANT" in markdown
        assert "12.3" in markdown  # debt_ratio_pct
        assert "4.1" in markdown  # cash_ratio_pct
        assert "Chevron Corp" in markdown
        assert "10-K" in markdown  # the filing form the ratio was computed from
        assert "AAPL260828P00305000" in markdown
        assert "NO_SCREEN_ON_RECORD" in markdown
        # the known-limitations caveat must travel with the evidence, not be left implicit
        assert "SIC code" in markdown
        assert "XBRL" in markdown


def test_export_audit_writes_a_timestamped_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        output_dir = Path(tmp) / "exports"
        seed_db(db_path)

        report = audit_export.export_audit(db_path, output_dir, now_iso="2026-08-26T12:00:00+00:00")
        assert report["status"] == "OK", report
        assert report["symbol_count"] == 2, report

        out_path = Path(report["output_path"])
        assert out_path.exists(), report
        content = out_path.read_text(encoding="utf-8")
        assert "Chevron Corp" in content


def main() -> None:
    test_gather_ledger_and_distinct_symbols()
    test_gather_shariah_evidence_includes_edgar_ratios()
    test_render_markdown_contains_the_actual_evidence()
    test_export_audit_writes_a_timestamped_file()
    print(
        "PASS: audit_export gathers the ledger, the latest verdict per symbol, and the "
        "EDGAR ratio evidence."
    )
    print(
        "PASS: audit_export's rendered Markdown carries the actual numbers and the "
        "documented caveats, and writes a timestamped file."
    )


if __name__ == "__main__":
    main()
