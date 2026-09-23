"""Proposal state, in its own SQLite file. The two-tap flow's memory.

    PENDING --tap1--> QUEUED --tap2--> EXECUTED
       |                 |               `-(timeout)-> EXECUTION_UNCERTAIN
       |- expire -> EXPIRED
       |- hold   -> HELD
                         `- expire -> QUEUE_EXPIRED

## Idempotency is a statement, not an `if`

Telegram redelivers callbacks. A double tap is the normal case, not an edge case, and the
gap between "read the status" and "write the new status" is exactly where a second
delivery lands. So `claim_execution` is one conditional UPDATE and the database decides
who won; `rowcount == 0` means somebody else already claimed it and the backend is never
called. `UNIQUE(queue_id)` is the belt to that brace -- the same guard `portfolio_store`
relies on, which CLAUDE.md records holding under a real double reconcile.

## Its own file

`backend/bridge/state.sqlite3`, never `paper_trading.db`. A locked bridge database must
never be able to affect the backend, and bridge state is disposable in a way the ledger
is not.

## Expiry is short because a quote is perishable

CLAUDE.md records queue 10 dying `BROKER_CANCELLED` because an option bid was 1.05 at
submission and 1.00 under two minutes later. The recorded instruction is "re-quote and
submit as close together as possible", so a proposal expires rather than resting on a
price that no longer exists.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

BRIDGE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BRIDGE_DIR / "state.sqlite3"

STATUS_PENDING = "PENDING"
STATUS_QUEUED = "QUEUED"
STATUS_EXECUTING = "EXECUTING"
STATUS_EXECUTED = "EXECUTED"
STATUS_EXECUTION_UNCERTAIN = "EXECUTION_UNCERTAIN"
# A dry run reached no broker. Recording it as EXECUTED would make the store claim an
# order was submitted when nothing was, which is the kind of quiet untruth this project
# treats as worse than a gap.
STATUS_DRY_RUN = "DRY_RUN"
STATUS_EXPIRED = "EXPIRED"
STATUS_QUEUE_EXPIRED = "QUEUE_EXPIRED"
STATUS_HELD = "HELD"
STATUS_REJECTED = "REJECTED"

TERMINAL = frozenset(
    {
        STATUS_EXECUTED,
        STATUS_EXECUTION_UNCERTAIN,
        STATUS_DRY_RUN,
        STATUS_EXPIRED,
        STATUS_QUEUE_EXPIRED,
        STATUS_HELD,
        STATUS_REJECTED,
    }
)

# An option bid goes stale in under two minutes (queue 10). An equity bid does not, but it
# is not durable either. These are ceilings on how long a quote may sit unacted upon.
OPTION_PENDING_TTL_SECONDS = 90
OPTION_QUEUE_TTL_SECONDS = 60
EQUITY_PENDING_TTL_SECONDS = 300
EQUITY_QUEUE_TTL_SECONDS = 180

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id           TEXT PRIMARY KEY,
    callback_nonce        TEXT UNIQUE NOT NULL,
    chat_id               INTEGER,
    message_id            INTEGER,
    created_at            REAL NOT NULL,
    expires_at            REAL NOT NULL,
    queue_expires_at      REAL,
    status                TEXT NOT NULL,
    status_reason         TEXT,
    symbol                TEXT,
    market                TEXT,
    side                  TEXT,
    quantity              INTEGER,
    price                 REAL,
    asset_class           TEXT,
    preview_id            TEXT,
    preview_json          TEXT,
    queue_id              INTEGER UNIQUE,
    approval_json         TEXT,
    execute_attempted_at  REAL,
    execute_result_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON proposals(status);
"""


def connect(path=None) -> sqlite3.Connection:
    """Open the bridge state database, creating it if needed."""
    target = str(path) if path is not None else str(DEFAULT_DB_PATH)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.executescript(_SCHEMA)
    connection.commit()
    return connection


def _row(connection, proposal_id: str):
    cursor = connection.execute("SELECT * FROM proposals WHERE proposal_id = ?", (proposal_id,))
    return cursor.fetchone()


