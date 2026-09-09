import sqlite3

def ensure_p4_identity_tables(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS p4_security_identity (
            security_id TEXT PRIMARY KEY,
            issuer_name TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS p4_historical_tickers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            security_id TEXT NOT NULL REFERENCES p4_security_identity(security_id),
            historical_ticker TEXT NOT NULL,
            exchange TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            status TEXT NOT NULL
        )
        """
    )
