import sqlite3
import pytest
from backend.p4_identity import resolve_historical_ticker
from backend.p4_schema import ensure_p4_identity_tables

def test_missing_table_fails_closed():
    conn = sqlite3.connect(":memory:")
    # Table does NOT exist
    sec = resolve_historical_ticker(conn, "AAPL", "US", "2024-01-01")
    assert sec is None
    conn.close()

@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    ensure_p4_identity_tables(conn)
    
    # Sec 1 changes ticker from A to B
    conn.execute("INSERT INTO p4_security_identity (security_id, issuer_name) VALUES ('sec-1', 'Company 1')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec-1', 'TICKER_A', 'MYX', '2020-01-01', '2022-01-01', 'ACTIVE')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec-1', 'TICKER_B', 'MYX', '2022-01-01', NULL, 'ACTIVE')")
    
    # Sec 2 re-uses TICKER_A later
    conn.execute("INSERT INTO p4_security_identity (security_id, issuer_name) VALUES ('sec-2', 'Company 2')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec-2', 'TICKER_A', 'MYX', '2023-01-01', NULL, 'ACTIVE')")
    
    # Cross-exchange duplicate for TICKER_A
    conn.execute("INSERT INTO p4_security_identity (security_id, issuer_name) VALUES ('sec-3', 'Company 3 US')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec-3', 'TICKER_A', 'US', '2020-01-01', NULL, 'ACTIVE')")
    
    # Overlapping mapping (conflict)
    conn.execute("INSERT INTO p4_security_identity (security_id, issuer_name) VALUES ('sec-4', 'Company 4')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec-4', 'CONFLICT', 'MYX', '2021-01-01', NULL, 'ACTIVE')")
    conn.execute("INSERT INTO p4_security_identity (security_id, issuer_name) VALUES ('sec-5', 'Company 5')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec-5', 'CONFLICT', 'MYX', '2021-01-01', NULL, 'ACTIVE')")
    
    conn.commit()
    yield conn
    conn.close()

def test_exact_ticker_match(db_conn):
    sec = resolve_historical_ticker(db_conn, "TICKER_B", "MYX", "2022-06-01")
    assert sec == "sec-1"

def test_exchange_distinction(db_conn):
    # TICKER_A on MYX at this date is sec-1
    assert resolve_historical_ticker(db_conn, "TICKER_A", "MYX", "2021-06-01") == "sec-1"
    # TICKER_A on US at the exact same date is sec-3
    assert resolve_historical_ticker(db_conn, "TICKER_A", "US", "2021-06-01") == "sec-3"

def test_overlap_fails_closed(db_conn):
    # Both sec-4 and sec-5 claim 'CONFLICT' on MYX simultaneously
    sec = resolve_historical_ticker(db_conn, "CONFLICT", "MYX", "2021-06-01")
    assert sec is None

def test_valid_from_boundary(db_conn):
    sec = resolve_historical_ticker(db_conn, "TICKER_A", "MYX", "2023-01-01")
    assert sec == "sec-2"
    
    sec = resolve_historical_ticker(db_conn, "TICKER_A", "MYX", "2022-06-01")
    assert sec is None

def test_valid_to_boundary(db_conn):
    sec = resolve_historical_ticker(db_conn, "TICKER_A", "MYX", "2021-12-31")
    assert sec == "sec-1"
    
    sec = resolve_historical_ticker(db_conn, "TICKER_A", "MYX", "2022-01-01")
    assert sec is None

def test_unresolved_ticker_is_explicit_none(db_conn):
    sec = resolve_historical_ticker(db_conn, "MISSING_TICKER", "MYX", "2022-01-01")
    assert sec is None

def test_no_fuzzy_matching(db_conn):
    sec = resolve_historical_ticker(db_conn, "TICKER_b", "MYX", "2022-06-01")
    assert sec is None