def _as_dict(row) -> dict | None:
    if row is None:
        return None
    record = dict(row)
    for key in ("preview_json", "approval_json", "execute_result_json"):
        raw = record.pop(key)
        record[key[: -len("_json")]] = json.loads(raw) if raw else None
    return record


def ttls_for(asset_class: str) -> tuple:
    """(pending, queued) seconds. Options are shorter because their quotes decay faster."""
    if str(asset_class or "equity").lower() == "option":
        return OPTION_PENDING_TTL_SECONDS, OPTION_QUEUE_TTL_SECONDS
    return EQUITY_PENDING_TTL_SECONDS, EQUITY_QUEUE_TTL_SECONDS


def create_proposal(connection, *, preview: dict, chat_id: int, market: str, now=None) -> dict:
    """Record a preview as a proposal awaiting tap 1."""
    moment = time.time() if now is None else now
    asset_class = preview.get("asset_class") or "equity"
    pending_ttl, _ = ttls_for(asset_class)
    proposal_id = secrets.token_hex(8)
    # 12 bytes url-safe fits Telegram's 64-byte callback_data limit with room for the
    # action prefix, and is not guessable by someone who can see the chat.
    nonce = secrets.token_urlsafe(12)

    connection.execute(
        """
        INSERT INTO proposals (
            proposal_id, callback_nonce, chat_id, created_at, expires_at, status,
            symbol, market, side, quantity, price, asset_class, preview_id, preview_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            proposal_id,
            nonce,
            chat_id,
            moment,
            moment + pending_ttl,
            STATUS_PENDING,
            preview.get("symbol"),
            str(market or "US").upper(),
            preview.get("side"),
            preview.get("quantity"),
            preview.get("price"),
            asset_class,
            str(preview.get("preview_id") or ""),
            json.dumps(preview, default=str),
        ),
    )
    connection.commit()
    return _as_dict(_row(connection, proposal_id))


def load_proposal(connection, proposal_id: str) -> dict | None:
    return _as_dict(_row(connection, proposal_id))


def resolve_nonce(connection, nonce: str) -> dict | None:
    cursor = connection.execute("SELECT * FROM proposals WHERE callback_nonce = ?", (nonce,))
    return _as_dict(cursor.fetchone())


def attach_message(connection, proposal_id: str, *, message_id: int) -> None:
    connection.execute(
        "UPDATE proposals SET message_id = ? WHERE proposal_id = ?", (message_id, proposal_id)
    )
    connection.commit()


def expire_due(connection, *, now=None) -> list:
    """Move anything past its deadline to an expired state. Returns what moved."""
    moment = time.time() if now is None else now
    moved = []

    cursor = connection.execute(
        "SELECT proposal_id FROM proposals WHERE status = ? AND expires_at <= ?",
        (STATUS_PENDING, moment),
    )
    for row in cursor.fetchall():
        connection.execute(
            "UPDATE proposals SET status = ?, status_reason = ? WHERE proposal_id = ? AND status = ?",
            (STATUS_EXPIRED, "quote_stale_requote_required", row["proposal_id"], STATUS_PENDING),
        )
        moved.append(row["proposal_id"])

    cursor = connection.execute(
        "SELECT proposal_id FROM proposals WHERE status = ? AND queue_expires_at <= ?",
        (STATUS_QUEUED, moment),
    )
    for row in cursor.fetchall():
        connection.execute(
            "UPDATE proposals SET status = ?, status_reason = ? WHERE proposal_id = ? AND status = ?",
            (
                STATUS_QUEUE_EXPIRED,
                "queued_too_long_requote_required",
                row["proposal_id"],
                STATUS_QUEUED,
            ),
        )
        moved.append(row["proposal_id"])

    connection.commit()
    return moved


def mark_queued(connection, proposal_id: str, *, approval: dict, now=None) -> dict:
    """Tap 1 succeeded: record the queue id and start the shorter tap-2 clock."""
    moment = time.time() if now is None else now
    record = load_proposal(connection, proposal_id)
    if record is None:
        return {"status": "REJECT", "reason": "unknown_proposal"}
    if record["status"] != STATUS_PENDING:
        return {"status": "REJECT", "reason": f"not_pending_{record['status'].lower()}"}

    _, queue_ttl = ttls_for(record["asset_class"])
    queue_id = (approval or {}).get("queue_id")
    try:
        connection.execute(
            """
            UPDATE proposals
               SET status = ?, queue_id = ?, approval_json = ?, queue_expires_at = ?
             WHERE proposal_id = ? AND status = ?
            """,
            (
                STATUS_QUEUED,
                queue_id,
                json.dumps(approval, default=str),
                moment + queue_ttl,
                proposal_id,
                STATUS_PENDING,
            ),
        )
    except sqlite3.IntegrityError:
        # UNIQUE(queue_id): this queue entry already belongs to another proposal, which
        # means something was double-submitted upstream. Refuse rather than overwrite.
        connection.rollback()
        return {"status": "REJECT", "reason": "queue_id_already_claimed"}
    connection.commit()
    return {"status": "OK", "proposal": load_proposal(connection, proposal_id)}


def mark_rejected(connection, proposal_id: str, *, reason: str, approval=None) -> dict:
    connection.execute(
        "UPDATE proposals SET status = ?, status_reason = ?, approval_json = ? WHERE proposal_id = ?",
        (
            STATUS_REJECTED,
            reason,
            json.dumps(approval, default=str) if approval else None,
            proposal_id,
        ),
    )
    connection.commit()
    return {"status": "OK", "proposal": load_proposal(connection, proposal_id)}


def hold_proposal(connection, proposal_id: str, *, reason: str) -> dict:
    """The risk officer's one power: stop a proposal. Nothing can advance one."""
    connection.execute(
        "UPDATE proposals SET status = ?, status_reason = ? WHERE proposal_id = ? AND status IN (?, ?)",
        (STATUS_HELD, reason, proposal_id, STATUS_PENDING, STATUS_QUEUED),
    )
    connection.commit()
    return {"status": "OK", "proposal": load_proposal(connection, proposal_id)}


def claim_execution(connection, proposal_id: str, *, now=None) -> dict:
    """Take exclusive right to execute, or report that someone already has.

    This is the idempotency primitive and it is deliberately ONE statement. Reading the
    status and then writing it would leave a window that a redelivered Telegram callback
    lands in, and the cost of losing that race is two orders.
    """
    moment = time.time() if now is None else now
    cursor = connection.execute(
        """
        UPDATE proposals
           SET status = ?, execute_attempted_at = ?
         WHERE proposal_id = ?
           AND status = ?
           AND execute_attempted_at IS NULL
        """,
        (STATUS_EXECUTING, moment, proposal_id, STATUS_QUEUED),
    )
    connection.commit()
    if cursor.rowcount == 0:
        record = load_proposal(connection, proposal_id)
        if record is None:
            return {"status": "REJECT", "reason": "unknown_proposal"}
        return {
            "status": "ALREADY_CLAIMED",
            "reason": f"not_executable_{record['status'].lower()}",
            "proposal": record,
        }
    return {"status": "OK", "proposal": load_proposal(connection, proposal_id)}


def record_execution(
    connection, proposal_id: str, *, result: dict, uncertain=False, dry_run=False
) -> dict:
    """Write the outcome. An uncertain result is NOT a failure to retry -- it is a fact.

    A timeout on execute means the request may or may not have reached the broker. The
    only safe next step is a human checking /paper/status and reconciling, so the state
    says exactly that rather than reverting to QUEUED where a tap could resend it.
    """
    if dry_run:
        status = STATUS_DRY_RUN
    elif uncertain:
        status = STATUS_EXECUTION_UNCERTAIN
    else:
        status = STATUS_EXECUTED
    connection.execute(
        "UPDATE proposals SET status = ?, execute_result_json = ? WHERE proposal_id = ?",
        (status, json.dumps(result, default=str), proposal_id),
    )
    connection.commit()
    return {"status": "OK", "proposal": load_proposal(connection, proposal_id)}


def open_proposals(connection) -> list:
    cursor = connection.execute(
        "SELECT * FROM proposals WHERE status IN (?, ?) ORDER BY created_at",
        (STATUS_PENDING, STATUS_QUEUED),
    )
    return [_as_dict(row) for row in cursor.fetchall()]
