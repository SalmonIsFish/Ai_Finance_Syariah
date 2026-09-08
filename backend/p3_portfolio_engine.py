import sqlite3
import json
from datetime import datetime, timezone

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def ensure_p3_tables(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_portfolios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            base_currency TEXT NOT NULL,
            initial_cash REAL NOT NULL,
            current_cash REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_portfolio_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES p3_portfolios(id),
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL,
            average_cost REAL NOT NULL,
            market_value REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            sector TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(portfolio_id, ticker)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_portfolio_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            portfolio_id INTEGER NOT NULL REFERENCES p3_portfolios(id),
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            requested_price REAL NOT NULL,
            status TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            executed_at TEXT,
            rejection_reason TEXT,
            actor TEXT,
            shariah_publication_id TEXT,
            shariah_verdict TEXT,
            evidence_reference TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS p3_portfolio_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES p3_portfolio_orders(id),
            portfolio_id INTEGER NOT NULL REFERENCES p3_portfolios(id),
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            quantity REAL NOT NULL,
            executed_price REAL NOT NULL,
            filled_at TEXT NOT NULL
        )
        """
    )
    connection.commit()

def create_portfolio(connection: sqlite3.Connection, name: str, initial_cash: float, base_currency: str = "USD") -> dict:
    ensure_p3_tables(connection)
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO p3_portfolios (name, status, base_currency, initial_cash, current_cash, created_at, updated_at)
        VALUES (?, 'ACTIVE', ?, ?, ?, ?, ?)
        """,
        (name, base_currency, initial_cash, initial_cash, now, now)
    )
    connection.commit()
    return get_portfolio(connection, cursor.lastrowid)

def get_portfolio(connection: sqlite3.Connection, portfolio_id: int) -> dict:
    ensure_p3_tables(connection)
    row = connection.execute("SELECT * FROM p3_portfolios WHERE id = ?", (portfolio_id,)).fetchone()
    if not row:
        raise ValueError(f"Portfolio {portfolio_id} not found")
    return dict(row)

def get_portfolio_positions(connection: sqlite3.Connection, portfolio_id: int) -> list[dict]:
    ensure_p3_tables(connection)
    rows = connection.execute("SELECT * FROM p3_portfolio_positions WHERE portfolio_id = ?", (portfolio_id,)).fetchall()
    return [dict(r) for r in rows]

def get_portfolio_orders(connection: sqlite3.Connection, portfolio_id: int) -> list[dict]:
    ensure_p3_tables(connection)
    rows = connection.execute("SELECT * FROM p3_portfolio_orders WHERE portfolio_id = ?", (portfolio_id,)).fetchall()
    return [dict(r) for r in rows]

def get_order(connection: sqlite3.Connection, order_id: int) -> dict:
    ensure_p3_tables(connection)
    row = connection.execute("SELECT * FROM p3_portfolio_orders WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise ValueError(f"Order {order_id} not found")
    return dict(row)

def update_order_status(connection: sqlite3.Connection, order_id: int, current_status: str, new_status: str, updates: dict = None) -> dict:
    ensure_p3_tables(connection)
    
    # Strictly enforce state machine
    valid_transitions = {
        "PROPOSED": {"APPROVED", "REJECTED", "CANCELLED"},
        "APPROVED": {"EXECUTED", "REJECTED", "CANCELLED"}
    }
    if current_status not in valid_transitions or new_status not in valid_transitions[current_status]:
        raise ValueError(f"Invalid state transition from {current_status} to {new_status}")
    
    updates = updates or {}
    set_clauses = ["status = ?"]
    params = [new_status]
    for k, v in updates.items():
        set_clauses.append(f"{k} = ?")
        params.append(v)
    
    set_clauses_str = ", ".join(set_clauses)
    params.extend([order_id, current_status])
    
    cursor = connection.execute(
        f"UPDATE p3_portfolio_orders SET {set_clauses_str} WHERE id = ? AND status = ?",
        tuple(params)
    )
    if cursor.rowcount != 1:
        raise ValueError(f"Concurrency error: could not update order {order_id} from {current_status} to {new_status}")
    
    return get_order(connection, order_id)
