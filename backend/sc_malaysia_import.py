"""Ingest a SC Malaysia universe JSON or the official PDF into the sc_publications store.

The JSON path must follow the schema defined in the Obsidian vault's
08-Data-Governance/shariah-universe/schema.json. Either path ingests a
publication that starts as 'pending' (or 'needs_reconciliation' if the parsed
data doesn't cleanly reconcile) and requires explicit human approval before
the Shariah gate can query it. Neither path ever activates a publication.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import sc_malaysia_store
import sc_pdf_parser


PARSER_VERSION = "json-import-v1"


def _compute_file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ingest_universe_json(
    connection: sqlite3.Connection,
    json_path: str | Path,
    *,
    official_record_count: int | None = None,
    extractable_record_count: int | None = None,
    human_review_status: str = "pending",
) -> dict:
    """Parse a SC Malaysia universe JSON and insert into the store.

    Returns a summary including the publication ID and record counts.
    If the parsed count does not match the official count, the publication
    is marked 'needs_reconciliation' instead of 'pending'.
    """
    path = Path(json_path)
    if not path.exists():
        return {"status": "error", "reason": "file_not_found", "path": str(path)}

    raw = path.read_text(encoding="utf-8-sig")
    dataset = json.loads(raw)

    pub_id = dataset.get("dataset_id")
    if not pub_id:
        return {"status": "error", "reason": "missing_dataset_id"}

    records = dataset.get("records", [])
    parsed_count = len(records)

    source = dataset.get("source", {})

    effective_status = human_review_status
    reconciliation_notes = None

    if official_record_count and parsed_count < official_record_count:
        effective_status = "needs_reconciliation"
        reconciliation_notes = (
            f"Parsed {parsed_count} records but official count is "
            f"{official_record_count}. Gap of {official_record_count - parsed_count} "
            f"records. Do not activate until reconciled."
        )

    pub = {
        "id": pub_id,
        "publication_date": dataset.get("publication_date", ""),
        "effective_date": dataset.get("publication_date"),
        "source_url": source.get("url"),
        "source_document_hash": _compute_file_hash(path),
        "official_record_count": official_record_count,
        "extractable_record_count": extractable_record_count,
        "parsed_record_count": parsed_count,
        "parser_version": PARSER_VERSION,
        "human_review_status": effective_status,
        "human_review_notes": reconciliation_notes,
    }

    try:
        sc_malaysia_store.insert_publication(connection, pub)
    except sqlite3.IntegrityError:
        return {
            "status": "error",
            "reason": "publication_already_exists",
            "publication_id": pub_id,
        }

    sc_malaysia_store.insert_securities(connection, pub_id, records)

    return {
        "status": "ingested",
        "publication_id": pub_id,
        "publication_date": pub["publication_date"],
        "parsed_record_count": parsed_count,
        "official_record_count": official_record_count,
        "human_review_status": effective_status,
        "reconciliation_notes": reconciliation_notes,
    }


def ingest_pdf_publication(
    connection: sqlite3.Connection,
    pdf_path: str | Path,
    *,
    publication_date: str,
    source_url: str | None = None,
    effective_date: str | None = None,
    human_review_status: str = "pending",
) -> dict:
    """Parse the official SC Malaysia PDF (sc_pdf_parser) and stage it as a publication.

    ``publication_date`` must be supplied explicitly (e.g. "2026-05-29") rather
    than inferred from the PDF, since the PDF's own "As at 21 May 2026" text is
    the securities list's as-of date, not necessarily the publication's release
    date -- conflating the two would be exactly the kind of metadata guess this
    system must not make.

    The publication is marked 'needs_reconciliation' instead of the requested
    ``human_review_status`` whenever sc_pdf_parser.reconcile() reports any
    unresolved discrepancy, duplicate, invalid record, or numbering anomaly --
    a publication in that state cannot be activated (sc_malaysia_store enforces
    this). A clean reconciliation still only reaches 'pending': it makes the
    publication ready for human review, never approved or active on its own.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return {"status": "error", "reason": "file_not_found", "path": str(pdf_path)}

    result = sc_pdf_parser.parse_pdf(pdf_path)
    report = sc_pdf_parser.reconcile(result)

    pub_id = f"sc-sac-my-{publication_date}"

    clean = (
        report["unresolved_discrepancy"] in (0, None)
        and report["duplicate_record_count"] == 0
        and report["invalid_record_count"] == 0
        and report["numbering_anomaly_count"] == 0
    )
    effective_status = human_review_status if clean else "needs_reconciliation"
    reconciliation_notes = None
    if not clean:
        reconciliation_notes = (
            f"Reconciliation issues: unresolved_discrepancy={report['unresolved_discrepancy']}, "
            f"duplicates={report['duplicate_record_count']}, "
            f"invalid={report['invalid_record_count']}, "
            f"numbering_anomalies={report['numbering_anomaly_count']}. "
            "Do not activate until reconciled."
        )

    pub = {
        "id": pub_id,
        "publication_date": publication_date,
        "effective_date": effective_date or publication_date,
        "source_url": source_url,
        "source_document_hash": result.source_document_hash,
        "official_record_count": report["official_stated_total"],
        "extractable_record_count": report["unique_ticker_count"],
        "parsed_record_count": report["parsed_record_count"],
        "parser_version": result.parser_version,
        "human_review_status": effective_status,
        "human_review_notes": reconciliation_notes,
    }

    try:
        sc_malaysia_store.insert_publication(connection, pub)
    except sqlite3.IntegrityError:
        return {"status": "error", "reason": "publication_already_exists", "publication_id": pub_id}

    seen_tickers: set[str] = set()
    securities: list[dict] = []
    for r in result.main_list:
        securities.append(
            {
                "ticker": r.ticker,
                "issuer_name": r.issuer_name,
                "shariah_status": "COMPLIANT",
                "board": r.market,
                "sector": r.sector,
            }
        )
        seen_tickers.add(r.ticker)
    for r in result.additional_instruments:
        if r.ticker in seen_tickers:
            continue
        securities.append(
            {
                "ticker": r.ticker,
                "issuer_name": r.issuer_name,
                "shariah_status": "COMPLIANT",
                "board": "OTHER_INSTRUMENT",
                "sector": None,
            }
        )
        seen_tickers.add(r.ticker)
    skipped_conflicts = []
    for r in result.table2_newly_non_compliant:
        # Table 2 is an explicit authoritative SC statement of reclassification
        # to non-compliant -- not an absence-based inference -- so REJECT is
        # warranted here. A ticker also present in the current compliant main
        # list would be a genuine source contradiction; such a ticker is
        # skipped and reported rather than silently overwritten either way.
        if r.ticker in seen_tickers:
            skipped_conflicts.append(r.ticker)
            continue
        securities.append(
            {
                "ticker": r.ticker,
                "issuer_name": r.issuer_name,
                "shariah_status": "NON_COMPLIANT",
                "board": None,
                "sector": None,
            }
        )
        seen_tickers.add(r.ticker)

    sc_malaysia_store.insert_securities(connection, pub_id, securities)

    return {
        "status": "ingested",
        "publication_id": pub_id,
        "publication_date": publication_date,
        "human_review_status": effective_status,
        "reconciliation_notes": reconciliation_notes,
        "reconciliation_report": report,
        "securities_inserted": len(securities),
        "table2_conflicts_skipped": skipped_conflicts,
    }
