"""Append-only evidence trail for every trading decision.

Every decision produces a self-contained record with full provenance:
  - Which SC publication was consulted and what it said
  - Which market data was used and from where
  - Which quant strategy produced the signal
  - Each gate verdict with its reason
  - Which agent produced which information

Records are JSONL (one JSON object per line, greppable) appended to
data/evidence/decisions.jsonl. Each record is independently valid and
fully reproducible from the recorded inputs.

This module is write-only at the decision layer. Reading is for audit/API.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import REPO_ROOT


# Overridable so a test run can never write into the real trail. An evidence
# file containing fabricated test decisions is worse than no evidence file: the
# whole value of the trail is that every line in it actually happened. When the
# decision recorder was first wired up on 2026-09-22 a single suite run appended
# 26 synthetic decisions here, which is exactly the contamination this prevents.
#
# backend/run_all_tests.py sets EVIDENCE_DIR for the whole suite; individual
# tests that exercise the trail swap these module globals directly, which still
# works because both are read at call time.
EVIDENCE_DIR = Path(os.getenv("EVIDENCE_DIR") or (REPO_ROOT / "data" / "evidence"))
DECISIONS_FILE = EVIDENCE_DIR / "decisions.jsonl"


def build_decision_record(
    *,
    ticker: str,
    shariah_result: dict,
    quant_result: dict | None = None,
    risk_result: dict | None = None,
    account_result: dict | None = None,
    attractiveness: dict | None = None,
    final_decision: str,
    decision_reason: str,
    market_data_snapshot: dict | None = None,
) -> dict:
    # shariah_result may be the raw sc_malaysia_store.check_eligibility() dict
    # or the agents/shariah_agent.py wrapper around it (which nests the raw
    # dict under "details") -- accept either shape rather than require callers
    # to normalize it first.
    shariah_details = shariah_result.get("details", shariah_result)
    source_hash = shariah_result.get("source_document_hash") or shariah_details.get(
        "source_document_hash"
    )

    record = {
        "decision_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticker": ticker,
        "shariah": {
            "status": shariah_result.get("status"),
            "reason": shariah_result.get("reason"),
            "publication_id": shariah_result.get("publication_id"),
            "publication_date": shariah_result.get("publication_date"),
            "source_document_hash": source_hash,
            "provider": shariah_result.get("provider"),
        },
    }

    if quant_result is not None:
        record["quant"] = {
            "signal": quant_result.get("signal"),
            "strategy_id": quant_result.get("signal_source"),
            "reason": quant_result.get("reason"),
            "price": quant_result.get("price"),
            "bars": quant_result.get("bars"),
            "price_source": quant_result.get("price_source"),
            "as_of_date": quant_result.get("as_of_date"),
            "strategy_parameters": quant_result.get("strategy"),
        }

    if market_data_snapshot is not None:
        record["market_data"] = market_data_snapshot

    if risk_result is not None:
        record["risk"] = {
            "status": risk_result.get("status"),
            "checks": risk_result.get("checks"),
        }

    if account_result is not None:
        record["account"] = {
            "status": account_result.get("status"),
            "reason": account_result.get("reason"),
        }

    if attractiveness is not None:
        record["attractiveness"] = attractiveness

    record["decision"] = final_decision
    record["decision_reason"] = decision_reason

    return record


def append_decision(record: dict) -> Path:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, separators=(",", ":"), sort_keys=True)
    with open(DECISIONS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return DECISIONS_FILE


def read_decisions(*, limit: int = 50, ticker: str | None = None) -> list[dict]:
    if not DECISIONS_FILE.exists():
        return []
    records = []
    for line in DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ticker and record.get("ticker") != ticker:
            continue
        records.append(record)
    return records[-limit:]
