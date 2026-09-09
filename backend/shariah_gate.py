"""Fail-closed Shariah universe gate.

Three outcomes:
  PASS    — approved, activated SC publication confirms COMPLIANT
  REJECT  — approved, activated SC publication confirms not eligible
  UNKNOWN — no approved, activated publication settles the question

UNKNOWN fails closed for trading but is preserved in evidence.

This module imports ONLY sc_malaysia_store and config. It does NOT import
any LLM, research, vault_indexer, or copilot module. The Shariah gate's
sole data source is the approved SC Malaysia publication store.

The legacy flat-JSON fallback path (_check_via_legacy_json) can NEVER
produce PASS or REJECT, regardless of what the file itself claims -- an
un-reviewed file was never approved or activated through sc_malaysia_store's
workflow, so it has no authority. This was a real bypass until the Phase 0
integration audit found it (see git history): a fresh clone with no
approved SC publication could still get a Shariah PASS purely from the
committed legacy JSON's own internal "active" flag. Do not restore this.
"""

import json
import sqlite3
from pathlib import Path

from config import load_settings

# The SC Malaysia store is the primary authority. The legacy flat JSON
# is a migration compatibility path, not the final authority model.
try:
    import sc_malaysia_store

    _HAS_SC_STORE = True
except ImportError:
    _HAS_SC_STORE = False


def _check_via_sc_store(symbol: str) -> dict:
    """Query the approved SC Malaysia publication store."""
    conn = sc_malaysia_store.connect_default()
    try:
        return sc_malaysia_store.check_eligibility(conn, symbol)
    finally:
        conn.close()


def _check_via_legacy_json(symbol: str) -> dict:
    """Legacy flat JSON lookup. Used only when the SC store has no data.

    This path can NEVER produce PASS or REJECT, regardless of what the JSON
    file itself claims (including its own internal `validation.status`
    field). It predates the SC publication human-approval/activation
    workflow and was never wired into it -- an un-reviewed flat file has no
    business asserting authoritative compliance, and doing so would let a
    fresh clone (or any deployment with no approved SC publication) produce
    a Shariah PASS with zero human review. It exists only so a symbol lookup
    on an unconfigured system fails closed with a diagnosable reason instead
    of an unhandled error; the file's presence and content are informational
    only. Once a publication is ingested, approved, and activated in the SC
    store, this path is never reached for Malaysian tickers at all.
    """
    settings = load_settings()
    if not settings.shariah_universe_path:
        return {"status": "UNKNOWN", "reason": "universe_not_configured"}

    path = Path(settings.shariah_universe_path)
    if not path.exists():
        return {"status": "UNKNOWN", "reason": "universe_file_missing"}

    dataset = json.loads(path.read_text(encoding="utf-8-sig"))
    record = next(
        (item for item in dataset.get("records", []) if str(item.get("ticker")) == symbol),
        None,
    )
    if not record:
        return {"status": "UNKNOWN", "reason": "legacy_universe_not_an_authority"}
    return {
        "status": "UNKNOWN",
        "reason": "legacy_universe_not_an_authority",
        "issuer_name": record.get("issuer_name"),
        "legacy_shariah_status": record.get("shariah_status"),
    }


def check_symbol(symbol: str) -> dict:
    """Check Shariah eligibility for a Malaysian ticker.

    Returns dict with 'status' (PASS/REJECT/UNKNOWN) and 'reason'.
    """
    if _HAS_SC_STORE:
        try:
            result = _check_via_sc_store(symbol)
            if result["reason"] != "no_approved_publication":
                return result
        except (sqlite3.Error, OSError):
            pass

    return _check_via_legacy_json(symbol)
