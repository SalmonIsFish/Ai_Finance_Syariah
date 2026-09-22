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

PARSING ON ONE HOST, STAGING ON ANOTHER
---------------------------------------
`--pdf` needs `pdfplumber`, which is not installed on the production host. The
answer is not to install a PDF parser next to the live service. `--export-json`
parses and writes the staged rows to a file, touching no database at all;
`--from-export` inserts that file verbatim, needing no parser:

    # where the PDF and pdfplumber are
    ... --pdf "<path>" --publication-date 2026-05-29 --export-json sc.json
    # on the production host
    ... --from-export sc.json --publication-date 2026-05-29 --apply

That is also the safer order for a reason beyond dependencies: **the parse that
gets reviewed is then byte-for-byte the parse that gets staged.** Re-parsing on
the target host means activating something nobody inspected, and a different
`pdfplumber` version could extract differently with nothing downstream able to
tell. The payload carries `source_document_hash`, so provenance stays the PDF's
rather than the intermediate file's, and `ingest_payload` recomputes nothing --
a `needs_reconciliation` parse stays `needs_reconciliation` and cannot be
laundered into a stageable one by moving it between machines.

`--apply` writes to `backend/paper_trading.db`, which is live. Back it up first,
and consider rehearsing against a copy with `--db`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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


def _report(pub: dict, report: dict, security_count: int) -> None:
    print(f"  publication_id:      {pub['id']}")
    print(f"  human_review_status: {pub['human_review_status']}")
    print(f"  securities:          {security_count}")
    print(f"  official total:      {report['official_stated_total']}")
    print(f"  unique tickers:      {report['unique_ticker_count']}")
    print(f"  parsed records:      {report['parsed_record_count']}")
    print(f"  unresolved discrep.: {report['unresolved_discrepancy']}")


def _admin_username() -> str:
    """The configured admin username, so next-steps commands are runnable as printed.

    This used to print a literal `--username <you>`, and on 2026-09-22 the owner
    copied it verbatim and had to stop and ask what to substitute. A next-steps
    hint that cannot be run as printed is a worse hint than none.

    Usernames are not secrets -- the password is, and is never read here. Fails
    soft to the placeholder: this is a printed suggestion, and an unreadable
    auth config must not take down an ingest that already succeeded.
    """
    try:
        import auth

        admins = [
            name for name, rec in auth._load_configured_users().items() if rec["role"] == "admin"
        ]
        if len(admins) == 1:
            return admins[0]
        if admins:
            return f"<{'|'.join(sorted(admins))}>"
    except Exception:
        pass
    return "<your-admin-username>"


def _next_steps(pub_id: str) -> None:
    user = _admin_username()
    print("Next, as an authenticated admin (this tool cannot do it):")
    print(f"  python backend/sc_admin_cli.py show {pub_id}")
    print(f"  python backend/sc_admin_cli.py securities {pub_id} --ticker 5225")
    print(f"  python backend/sc_admin_cli.py approve {pub_id} --username {user} --apply")
    print(f"  python backend/sc_admin_cli.py activate {pub_id} --username {user} --apply")
    print("  (approve first -- activate refuses an unapproved publication)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", help="path to the official SC PDF. Needs pdfplumber.")
    source.add_argument(
        "--from-export",
        metavar="PATH",
        help="ingest a payload written by --export-json. Needs no PDF parser, so "
        "this is how a parse reviewed on one machine is staged on another.",
    )
    parser.add_argument(
        "--export-json",
        metavar="PATH",
        help="with --pdf: parse and write the payload here, touching no database. "
        "The parse that is reviewed is then the exact one that gets staged.",
    )
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

    if args.export_json and args.from_export:
        print("ERROR: --export-json produces a payload; --from-export consumes one.")
        return 1

    source = Path(args.pdf or args.from_export)
    if not source.exists():
        print(f"ERROR: not found: {source}")
        return 1

    if args.pdf:
        try:
            import pdfplumber  # noqa: F401
        except ImportError:
            print("ERROR: pdfplumber is not installed in this interpreter.")
            print("  pip install pdfplumber")
            print()
            print("Or parse where it IS installed and carry the result:")
            print("  sc_ingest_cli.py --pdf <pdf> --publication-date <date> --export-json p.json")
            print("  scp p.json <this host>:")
            print("  sc_ingest_cli.py --from-export p.json --publication-date <date> --apply")
            return 1

    pub_id = f"sc-sac-my-{args.publication_date}"

    # Parsing writes nothing, so it happens before any database is opened.
    if args.export_json:
        print(f"Parsing {source} ...")
        payload = sc_malaysia_import.build_pdf_payload(
            source,
            publication_date=args.publication_date,
            source_url=args.source_url,
            effective_date=args.effective_date,
        )
        if payload["status"] != "parsed":
            print(f"FAILED: {payload.get('reason')}")
            return 1
        out = Path(args.export_json)
        out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        _report(
            payload["publication"], payload["reconciliation_report"], len(payload["securities"])
        )
        print(f"  table2 conflicts:    {payload['table2_conflicts_skipped'] or 'none'}")
        print()
        print(f"wrote {out}")
        print(f"  sha256: {digest}")
        print("No database was opened. Carry this file to the target host and ingest it with")
        print(f"  --from-export {out.name} --publication-date {args.publication_date} --apply")
        return 0

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
            print(f"  would read:  {source}")
            print(f"  would stage: {pub_id}")
            print("  as:          pending, or needs_reconciliation if the parse is unclean")
            print("               (needs_reconciliation cannot be activated at all)")
            print()
            print("Re-run with --apply to ingest.")
            return 0

        if args.from_export:
            payload = json.loads(source.read_text(encoding="utf-8"))
            staged = payload.get("publication", {}).get("id")
            if staged != pub_id:
                # The date is what names the publication. A mismatch means this
                # payload is not the one being asked for, and staging it anyway
                # would file one publication's securities under another's date.
                print(f"ERROR: payload is for {staged}, not {pub_id}.")
                return 1
            print(f"Ingesting payload {source} ...")
            result = sc_malaysia_import.ingest_payload(conn, payload)
        else:
            print(f"Parsing {source} ...")
            result = sc_malaysia_import.ingest_pdf_publication(
                conn,
                source,
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

    print("ingested:")
    _report(
        {
            "id": result["publication_id"],
            "human_review_status": result["human_review_status"],
        },
        result["reconciliation_report"],
        result["securities_inserted"],
    )
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
