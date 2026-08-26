"""Bundle the ledger, Shariah verdicts, and their EDGAR evidence into one
timestamped Markdown document -- the artifact a judge or scholar would
actually review, not a green checkmark.

Reads three things, all already-persisted and read-only:

1. paper_positions / paper_fills -- the local ledger (portfolio_store.py).
2. shariah_screens -- the append-only verdict log (shariah_screen_store.py),
   read via list_shariah_screens/latest_shariah_screen, never via
   sec_edgar_screen or sec_edgar_cache directly. This module does not import
   either -- test_single_screening_path.py's AST check would fail it if it did,
   and there is no need to: the verdict's persisted payload already carries
   everything the screen computed (company, SIC, and -- for a COMPLIANT/
   NON_COMPLIANT ratio verdict -- total_assets, interest_bearing_debt,
   conventional_cash, and which XBRL concepts were summed to get there). That
   payload *is* the EDGAR evidence a scholar would want to check; there is no
   separate "snapshot" this script needs to go dig out of sec_edgar_cache.py's
   hashed, TTL-expiring, verdict-free response cache.
3. The Known limitations this repo documents about that screen (SIC-code
   business-activity approximation, XBRL's inability to separate Islamic from
   conventional instruments) -- reproduced here verbatim so the caveat travels
   with the evidence instead of living only in CLAUDE.md.

Purely a reporting tool: nothing here decides, screens, or executes anything.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import BACKEND_DIR, REPO_ROOT
from shariah_screen_store import latest_shariah_screen

DB_PATH = BACKEND_DIR / "paper_trading.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "live-trade-evidence" / "audit-exports"

LIMITATIONS_NOTE = (
    "**Screening methodology and its limits (reproduced from CLAUDE.md's Known "
    "limitations, verbatim):** business activity is approximated by SIC code, and "
    "XBRL cannot separate Islamic from conventional instruments, so both the debt "
    "ratio and the cash ratio are overstated. Both approximations err toward "
    "rejection -- a company can be wrongly flagged NON_COMPLIANT by this "
    "conservatism, but not wrongly cleared by it."
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gather_ledger(connection: sqlite3.Connection) -> dict:
    connection.row_factory = sqlite3.Row
    positions = [
        dict(row)
        for row in connection.execute("SELECT * FROM paper_positions ORDER BY id").fetchall()
    ]
    fills = [
        dict(row) for row in connection.execute("SELECT * FROM paper_fills ORDER BY id").fetchall()
    ]
    return {"positions": positions, "fills": fills}


def distinct_symbols(ledger: dict) -> list[str]:
    symbols = {row["symbol"] for row in ledger["positions"]} | {
        row["symbol"] for row in ledger["fills"]
    }
    return sorted(symbols)


def gather_shariah_evidence(connection: sqlite3.Connection, symbols: list[str]) -> list[dict]:
    evidence = []
    for symbol in symbols:
        screen = latest_shariah_screen(connection, symbol)
        if screen is None:
            evidence.append({"symbol": symbol, "status": "NO_SCREEN_ON_RECORD"})
            continue
        payload = screen.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                payload = {}
        evidence.append(
            {
                "symbol": symbol,
                "status": screen.get("status"),
                "screened_at": screen.get("screened_at"),
                "screen": screen.get("screen"),
                "provider": screen.get("provider"),
                "reason": screen.get("reason"),
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return evidence


def _render_ledger_section(ledger: dict) -> str:
    lines = ["## Ledger", "", "### Positions (`paper_positions`)", ""]
    if not ledger["positions"]:
        lines.append("_No open positions recorded._")
    else:
        lines.append(
            "| symbol | account | quantity | avg cost | cost basis | realized P&L | updated |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for row in ledger["positions"]:
            lines.append(
                f"| {row.get('symbol')} | {row.get('account_suffix')} | {row.get('quantity')} | "
                f"{row.get('average_cost')} | {row.get('cost_basis')} | "
                f"{row.get('realized_pnl')} | {row.get('updated_at')} |"
            )
    lines += ["", "### Fills (`paper_fills`)", ""]
    if not ledger["fills"]:
        lines.append("_No fills recorded._")
    else:
        lines.append(
            "| symbol | side | quantity | avg price | notional | filled at | broker order id |"
        )
        lines.append("|---|---|---|---|---|---|---|")
        for row in ledger["fills"]:
            lines.append(
                f"| {row.get('symbol')} | {row.get('side')} | {row.get('quantity')} | "
                f"{row.get('avg_price')} | {row.get('notional')} | {row.get('filled_at')} | "
                f"{row.get('broker_order_id')} |"
            )
    return "\n".join(lines)


def _render_evidence_section(evidence: list[dict]) -> str:
    lines = ["## Shariah Compliance Verdicts and EDGAR Evidence", ""]
    for row in evidence:
        lines.append(f"### {row['symbol']}")
        lines.append("")
        if row["status"] == "NO_SCREEN_ON_RECORD":
            lines.append(
                "- **Status:** NO_SCREEN_ON_RECORD -- no Shariah screen is on record for this "
                "exact symbol. (Expected for an OCC option symbol -- the underlying equity is "
                "what gets screened.)"
            )
            lines.append("")
            continue
        payload = row["payload"]
        lines.append(f"- **Status:** {row['status']}")
        lines.append(f"- **Screened at:** {row['screened_at']}")
        lines.append(f"- **Screen type:** {row['screen']}")
        lines.append(f"- **Provider:** {row['provider']}")
        lines.append(f"- **Company:** {payload.get('company', '—')}")
        lines.append(
            f"- **SIC:** {payload.get('sic', '—')} ({payload.get('sic_description', '—')})"
        )
        lines.append(f"- **Reason:** {row['reason']}")
        ratios = payload.get("ratios")
        if isinstance(ratios, dict):
            lines.append("- **EDGAR ratio evidence (from the filing named below):**")
            lines.append(
                f"  - Filing form: {ratios.get('form', '—')}, "
                f"report date: {payload.get('report_date', '—')}"
            )
            lines.append(f"  - Total assets: {ratios.get('total_assets', '—')}")
            lines.append(
                f"  - Interest-bearing debt: {ratios.get('interest_bearing_debt', '—')} "
                f"({ratios.get('debt_ratio_pct', '—')}% of total assets, "
                f"limit {ratios.get('limit_pct', '—')}%)"
            )
            lines.append(
                f"  - Conventional cash: {ratios.get('conventional_cash', '—')} "
                f"({ratios.get('cash_ratio_pct', '—')}% of total assets)"
            )
            lines.append(f"  - Debt XBRL concepts summed: {ratios.get('debt_concepts_used', [])}")
            lines.append(f"  - Cash XBRL concepts summed: {ratios.get('cash_concepts_used', [])}")
        lines.append("")
    return "\n".join(lines)


def render_markdown(*, generated_at: str, db_path: str, ledger: dict, evidence: list[dict]) -> str:
    parts = [
        "# Amanah Trader Audit Export",
        "",
        f"Generated: {generated_at}",
        f"Source database: `{db_path}`",
        "",
        LIMITATIONS_NOTE,
        "",
        _render_ledger_section(ledger),
        "",
        _render_evidence_section(evidence),
    ]
    return "\n".join(parts) + "\n"


def export_audit(db_path: Path, output_dir: Path, *, now_iso: str | None = None) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"database does not exist: {db_path}")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        ledger = gather_ledger(connection)
        symbols = distinct_symbols(ledger)
        evidence = gather_shariah_evidence(connection, symbols)
    finally:
        connection.close()

    generated_at = now_iso or utc_now_iso()
    markdown = render_markdown(
        generated_at=generated_at, db_path=str(db_path), ledger=ledger, evidence=evidence
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_timestamp = generated_at.replace(":", "").replace("+00:00", "Z")
    out_path = output_dir / f"audit-export-{safe_timestamp}.md"
    out_path.write_text(markdown, encoding="utf-8")

    return {"status": "OK", "output_path": str(out_path), "symbol_count": len(symbols)}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="path to paper_trading.db")
    parser.add_argument(
        "--out-dir", default=str(DEFAULT_OUTPUT_DIR), help="directory to write the export into"
    )
    args = parser.parse_args()

    report = export_audit(Path(args.db), Path(args.out_dir))
    print(f"exported: {report['output_path']}")
    print(f"symbols:  {report['symbol_count']}")


if __name__ == "__main__":
    sys.exit(main() or 0)
