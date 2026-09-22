"""Stage an SC Malaysia publication into the store, from the official PDF.

`sc_malaysia_import` has had both ingest paths implemented for some time, but no
command-line entry point -- so the only way to populate the SC store was an
ad-hoc script. That is why the live droplet had `sc_publications: 0` on
2026-09-22 and returned `UNKNOWN / no_approved_publication` for every Malaysian
ticker while the owner's laptop had a fully populated store. This closes that gap.

WHY THE PDF AND NOT THE COMMITTED JSON
--------------------------------------
`data/shariah-universe/2026-05-29.json` is committed and would be far easier to
ingest, but it is **not a substitute**:

    committed JSON   688 records, ALL "COMPLIANT", zero NON_COMPLIANT
    official PDF     887 COMPLIANT + 18 NON_COMPLIANT

Ingesting the JSON would silently convert 18 securities the SC explicitly ruled
*ineligible* into "we could not confirm". Trading still blocks either way, since
UNKNOWN fails closed -- but the holdings-compliance path would then report those
holdings as UNCONFIRMED rather than NON_COMPLIANT, and **no divestment duty
would ever be raised** where one genuinely exists. A missing REJECT is not a
safe approximation of a REJECT, and the one-month disposal window makes the
difference consequential rather than cosmetic.

That JSON remains what it is elsewhere in the repo: a migration-compatibility
fixture that can never assert PASS on its own. This tool deliberately ignores it.

WHAT THIS DOES NOT DO
---------------------
Ingestion stages a publication as `pending`, or as `needs_reconciliation` if the
parse does not reconcile cleanly -- and a publication in that state cannot be
activated at all. It never approves and never activates. Making a publication
authoritative stays exactly where it was: `sc_admin_cli.py approve` then
`activate`, both of which require an authenticated admin. That separation is the
point. This tool moves data in; only a human with credentials makes it count.

    python backend/sc_ingest_cli.py --pdf "<path>" --publication-date 2026-05-29
    python backend/sc_ingest_cli.py --pdf "<path>" --publication-date 2026-05-29 --apply

Needs `pdfplumber`, which is NOT installed on the droplet by default.

`--apply` writes to `backend/paper_trading.db`, which is live. Back it up first,
and consider rehearsing against a copy with `--db`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import sc_malaysia_import
import sc_malaysia_store


def _connect(db_path: str | None) -> sqlite3.Connection:
    if db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        conn = sc_malaysia_store.connect_default()
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def _next_steps(pub_id: str) -> None:
    print("Next, as an authenticated admin (this tool cannot do it):")
    print(f"  python backend/sc_admin_cli.py show {pub_id}")
    print(f"  python backend/sc_admin_cli.py securities {pub_id} --ticker 5225")
    print(f"  python backend/sc_admin_cli.py approve {pub_id} --username <you> --apply")
    print(f"  python backend/sc_admin_cli.py activate {pub_id} --username <you> --apply")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", required=True, help="path to the official SC PDF")
    parser.add_argument(
        "--publication-date",
        required=True,
        help="release date, e.g. 2026-05-29. Given explicitly, never inferred from "
        "the PDF: its 'As at' text is the list's as-of date, not the release date.",
    )
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--effective-date", default=None, help="defaults to --publication-date")
    parser.add_argument(
        "--db",
        default=None,
        help="write to this SQLite file instead of backend/paper_trading.db. "
        "Use a copy to rehearse before touching the live database.",
    )
    parser.add_argument(
        "--apply", action="store_true", help="actually write (default is a dry run)"
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}")
        return 1

    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("ERROR: pdfplumber is not installed in this interpreter.")
        print("  pip install pdfplumber")
        return 1

    pub_id = f"sc-sac-my-{args.publication_date}"

    conn = _connect(args.db)
    try:
        existing = sc_malaysia_store.get_publication(conn, pub_id)
        if existing:
            print(f"{pub_id} already exists in this database:")
            print(f"  human_review_status: {existing['human_review_status']}")
            print(f"  activated_at:        {existing['activated_at']}")
            print()
            print("Publications are immutable. Nothing to ingest.")
            if not existing["activated_at"]:
                _next_steps(pub_id)
            return 0

        if not args.apply:
            print(f"DRY RUN -- nothing written. Database: {args.db or 'backend/paper_trading.db'}")
            print(f"  would parse: {pdf_path}")
            print(f"  would stage: {pub_id}")
            print("  as:          pending, or needs_reconciliation if the parse is unclean")
            print("               (needs_reconciliation cannot be activated at all)")
            print()
            print("Re-run with --apply to ingest.")
            return 0

        print(f"Parsing {pdf_path} ...")
        result = sc_malaysia_import.ingest_pdf_publication(
            conn,
            pdf_path,
            publication_date=args.publication_date,
            source_url=args.source_url,
            effective_date=args.effective_date,
        )
        if result["status"] == "ingested":
            conn.commit()
    finally:
        conn.close()

    if result["status"] != "ingested":
        print(f"FAILED: {result.get('reason')}")
        for key, value in sorted(result.items()):
            if key not in ("status", "reason"):
                print(f"  {key}: {value}")
        return 1

    report = result["reconciliation_report"]
    print("ingested:")
    print(f"  publication_id:      {result['publication_id']}")
    print(f"  human_review_status: {result['human_review_status']}")
    print(f"  securities_inserted: {result['securities_inserted']}")
    print(f"  official total:      {report['official_stated_total']}")
    print(f"  unique tickers:      {report['unique_ticker_count']}")
    print(f"  parsed records:      {report['parsed_record_count']}")
    print(f"  unresolved discrep.: {report['unresolved_discrepancy']}")
    if result["reconciliation_notes"]:
        print(f"  notes: {result['reconciliation_notes']}")
    if result["table2_conflicts_skipped"]:
        # A ticker in both the compliant main list and Table 2's reclassification
        # list is a contradiction in the source document, not something to
        # resolve by picking one. It is skipped and named so a human decides.
        print(f"  SOURCE CONTRADICTION, skipped: {result['table2_conflicts_skipped']}")

    print()
    print("Staged only. It is NOT authoritative and screens nothing yet.")
    _next_steps(pub_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
