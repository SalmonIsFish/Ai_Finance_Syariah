"""Read-only application-service layer for the Malaysian screening/eligibility API.

Route handlers in local_api.py stay thin and delegate here. These functions
never decide eligibility themselves -- each one calls straight into the same
deterministic domain modules the trading gate chain uses (shariah_gate,
sc_malaysia_store, agents.quant_agent, confidence, risk_checks, evidence,
vault_indexer) and shapes their output into one consistent response
contract. No Shariah decision, risk calculation, or quant scoring logic is
duplicated or reimplemented here -- if a rule needs to change, it changes in
exactly one domain module, not here and there too.

This module is read-only: nothing here writes to the SC publication store,
approves anything, or submits an order. It is safe to call from any number
of unauthenticated GET routes without touching the trading gate chain's
state at all.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import confidence
import market_data
import sc_malaysia_store
import shariah_gate
import vault_indexer
from agents.quant_agent import evaluate_quant
from config import load_settings


def _shariah_verdict(ticker: str) -> dict:
    """The one Shariah verdict shape every endpoint below reuses.

    Routes through shariah_gate.check_symbol exactly like the trading gate
    chain does -- this can never disagree with what /paper/preview computes
    for the same ticker at the same moment.
    """
    result = shariah_gate.check_symbol(ticker)
    return {
        "status": result.get("status"),
        "reason_code": result.get("reason"),
        "publication_id": result.get("publication_id"),
        "publication_date": result.get("publication_date"),
        "source_document_hash": result.get("source_document_hash"),
        "issuer_name": result.get("issuer_name"),
        "board": result.get("board"),
        "sector": result.get("sector"),
    }


_UNIVERSE_STATUS_FILTERS = {"PASS", "REJECT", "ALL"}


def universe_list(
    connection: sqlite3.Connection,
    *,
    limit: int = 200,
    offset: int = 0,
    shariah_status: str = "PASS",
) -> dict:
    """Securities in the currently active, approved SC publication.

    Empty (not an error) when nothing is active -- this reads only the
    approved+activated publication, never the legacy JSON fallback and
    never an unapproved 'pending' or 'needs_reconciliation' publication.

    ``shariah_status`` filters which authoritative rows come back: PASS
    (default, preserves the original COMPLIANT-only behavior), REJECT
    (NON_COMPLIANT rows), or ALL (both). Every returned row carries a
    normalized ``verdict`` of "PASS" or "REJECT" alongside the raw
    ``shariah_status`` so callers never interpret the raw SC enum
    themselves. Raises ValueError for anything else -- callers must fail
    predictably, not silently fall back to a default.
    """
    normalized_filter = shariah_status.strip().upper()
    if normalized_filter not in _UNIVERSE_STATUS_FILTERS:
        raise ValueError(f"invalid shariah_status: {shariah_status!r}")

    pub = sc_malaysia_store.get_active_publication(connection)
    if pub is None:
        return {"active_publication": None, "count": 0, "securities": []}

    securities = sc_malaysia_store.publication_securities(connection, pub["id"])
    tagged = []
    for s in securities:
        row = dict(s)
        row["verdict"] = "PASS" if row["shariah_status"] == "COMPLIANT" else "REJECT"
        tagged.append(row)

    if normalized_filter == "PASS":
        matched = [r for r in tagged if r["verdict"] == "PASS"]
    elif normalized_filter == "REJECT":
        matched = [r for r in tagged if r["verdict"] == "REJECT"]
    else:
        matched = tagged

    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    return {
        "active_publication": {
            "id": pub["id"],
            "publication_date": pub["publication_date"],
            "activated_at": pub["activated_at"],
        },
        "count": len(matched),
        "offset": offset,
        "limit": limit,
        "shariah_status_filter": normalized_filter,
        "securities": matched[offset : offset + limit],
    }


def universe_ticker(connection: sqlite3.Connection, ticker: str) -> dict:
    normalized = ticker.strip().upper()
    verdict = sc_malaysia_store.check_eligibility(connection, normalized)
    return {"ticker": normalized, "verdict": verdict}


def universe_publications(connection: sqlite3.Connection) -> dict:
    pubs = sc_malaysia_store.list_publications(connection)
    return {"publications": [dict(p) for p in pubs]}


def publication_detail(connection: sqlite3.Connection, publication_id: str) -> dict | None:
    pub = sc_malaysia_store.get_publication(connection, publication_id)
    if pub is None:
        return None
    securities = sc_malaysia_store.publication_securities(connection, publication_id)
    compliant = [s for s in securities if s["shariah_status"] == "COMPLIANT"]
    non_compliant = [s for s in securities if s["shariah_status"] != "COMPLIANT"]
    return {
        "publication": dict(pub),
        "security_count": len(securities),
        "compliant_count": len(compliant),
        "non_compliant_count": len(non_compliant),
    }


def screen_ticker(ticker: str) -> dict:
    """The composed, single-response eligibility view for one ticker.

    Combines the independently-computed Shariah verdict and quant
    attractiveness into one payload -- but never combines them into a single
    number. `eligibility.status` is derived from Shariah alone: a high quant
    score can never turn a REJECT or UNKNOWN into ELIGIBLE, and a low quant
    score can never turn a PASS into BLOCKED. See confidence.py and
    agent_coordinator.py, which enforce the same separation in the trading
    path this mirrors.
    """
    normalized = ticker.strip().upper()
    shariah = _shariah_verdict(normalized)

    quant_result = evaluate_quant(normalized, allow_fallback=True, allow_stale_cache=True)
    quant = {
        "signal": quant_result.get("signal"),
        "reason_code": quant_result.get("reason"),
        "strategy_id": quant_result.get("signal_source"),
        "price": quant_result.get("price"),
        "price_source": quant_result.get("price_source"),
        "as_of_date": quant_result.get("as_of_date"),
        "bars": quant_result.get("bars"),
    }

    end_date = date.today()
    start_date = end_date - timedelta(days=320)
    bars, _source = market_data.fetch_eod_prices(
        normalized,
        start_date.isoformat(),
        end_date.isoformat(),
        allow_fallback=True,
        allow_stale_cache=True,
    )
    attractiveness = confidence.score_attractiveness(
        strategy_result=quant_result.get("strategy") or {},
        bars=bars,
        risk_headroom=None,
    )

    eligibility_status = "ELIGIBLE" if shariah["status"] == "PASS" else "BLOCKED"
    eligibility_reason = (
        "shariah_pass" if shariah["status"] == "PASS" else f"shariah_{shariah['status'].lower()}"
    )

    return {
        "ticker": normalized,
        "shariah": shariah,
        "quant": quant,
        "attractiveness": attractiveness,
        "eligibility": {"status": eligibility_status, "reason_code": eligibility_reason},
    }


def risk_limits() -> dict:
    """The currently configured hard risk limits. Informational only -- a
    real PASS/REJECT risk verdict requires order-specific inputs (position
    size, existing exposure) that only exist at /paper/preview time.

    Reports the actual configured values from settings (which may be
    overridden via .env), not risk_checks.default_limits()'s hardcoded
    fallback constants -- the two only coincide when nothing has been
    overridden.
    """
    settings = load_settings()
    return {
        "limits": {
            "max_position_pct": settings.max_position_pct,
            "max_total_exposure_pct": settings.max_total_exposure_pct,
            "max_loss_per_trade_pct": settings.max_loss_per_trade_pct,
            "max_daily_loss_pct": settings.max_daily_loss_pct,
            "max_weekly_loss_pct": settings.max_weekly_loss_pct,
            "max_orders_per_day": settings.max_orders_per_day,
            "max_sector_exposure_pct": settings.max_sector_exposure_pct,
        }
    }


def evidence_for_ticker(ticker: str, *, limit: int = 20) -> dict:
    import evidence as evidence_module

    normalized = ticker.strip().upper()
    records = evidence_module.read_decisions(limit=limit, ticker=normalized)
    return {"ticker": normalized, "count": len(records), "decisions": records}


def knowledge_search(vault_path: str, query: str, *, limit: int = 20) -> dict:
    index = vault_indexer.VaultIndex()
    build_result = index.build(vault_path)
    if build_result.get("status") != "indexed":
        return {"status": build_result.get("status", "error"), "results": []}
    results = index.search(query, limit=limit)
    return {"status": "ok", "count": len(results), "results": results}


def knowledge_note(vault_path: str, relative_path: str) -> dict | None:
    index = vault_indexer.VaultIndex()
    build_result = index.build(vault_path)
    if build_result.get("status") != "indexed":
        return None
    note = index.get_note(relative_path)
    return note


def research_intelligence_for_ticker(ticker: str, vault_path: str | None) -> dict:
    """Aggregates all read-only deterministic and knowledge context for a security."""
    normalized = ticker.strip().upper()
    screen_data = screen_ticker(normalized)
    
    knowledge_context = []
    if vault_path:
        search_res = knowledge_search(vault_path, normalized, limit=5)
        for res in search_res.get("results", []):
            knowledge_context.append({
                "source_type": "vault",
                "path": res.get("filename"),
                "title": res.get("frontmatter", {}).get("title") or res.get("filename"),
                "relevance": res.get("excerpt"),
                "retrieved_at": screen_data["as_of"],
                "content": res.get("body", "")  # vault_indexer doesn't return full body in search by default, but it's available via get_note
            })
            
            # Optionally fetch full body if needed, but let's stick to search results to save tokens,
            # or fetch full note if required. The vault index search result might have `content`?
            note_full = knowledge_note(vault_path, res.get("filename", ""))
            if note_full:
                knowledge_context[-1]["content"] = note_full.get("body", "")

    evidence_data = evidence_for_ticker(normalized, limit=10)
    
    unified_evidence = []
    # Add publication timeline event
    unified_evidence.append({
        "source_type": "sc_publication",
        "timestamp": screen_data["shariah"].get("publication_date", ""),
        "detail": f"SC Publication {screen_data['shariah'].get('publication_id', 'UNKNOWN')} established status as {screen_data['shariah']['status']}"
    })
    
    # Add quant timeline event
    unified_evidence.append({
        "source_type": "quant_signal",
        "timestamp": screen_data["quant"].get("as_of_date", ""),
        "detail": f"Signal {screen_data['quant'].get('signal', 'UNKNOWN')} generated via {screen_data['quant'].get('strategy_id', 'UNKNOWN')}"
    })
    
    # Add recent trading decisions
    for dec in evidence_data.get("decisions", []):
        unified_evidence.append({
            "source_type": "trade_decision",
            "timestamp": dec.get("timestamp", ""),
            "detail": f"Decision: {dec.get('decision', dec.get('final_decision'))}, Reason: {dec.get('decision_reason', '')}"
        })
        
    return {
        "ticker": normalized,
        "identity": {
            "issuer": screen_data["shariah"].get("issuer_name"),
            "board": screen_data["shariah"].get("board"),
            "sector": screen_data["shariah"].get("sector")
        },
        "shariah": screen_data["shariah"],
        "quant": screen_data["quant"],
        "attractiveness": screen_data["attractiveness"],
        "risk_context": risk_limits()["limits"],
        "knowledge": knowledge_context,
        "evidence": sorted(unified_evidence, key=lambda x: x["timestamp"], reverse=True)
    }

