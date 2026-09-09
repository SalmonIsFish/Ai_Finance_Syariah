import sqlite3
from typing import Dict, Any

class NoAuthoritativeUniverseError(Exception):
    pass

def authoritative_publication_as_of(
    db_conn: sqlite3.Connection, 
    decision_date: str, 
    information_cutoff: str
) -> Dict[str, Any]:
    """
    Returns the authoritative SC publication dictionary for a given decision date,
    strictly enforcing the information cutoff.
    
    Tie-breaks:
    - If multiple exist prior to the decision date, select max effective_date.
    - If effective_date is identical, select max publication_date.
    
    Timestamps `approved_at` and `activated_at` are strictly ignored.
    """
    cursor = db_conn.execute(
        """
        SELECT id, effective_date, publication_date, human_review_status
        FROM sc_publications
        WHERE human_review_status = 'approved'
          AND effective_date <= ? 
          AND publication_date <= ?
        ORDER BY effective_date DESC, publication_date DESC
        LIMIT 1
        """,
        (decision_date, information_cutoff)
    )
    
    row = cursor.fetchone()
    if not row:
        raise NoAuthoritativeUniverseError(f"No authoritative publication found for decision_date={decision_date} and cutoff={information_cutoff}")
    
    # Return as dict
    keys = ["id", "effective_date", "publication_date", "human_review_status"]
    pub_dict = dict(zip(keys, row))
    return pub_dict

def get_historical_shariah_status(
    db_conn: sqlite3.Connection,
    ticker: str,
    publication_id: str
) -> Dict[str, Any]:
    """
    Fetches the Shariah status from the exact historical publication.
    If the security is not found in the publication, its status is explicitly UNKNOWN.
    We NEVER infer 'REJECT' solely from absence, preserving Amanah Trader rules.
    """
    cursor = db_conn.execute(
        """
        SELECT shariah_status, sector
        FROM sc_security_status
        WHERE publication_id = ? AND ticker = ?
        """,
        (publication_id, ticker)
    )
    row = cursor.fetchone()
    if not row:
        return {"status": "UNKNOWN", "raw_status": None, "sector": None}
        
    raw_status = row[0]
    sector = row[1]
    
    status_upper = raw_status.upper() if raw_status else ""
    if status_upper == "COMPLIANT":
        canonical = "PASS"
    elif "NON-COMPLIANT" in status_upper:
        canonical = "REJECT"
    elif status_upper in ["PASS", "REJECT", "UNKNOWN"]:
        canonical = status_upper
    else:
        canonical = "UNKNOWN"
        
    return {"status": canonical, "raw_status": raw_status, "sector": sector}
