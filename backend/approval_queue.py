"""Structured approval queue storage for paper orders."""

import json
import sqlite3
from datetime import datetime, timezone


def ensure_approval_queue(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity INTEGER,
            price REAL,
            notional REAL,
            approval_status TEXT NOT NULL,
            execution_environment TEXT,
            broker_submission INTEGER NOT NULL,
            shariah_status TEXT,
            shariah_market TEXT,
            quant_signal TEXT,
            risk_status TEXT,
            execution_status TEXT,
            execution_message TEXT,
            executed_at TEXT,
            payload TEXT NOT NULL
        )
        """
    )
    existing_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(approval_queue)").fetchall()
    }
    migrations = {
        "execution_status": "ALTER TABLE approval_queue ADD COLUMN execution_status TEXT",
        "execution_message": "ALTER TABLE approval_queue ADD COLUMN execution_message TEXT",
        "executed_at": "ALTER TABLE approval_queue ADD COLUMN executed_at TEXT",
    }
    for column, statement in migrations.items():
        if column not in existing_columns:
            connection.execute(statement)
    connection.commit()


def record_approval(
    connection: sqlite3.Connection,
    *,
    preview: dict,
    approval: dict,
    approved_by_user: bool,
    verified_shariah: dict | None = None,
    verified_risk: dict | None = None,
) -> dict:
    """`verified_shariah` / `verified_risk` are the server-derived verdicts the
    caller actually gated the decision on -- pass them explicitly rather than
    let this function fall back to reading the client-echoed `preview` dict,
    which is untrusted (see local_api.authoritative_shariah_verdict and
    authoritative_risk_verdict). Omitting them preserves the old
    derive-from-preview behavior for existing callers/tests that construct
    their own preview/approval fixtures directly, not through the real
    /paper/approval endpoint.
    """
    ensure_approval_queue(connection)
    created_at = datetime.now(timezone.utc).isoformat()
    agents = preview.get("agent_summary", {})
    shariah = (
        verified_shariah
        if verified_shariah is not None
        else agents.get("shariah", preview.get("shariah", {}))
    )
    quant = agents.get("quant", {})
    risk = (
        verified_risk if verified_risk is not None else agents.get("risk", preview.get("risk", {}))
    )
    status = approval.get("status", "UNKNOWN")
    payload = {
        "approved_by_user": approved_by_user,
        "preview": preview,
        "approval": approval,
    }
    cursor = connection.execute(
        """
        INSERT INTO approval_queue (
            created_at,
            symbol,
            side,
            quantity,
            price,
            notional,
            approval_status,
            execution_environment,
            broker_submission,
            shariah_status,
            shariah_market,
            quant_signal,
            risk_status,
            execution_status,
            execution_message,
            executed_at,
            payload
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            created_at,
            preview.get("symbol") or "",
            preview.get("side") or "BUY",
            preview.get("quantity"),
            preview.get("price"),
            preview.get("notional"),
            status,
            approval.get("execution_environment"),
            1 if approval.get("broker_submission") else 0,
            shariah.get("status"),
            shariah.get("market"),
            quant.get("signal"),
            risk.get("status"),
            "NOT_EXECUTED",
            None,
            None,
            json.dumps(payload, sort_keys=True),
        ),
    )
    connection.commit()
    return {"id": cursor.lastrowid, "created_at": created_at, "approval_status": status}


