"""Compute differences between two SC Malaysia publication snapshots."""

import sqlite3

import sc_malaysia_store


def diff_publications(
    connection: sqlite3.Connection,
    old_publication_id: str,
    new_publication_id: str,
) -> dict:
    """Compare two publications and return additions, removals, and changes.

    Returns:
        {
            "old_id": ..., "new_id": ...,
            "old_count": N, "new_count": N,
            "added": [{"ticker": ..., "issuer_name": ...}, ...],
            "removed": [{"ticker": ..., "issuer_name": ...}, ...],
            "changed": [{"ticker": ..., "old_issuer": ..., "new_issuer": ...}, ...],
            "unchanged_count": N,
        }
    """
    old_secs = sc_malaysia_store.publication_securities(connection, old_publication_id)
    new_secs = sc_malaysia_store.publication_securities(connection, new_publication_id)

    old_by_ticker = {s["ticker"]: s for s in old_secs}
    new_by_ticker = {s["ticker"]: s for s in new_secs}

    old_tickers = set(old_by_ticker.keys())
    new_tickers = set(new_by_ticker.keys())

    added = []
    for t in sorted(new_tickers - old_tickers):
        s = new_by_ticker[t]
        added.append({"ticker": t, "issuer_name": s.get("issuer_name")})

    removed = []
    for t in sorted(old_tickers - new_tickers):
        s = old_by_ticker[t]
        removed.append({"ticker": t, "issuer_name": s.get("issuer_name")})

    changed = []
    unchanged = 0
    for t in sorted(old_tickers & new_tickers):
        o = old_by_ticker[t]
        n = new_by_ticker[t]
        if o.get("issuer_name") != n.get("issuer_name"):
            changed.append(
                {
                    "ticker": t,
                    "old_issuer": o.get("issuer_name"),
                    "new_issuer": n.get("issuer_name"),
                }
            )
        else:
            unchanged += 1

    return {
        "old_id": old_publication_id,
        "new_id": new_publication_id,
        "old_count": len(old_secs),
        "new_count": len(new_secs),
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_count": unchanged,
    }
