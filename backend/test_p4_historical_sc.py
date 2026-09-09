import sqlite3
import pytest
from backend.p4_historical_sc import (
    authoritative_publication_as_of, 
    get_historical_shariah_status,
    NoAuthoritativeUniverseError
)

@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE sc_publications (
            id TEXT PRIMARY KEY,
            effective_date TEXT,
            publication_date TEXT,
            human_review_status TEXT,
            approved_at TEXT,
            activated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE sc_security_status (
            publication_id TEXT NOT NULL,
            ticker TEXT NOT NULL,
            shariah_status TEXT NOT NULL,
            sector TEXT,
            PRIMARY KEY(publication_id, ticker)
        )
        """
    )
    
    pubs = [
        ("pub-1", "2024-05-29", "2024-05-24", "approved", "2024-05-24", "2024-05-29"),
        ("pub-2", "2024-11-29", "2024-11-24", "approved", "2024-11-24", "2024-11-29"),
        ("pub-3a", "2025-05-29", "2025-05-20", "approved", "2025-05-21", "2025-05-29"),
        ("pub-3b", "2025-05-29", "2025-05-24", "approved", "2025-05-25", "2025-05-29"),
        ("pub-gov", "2023-01-15", "2023-01-01", "pending", None, None),
        ("sc-sac-my-2026-05-29", "2026-05-29", "2026-05-24", "pending", None, None)
    ]
    
    for p in pubs:
        conn.execute(
            "INSERT INTO sc_publications (id, effective_date, publication_date, human_review_status, approved_at, activated_at) VALUES (?, ?, ?, ?, ?, ?)",
            p
        )
        
    conn.execute("INSERT INTO sc_security_status (publication_id, ticker, shariah_status, sector) VALUES (?, ?, ?, ?)", ("pub-1", "PASSING_STOCK", "Compliant", "Tech"))
    conn.execute("INSERT INTO sc_security_status (publication_id, ticker, shariah_status, sector) VALUES (?, ?, ?, ?)", ("pub-1", "FAILING_STOCK", "Non-Compliant", "Finance"))
        
    conn.commit()
    yield conn
    conn.close()

def test_pit_normal_sequence(db_conn):
    pub = authoritative_publication_as_of(db_conn, "2024-06-01", "2024-05-31")
    assert pub["id"] == "pub-1"
    
def test_pit_future_effective_invisible(db_conn):
    pub = authoritative_publication_as_of(db_conn, "2024-11-25", "2024-11-24")
    assert pub["id"] == "pub-1"

def test_pit_information_cutoff(db_conn):
    pub = authoritative_publication_as_of(db_conn, "2025-06-15", "2025-05-22")
    assert pub["id"] == "pub-3a"

def test_pit_tie_break(db_conn):
    pub = authoritative_publication_as_of(db_conn, "2025-06-01", "2025-05-31")
    assert pub["id"] == "pub-3b"

def test_pit_governance_ignored_but_must_be_approved(db_conn):
    # pub-gov is pending, so it should be skipped
    with pytest.raises(NoAuthoritativeUniverseError):
        authoritative_publication_as_of(db_conn, "2023-02-01", "2023-01-31")

def test_pit_pending_sc_sac_my_2026_05_29(db_conn):
    # The staged one is pending, must be skipped
    pub = authoritative_publication_as_of(db_conn, "2026-06-01", "2026-05-31")
    # Will fallback to pub-3b
    assert pub["id"] == "pub-3b"

def test_pit_no_universe(db_conn):
    with pytest.raises(NoAuthoritativeUniverseError):
        authoritative_publication_as_of(db_conn, "1999-01-01", "1998-12-31")

def test_historical_status_resolution(db_conn):
    status = get_historical_shariah_status(db_conn, "PASSING_STOCK", "pub-1")
    assert status["status"] == "PASS"
    assert status["raw_status"] == "Compliant"
    assert status["sector"] == "Tech"
    
    status = get_historical_shariah_status(db_conn, "FAILING_STOCK", "pub-1")
    assert status["status"] == "REJECT"
    assert status["raw_status"] == "Non-Compliant"
    
    status = get_historical_shariah_status(db_conn, "MISSING_STOCK", "pub-1")
    assert status["status"] == "UNKNOWN"
    assert status["sector"] is None