def list_approvals(connection: sqlite3.Connection, *, limit: int = 100) -> list[dict]:
    ensure_approval_queue(connection)
    rows = connection.execute(
        """
        SELECT
            id,
            created_at,
            symbol,
            side,
            quantity,
            price,
            notional,
            approval_status,
            execution_environment,
            broker_submission,
            shariah_status,
            shariah_market,
            quant_signal,
            risk_status,
            execution_status,
            execution_message,
            executed_at,
            payload
        FROM approval_queue
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    approvals = []
    for row in rows:
        item = dict(row)
        item["broker_submission"] = bool(item["broker_submission"])
        approvals.append(item)
    return approvals


def get_approval(connection: sqlite3.Connection, approval_id: int) -> dict | None:
    ensure_approval_queue(connection)
    row = connection.execute(
        """
        SELECT
            id,
            created_at,
            symbol,
            side,
            quantity,
            price,
            notional,
            approval_status,
            execution_environment,
            broker_submission,
            shariah_status,
            shariah_market,
            quant_signal,
            risk_status,
            execution_status,
            execution_message,
            executed_at,
            payload
        FROM approval_queue
        WHERE id = ?
        """,
        (approval_id,),
    ).fetchone()
    if row is None:
        return None
    item = dict(row)
    item["broker_submission"] = bool(item["broker_submission"])
    return item


def update_execution_status(
    connection: sqlite3.Connection, approval_id: int, *, status: str, message: str
) -> dict:
    ensure_approval_queue(connection)
    executed_at = datetime.now(timezone.utc).isoformat()
    connection.execute(
        """
        UPDATE approval_queue
        SET execution_status = ?, execution_message = ?, executed_at = ?
        WHERE id = ?
        """,
        (status, message, executed_at, approval_id),
    )
    connection.commit()
    return {
        "id": approval_id,
        "execution_status": status,
        "execution_message": message,
        "executed_at": executed_at,
    }


def record_broker_submission(
    connection: sqlite3.Connection, approval_id: int, *, broker_response: dict
) -> dict:
    ensure_approval_queue(connection)
    executed_at = broker_response.get("submitted_at") or datetime.now(timezone.utc).isoformat()
    row = connection.execute(
        "SELECT payload FROM approval_queue WHERE id = ?", (approval_id,)
    ).fetchone()
    payload = {}
    if row is not None:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {"raw_payload": row["payload"]}
    payload["broker_submission"] = broker_response
    status = broker_response.get("status", "BROKER_SUBMITTED")
    message = (
        broker_response.get("broker_order_id")
        or broker_response.get("reason")
        or "paper order submitted"
    )
    connection.execute(
        """
        UPDATE approval_queue
        SET broker_submission = 1,
            execution_status = ?,
            execution_message = ?,
            executed_at = ?,
            payload = ?
        WHERE id = ?
        """,
        (status, message, executed_at, json.dumps(payload, sort_keys=True), approval_id),
    )
    connection.commit()
    return {
        "id": approval_id,
        "broker_submission": True,
        "execution_status": status,
        "execution_message": message,
        "executed_at": executed_at,
        "broker_response": broker_response,
    }


def record_broker_reconciliation(
    connection: sqlite3.Connection, approval_id: int, *, reconciliation: dict
) -> dict:
    ensure_approval_queue(connection)
    reconciled_at = reconciliation.get("reconciled_at") or datetime.now(timezone.utc).isoformat()
    row = connection.execute(
        "SELECT payload FROM approval_queue WHERE id = ?", (approval_id,)
    ).fetchone()
    payload = {}
    if row is not None:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            payload = {"raw_payload": row["payload"]}

    history = payload.get("broker_reconciliation_history")
    if not isinstance(history, list):
        history = []
    history.append(reconciliation)
    payload["broker_reconciliation"] = reconciliation
    payload["broker_reconciliation_history"] = history[-50:]

    status = reconciliation.get("status", "BROKER_STATUS_UNKNOWN")
    message = (
        reconciliation.get("broker_order_id")
        or reconciliation.get("reason")
        or reconciliation.get("order_status")
        or "paper order reconciled"
    )
    connection.execute(
        """
        UPDATE approval_queue
        SET execution_status = ?,
            execution_message = ?,
            executed_at = ?,
            payload = ?
        WHERE id = ?
        """,
        (status, message, reconciled_at, json.dumps(payload, sort_keys=True), approval_id),
    )
    connection.commit()
    return {
        "id": approval_id,
        "broker_submission": True,
        "execution_status": status,
        "execution_message": message,
        "executed_at": reconciled_at,
        "broker_reconciliation": reconciliation,
    }
