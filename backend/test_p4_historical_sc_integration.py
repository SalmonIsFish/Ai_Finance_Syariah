import sqlite3
import pytest
from backend import sc_malaysia_store
from backend.p4_historical_sc import get_historical_shariah_status, authoritative_publication_as_of

def test_historical_sc_integration_real_schema():
    conn = sqlite3.connect(":memory:")
    # Use the real schema
    sc_malaysia_store.ensure_sc_tables(conn)
    
    # Insert a real publication record using store structure
    conn.execute(
        """
        INSERT INTO sc_publications (
            id, publication_date, effective_date, ingestion_timestamp, human_review_status
        ) VALUES (
            'pub-real-1', '2024-05-24', '2024-05-29', '2024-05-24T00:00:00Z', 'approved'
        )
        """
    )
    
    # Insert a real COMPLIANT security
    conn.execute(
        """
        INSERT INTO sc_security_status (
            publication_id, ticker, shariah_status, sector
        ) VALUES (
            'pub-real-1', 'AAPL', 'Compliant', 'Tech'
        )
        """
    )
    conn.commit()
    
    # 1. Test authoritative resolver finds it
    pub = authoritative_publication_as_of(conn, "2024-06-01", "2024-05-31")
    assert pub["id"] == "pub-real-1"
    
    # 2. Test status maps COMPLIANT to PASS
    status = get_historical_shariah_status(conn, "AAPL", "pub-real-1")
    assert status["status"] == "PASS"
    assert status["raw_status"] == "Compliant"
    
    conn.close()
