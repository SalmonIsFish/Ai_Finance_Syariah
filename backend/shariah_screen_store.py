"""Append-only audit log of every Shariah screen the US path actually ran.

This is the lean half of the ``shariah_screens`` store specified in
``NEXT_STEPS.md`` -- timestamp, ticker, verdict, and the deciding ratios, so a
verdict can be dated and compared against the one before it. Dividend
purification tracking and the time-varying traffic light described there are
deliberately **not** here; they were scoped out as too much for the remaining
days before submission.

Append-only rather than overwrite-in-place because re-screening has to detect
*change*, and change needs the previous verdict to compare against. That
comparison is what ``previous_status`` and the returned ``changed`` flag carry.

This module must never import ``sec_edgar_screen``. ``test_single_screening_path``
enforces a four-module allowlist for that import, and a store is not on it --
so the limit the ratios were judged against is read off the verdict
(``ratios["limit_pct"]``) rather than imported from the screen.
"""

import json
import sqlite3
from datetime import datetime, timezone

from config import BACKEND_DIR


DB_PATH = BACKEND_DIR / "paper_trading.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_shariah_screen_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS shariah_screens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            screened_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            status TEXT NOT NULL,
            screen TEXT,
            report_date TEXT,
            debt_ratio_pct REAL,
            cash_ratio_pct REAL,
            limit_pct REAL,
            provider TEXT,
            reason TEXT,
            previous_status TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    connection.commit()


def _maybe_float(value) -> float | None:
    """Coerce a ratio to a float, or None. Never raise -- a malformed ratio must
    not cost us the audit row that records the verdict it belongs to."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def latest_shariah_screen(connection: sqlite3.Connection, symbol: str) -> dict | None:
    """The most recent screen recorded for one symbol, or None."""
    ensure_shariah_screen_tables(connection)
    normalized = str(symbol or "").strip().upper()
    row = connection.execute(
        "SELECT * FROM shariah_screens WHERE symbol = ? ORDER BY id DESC LIMIT 1",
        (normalized,),
    ).fetchone()
    return dict(row) if row else None


def record_shariah_screen(connection: sqlite3.Connection, verdict: dict) -> dict:
    """Append one screening verdict. Returns the row identity plus whether the
    verdict differs from the previous one recorded for the same symbol."""
    ensure_shariah_screen_tables(connection)

    verdict = verdict if isinstance(verdict, dict) else {}
    symbol = str(verdict.get("symbol") or "").strip().upper()
    status = verdict.get("status") or "UNKNOWN"
    ratios = verdict.get("ratios")
    if not isinstance(ratios, dict):
        ratios = {}

    previous = latest_shariah_screen(connection, symbol)
    previous_status = previous["status"] if previous else None

    screened_at = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO shariah_screens (
            screened_at, symbol, status, screen, report_date,
            debt_ratio_pct, cash_ratio_pct, limit_pct,
            provider, reason, previous_status, payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            screened_at,
            symbol,
            status,
            verdict.get("screen"),
            verdict.get("report_date"),
            _maybe_float(ratios.get("debt_ratio_pct")),
            _maybe_float(ratios.get("cash_ratio_pct")),
            _maybe_float(ratios.get("limit_pct")),
            verdict.get("provider"),
            verdict.get("reason"),
            previous_status,
            json.dumps(verdict, sort_keys=True, default=str),
        ),
    )
    connection.commit()
    return {
        "id": cursor.lastrowid,
        "screened_at": screened_at,
        "symbol": symbol,
        "status": status,
        "previous_status": previous_status,
        "changed": previous_status is not None and previous_status != status,
    }


SCREEN_COLUMNS = (
    "id, screened_at, symbol, status, screen, report_date, "
    "debt_ratio_pct, cash_ratio_pct, limit_pct, provider, reason, "
    "previous_status, payload"
)


def _hydrate(rows) -> list[dict]:
    """Rows -> dicts with `payload` parsed. Shared so every listing function
    returns the identical row shape; a caller must not have to care which query
    produced a row."""
    screens = []
    for row in rows:
        screen = dict(row)
        try:
            screen["payload"] = json.loads(screen["payload"])
        except (TypeError, json.JSONDecodeError):
            screen["payload"] = {}
        screens.append(screen)
    return screens


def list_shariah_screens(
    connection: sqlite3.Connection, *, symbol: str | None = None, limit: int = 100
) -> list[dict]:
    """Recent screens, newest first. Optionally narrowed to one symbol."""
    ensure_shariah_screen_tables(connection)
    capped_limit = max(1, min(2000, limit))
    if symbol:
        rows = connection.execute(
            f"SELECT {SCREEN_COLUMNS} FROM shariah_screens "
            "WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (str(symbol).strip().upper(), capped_limit),
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT {SCREEN_COLUMNS} FROM shariah_screens ORDER BY id DESC LIMIT ?",
            (capped_limit,),
        ).fetchall()
    return _hydrate(rows)


def latest_screen_per_symbol(connection: sqlite3.Connection) -> list[dict]:
    """The current verdict for every symbol ever screened -- one row each.

    This table is an append-only log, and it is far more append than log: in
    production on 2026-09-22 it held 14,213 rows for 16 distinct symbols, AAPL
    alone appearing 1,301 times. Listing it raw answers "what did we screen
    recently", which is a question about *activity*. This answers "what do we
    currently believe about each company", which is a question about *state* --
    and it is the only one of the two a reader can mistake for a roster.

    That distinction is why this exists rather than the UI de-duplicating
    client-side: a capped log cannot be de-duplicated after the fact without
    silently dropping symbols whose latest screen fell outside the cap. There
    is deliberately no limit parameter -- the result is bounded by the number
    of companies, not by traffic.

    Ordered by symbol, not by recency: this is a roster to look things up in.
    """
    ensure_shariah_screen_tables(connection)
    rows = connection.execute(
        f"SELECT {SCREEN_COLUMNS} FROM shariah_screens "
        "WHERE id IN (SELECT MAX(id) FROM shariah_screens GROUP BY symbol) "
        "ORDER BY symbol ASC"
    ).fetchall()
    return _hydrate(rows)


def record_screen_to_default_db(verdict: dict) -> dict:
    """Append to the backend's own paper_trading.db.

    The screening path has no connection to hand -- agents/shariah_agent.py takes
    none, and importing local_api from there would be circular -- so this opens
    its own, the same escape hatch sec_edgar_cache.py already uses via
    config.BACKEND_DIR. Errors propagate; the caller decides whether a failed log
    write matters, and in sec_edgar_screen it deliberately does not.
    """
    connection = sqlite3.connect(DB_PATH)
    try:
        connection.row_factory = sqlite3.Row
        return record_shariah_screen(connection, verdict)
    finally:
        connection.close()
