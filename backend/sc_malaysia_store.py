"""Immutable historical SC Malaysia publication store.

Each SC/SAC publication is an immutable snapshot. Once ingested and approved,
its records never change. The Shariah gate queries only approved, activated
publications to determine trading eligibility.

Three eligibility outcomes:
  PASS    — approved publication confirms COMPLIANT
  REJECT  — authoritative source confirms not eligible
  UNKNOWN — no approved publication contains this ticker

UNKNOWN fails closed for trading but is preserved in evidence.
"""

import sqlite3
from datetime import datetime, timezone

from config import BACKEND_DIR


DB_PATH = BACKEND_DIR / "paper_trading.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_sc_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sc_publications (
            id TEXT PRIMARY KEY,
            publication_date TEXT NOT NULL,
            effective_date TEXT,
            source_url TEXT,
            source_document_hash TEXT,
            official_record_count INTEGER,
            extractable_record_count INTEGER,
            parsed_record_count INTEGER,
            parser_version TEXT,
            ingestion_timestamp TEXT NOT NULL,
            human_review_status TEXT NOT NULL DEFAULT 'pending',
            human_review_notes TEXT,
            activated_at TEXT,
            deactivated_at TEXT,
            UNIQUE(publication_date)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sc_security_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publication_id TEXT NOT NULL REFERENCES sc_publications(id),
            ticker TEXT NOT NULL,
            issuer_name TEXT,
            shariah_status TEXT NOT NULL,
            board TEXT,
            sector TEXT,
            UNIQUE(publication_id, ticker)
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_sc_security_ticker ON sc_security_status(ticker)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_sc_security_pub ON sc_security_status(publication_id)"
    )
    # Audit fields for the human approval/activation workflow. As of Phase 2A
    # (auth.py), the value stored here is always an authenticated actor's
    # username -- sc_admin_cli.py's mutating commands require
    # auth.authenticate_credentials()+authorize() to succeed before calling
    # any of approve_publication/reject_publication/activate_publication/
    # deactivate_publication, and always pass the resulting Actor.username,
    # never a free-typed string. These columns remain plain TEXT rather than
    # a foreign key into a users table because there is still no user table
    # -- SC_ADMIN_AUTH_USERS is a static, env-configured credential list, not
    # a database-backed identity system; that remains a Phase 2+ concern if
    # this ever needs more than a handful of named reviewers/admins.
    existing_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(sc_publications)").fetchall()
    }
    migrations = {
        "approved_at": "ALTER TABLE sc_publications ADD COLUMN approved_at TEXT",
        "approved_by": "ALTER TABLE sc_publications ADD COLUMN approved_by TEXT",
        "rejected_at": "ALTER TABLE sc_publications ADD COLUMN rejected_at TEXT",
        "rejected_by": "ALTER TABLE sc_publications ADD COLUMN rejected_by TEXT",
        "activated_by": "ALTER TABLE sc_publications ADD COLUMN activated_by TEXT",
        "deactivated_by": "ALTER TABLE sc_publications ADD COLUMN deactivated_by TEXT",
        "deactivation_reason": "ALTER TABLE sc_publications ADD COLUMN deactivation_reason TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            connection.execute(statement)
    connection.commit()


def get_publication(connection: sqlite3.Connection, publication_id: str) -> dict | None:
    ensure_sc_tables(connection)
    row = connection.execute(
        "SELECT * FROM sc_publications WHERE id = ?", (publication_id,)
    ).fetchone()
    return dict(row) if row else None


def list_publications(connection: sqlite3.Connection) -> list[dict]:
    ensure_sc_tables(connection)
    rows = connection.execute(
        "SELECT * FROM sc_publications ORDER BY publication_date DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_active_publication(connection: sqlite3.Connection) -> dict | None:
    ensure_sc_tables(connection)
    row = connection.execute(
        "SELECT * FROM sc_publications "
        "WHERE human_review_status = 'approved' "
        "AND activated_at IS NOT NULL "
        "AND deactivated_at IS NULL "
        "ORDER BY publication_date DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def insert_publication(connection: sqlite3.Connection, pub: dict) -> dict:
    """Insert a new publication record. Status starts as 'pending'."""
    ensure_sc_tables(connection)
    pub_id = pub["id"]
    now = utc_now()
    connection.execute(
        """
        INSERT INTO sc_publications (
            id, publication_date, effective_date, source_url,
            source_document_hash, official_record_count,
            extractable_record_count, parsed_record_count,
            parser_version, ingestion_timestamp, human_review_status,
            human_review_notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            pub_id,
            pub["publication_date"],
            pub.get("effective_date"),
            pub.get("source_url"),
            pub.get("source_document_hash"),
            pub.get("official_record_count"),
            pub.get("extractable_record_count"),
            pub.get("parsed_record_count"),
            pub.get("parser_version"),
            now,
            pub.get("human_review_status", "pending"),
            pub.get("human_review_notes"),
        ),
    )
    connection.commit()
    return {"id": pub_id, "ingestion_timestamp": now, "status": "inserted"}


def insert_securities(
    connection: sqlite3.Connection,
    publication_id: str,
    securities: list[dict],
) -> dict:
    """Bulk-insert security records for a publication.

    A ticker duplicated within the input is skipped (first occurrence wins)
    rather than raising: a 'needs_reconciliation' publication is exactly the
    case where the parsed data has known flaws, and the point of staging it is
    for a human to see what the parser actually found -- a crash on the
    duplicate that should have been what flagged it for reconciliation in the
    first place would defeat that. Skipped tickers are reported, never silent.
    """
    ensure_sc_tables(connection)
    inserted = 0
    skipped_duplicate_tickers: list[str] = []
    for sec in securities:
        try:
            connection.execute(
                """
                INSERT INTO sc_security_status
                    (publication_id, ticker, issuer_name, shariah_status, board, sector)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    publication_id,
                    sec["ticker"],
                    sec.get("issuer_name"),
                    sec.get("shariah_status", "COMPLIANT"),
                    sec.get("board"),
                    sec.get("sector"),
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped_duplicate_tickers.append(sec["ticker"])
    connection.commit()
    return {
        "publication_id": publication_id,
        "inserted": inserted,
        "skipped_duplicate_tickers": skipped_duplicate_tickers,
    }


def approve_publication(
    connection: sqlite3.Connection,
    publication_id: str,
    *,
    notes: str | None = None,
    reviewer: str | None = None,
) -> dict:
    """Mark a publication as approved. Does NOT activate it yet.

    Refuses a publication currently flagged 'needs_reconciliation' -- an
    incomplete or inconsistent extraction must be fixed by re-ingesting a
    corrected publication, never waved through by approving the known-bad
    one. This is what makes 'needs_reconciliation' actually block activation
    rather than being one UPDATE away from 'approved'.

    `reviewer` is a free-text identifier (a name typed at the CLI), not an
    authenticated identity -- this repo has no user/auth system, and this
    field does not pretend otherwise. It exists so the audit trail records
    who claimed the review, pending real authentication being added before
    this operation is ever exposed over HTTP.
    """
    ensure_sc_tables(connection)
    pub = get_publication(connection, publication_id)
    if not pub:
        return {"status": "error", "reason": "publication_not_found"}
    if pub["human_review_status"] == "approved":
        return {"status": "already_approved"}
    if pub["human_review_status"] == "rejected":
        return {"status": "error", "reason": "publication_rejected"}
    if pub["human_review_status"] == "needs_reconciliation":
        return {
            "status": "error",
            "reason": "needs_reconciliation",
            "publication_id": publication_id,
            "human_review_notes": pub.get("human_review_notes"),
        }
    now = utc_now()
    connection.execute(
        "UPDATE sc_publications SET human_review_status = 'approved', "
        "human_review_notes = ?, approved_at = ?, approved_by = ? WHERE id = ?",
        (notes, now, reviewer, publication_id),
    )
    connection.commit()
    return {
        "status": "approved",
        "publication_id": publication_id,
        "approved_at": now,
        "approved_by": reviewer,
    }


def reject_publication(
    connection: sqlite3.Connection,
    publication_id: str,
    *,
    reason: str,
    reviewer: str | None = None,
) -> dict:
    """Mark a publication as rejected by human review -- a reviewer found a
    problem the automated reconciliation did not catch. A rejected
    publication can never be approved or activated; ingest a corrected
    publication instead. `reviewer` is recorded as `rejected_by` -- see
    ensure_sc_tables' comment on these columns being authenticated actor
    identities as of Phase 2A, not free-typed text.
    """
    ensure_sc_tables(connection)
    pub = get_publication(connection, publication_id)
    if not pub:
        return {"status": "error", "reason": "publication_not_found"}
    if pub["human_review_status"] == "approved":
        return {"status": "error", "reason": "already_approved_cannot_reject"}
    now = utc_now()
    connection.execute(
        "UPDATE sc_publications SET human_review_status = 'rejected', "
        "human_review_notes = ?, rejected_at = ?, rejected_by = ? WHERE id = ?",
        (reason, now, reviewer, publication_id),
    )
    connection.commit()
    return {
        "status": "rejected",
        "publication_id": publication_id,
        "rejected_at": now,
        "rejected_by": reviewer,
    }


def activate_publication(
    connection: sqlite3.Connection,
    publication_id: str,
    *,
    activated_by: str | None = None,
) -> dict:
    """Activate an approved publication for trading. Deactivates any prior
    active one. Enforces every activation-safety condition explicitly and
    returns a machine-readable `reason` the moment any one of them fails --
    activation never partially proceeds.
    """
    ensure_sc_tables(connection)
    pub = get_publication(connection, publication_id)
    if not pub:
        return {"status": "error", "reason": "publication_not_found"}
    if pub["human_review_status"] == "rejected":
        return {"status": "error", "reason": "publication_rejected"}
    if pub["human_review_status"] == "needs_reconciliation":
        return {"status": "error", "reason": "needs_reconciliation"}
    if pub["human_review_status"] != "approved":
        return {
            "status": "error",
            "reason": "publication_not_approved",
            "current_status": pub["human_review_status"],
        }
    if pub.get("deactivated_at"):
        return {"status": "error", "reason": "publication_already_superseded"}
    securities = publication_securities(connection, publication_id)
    if not securities:
        return {"status": "error", "reason": "no_security_records"}
    official = pub.get("official_record_count")
    extractable = pub.get("extractable_record_count")
    parsed = pub.get("parsed_record_count")
    if (
        official is None
        or extractable is None
        or parsed is None
        or not (official == extractable == parsed)
    ):
        return {
            "status": "error",
            "reason": "record_counts_do_not_reconcile",
            "official_record_count": official,
            "extractable_record_count": extractable,
            "parsed_record_count": parsed,
        }
    if not pub.get("source_document_hash"):
        return {"status": "error", "reason": "source_document_hash_missing"}
    if not pub.get("parser_version"):
        return {"status": "error", "reason": "parser_version_missing"}

    now = utc_now()
    connection.execute(
        "UPDATE sc_publications SET deactivated_at = ?, deactivation_reason = ? "
        "WHERE activated_at IS NOT NULL AND deactivated_at IS NULL",
        (now, f"superseded_by:{publication_id}"),
    )
    connection.execute(
        "UPDATE sc_publications SET activated_at = ?, activated_by = ? WHERE id = ?",
        (now, activated_by, publication_id),
    )
    connection.commit()
    return {
        "status": "activated",
        "publication_id": publication_id,
        "activated_at": now,
        "activated_by": activated_by,
    }


def deactivate_publication(
    connection: sqlite3.Connection,
    publication_id: str,
    *,
    reason: str,
    deactivated_by: str | None = None,
) -> dict:
    """Deactivate a publication without requiring a replacement to take over
    -- e.g. a problem is found in an already-active publication and trading
    must fall back to UNKNOWN (fail closed) rather than continue trusting it.

    Never deletes the publication or its securities: deactivation is a
    status change, and the row (with its approval/activation history) stays
    queryable forever, same as any other publication.
    """
    ensure_sc_tables(connection)
    pub = get_publication(connection, publication_id)
    if not pub:
        return {"status": "error", "reason": "publication_not_found"}
    if not pub.get("activated_at") or pub.get("deactivated_at"):
        return {"status": "error", "reason": "publication_not_currently_active"}
    now = utc_now()
    connection.execute(
        "UPDATE sc_publications SET deactivated_at = ?, deactivation_reason = ?, "
        "deactivated_by = ? WHERE id = ?",
        (now, reason, deactivated_by, publication_id),
    )
    connection.commit()
    return {
        "status": "deactivated",
        "publication_id": publication_id,
        "deactivated_at": now,
        "reason": reason,
        "deactivated_by": deactivated_by,
    }


def mark_needs_reconciliation(
    connection: sqlite3.Connection,
    publication_id: str,
    *,
    notes: str | None = None,
) -> dict:
    ensure_sc_tables(connection)
    connection.execute(
        "UPDATE sc_publications SET human_review_status = 'needs_reconciliation', "
        "human_review_notes = ? WHERE id = ?",
        (notes, publication_id),
    )
    connection.commit()
    return {"status": "needs_reconciliation", "publication_id": publication_id}


def check_eligibility(
    connection: sqlite3.Connection,
    ticker: str,
    *,
    as_of_date: str | None = None,
) -> dict:
    """Check Shariah eligibility from approved SC publications.

    Returns one of:
      PASS    — approved publication confirms COMPLIANT
      REJECT  — authoritative source confirms not eligible
      UNKNOWN — no approved publication contains this ticker

    ``as_of_date`` queries the active publication as of that date.
    None means the current active publication.
    """
    ensure_sc_tables(connection)
    normalized = str(ticker).strip()

    if as_of_date:
        pub_row = connection.execute(
            "SELECT * FROM sc_publications "
            "WHERE human_review_status = 'approved' "
            "AND activated_at IS NOT NULL "
            "AND publication_date <= ? "
            "ORDER BY publication_date DESC LIMIT 1",
            (as_of_date,),
        ).fetchone()
    else:
        pub_row = connection.execute(
            "SELECT * FROM sc_publications "
            "WHERE human_review_status = 'approved' "
            "AND activated_at IS NOT NULL "
            "AND deactivated_at IS NULL "
            "ORDER BY publication_date DESC LIMIT 1",
        ).fetchone()

    if not pub_row:
        return {
            "status": "UNKNOWN",
            "reason": "no_approved_publication",
            "ticker": normalized,
        }

    pub = dict(pub_row)
    sec_row = connection.execute(
        "SELECT * FROM sc_security_status WHERE publication_id = ? AND ticker = ?",
        (pub["id"], normalized),
    ).fetchone()

    if not sec_row:
        return {
            "status": "UNKNOWN",
            "reason": "not_present_in_approved_publication",
            "ticker": normalized,
            "publication_id": pub["id"],
            "publication_date": pub["publication_date"],
            "source_document_hash": pub.get("source_document_hash"),
        }

    sec = dict(sec_row)
    if sec["shariah_status"] == "COMPLIANT":
        return {
            "status": "PASS",
            "reason": "authoritative_compliant",
            "ticker": normalized,
            "issuer_name": sec.get("issuer_name"),
            "publication_id": pub["id"],
            "publication_date": pub["publication_date"],
            "board": sec.get("board"),
            "sector": sec.get("sector"),
            "source_document_hash": pub.get("source_document_hash"),
        }

    return {
        "status": "REJECT",
        "reason": "authoritative_non_compliant",
        "ticker": normalized,
        "shariah_status": sec["shariah_status"],
        "publication_id": pub["id"],
        "publication_date": pub["publication_date"],
        "source_document_hash": pub.get("source_document_hash"),
    }


def publication_securities(connection: sqlite3.Connection, publication_id: str) -> list[dict]:
    ensure_sc_tables(connection)
    rows = connection.execute(
        "SELECT * FROM sc_security_status WHERE publication_id = ? ORDER BY ticker",
        (publication_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def connect_default() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn
