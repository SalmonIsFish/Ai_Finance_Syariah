import sqlite3
from typing import Optional, Dict

class UnresolvedIdentityError(Exception):
    pass

def resolve_historical_ticker(db_conn: sqlite3.Connection, ticker: str, exchange: str, as_of_date: str) -> Optional[str]:
    """
    Resolves a historical ticker string at a specific point in time to a stable internal `security_id`.
    Requires exact match on ticker and exchange. Guesses/fuzzy matching are explicitly prohibited.
    If multiple mappings overlap, returns None.
    """
    try:
        cursor = db_conn.execute(
            """
            SELECT security_id 
            FROM p4_historical_tickers
            WHERE historical_ticker = ?
              AND exchange = ?
              AND valid_from <= ?
              AND (valid_to > ? OR valid_to IS NULL)
            """,
            (ticker, exchange, as_of_date, as_of_date)
        )
        rows = cursor.fetchall()
        if len(rows) == 1:
            return rows[0][0]
        return None
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return None
        raise
