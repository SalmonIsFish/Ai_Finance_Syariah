"""Local Shariah agent.

This agent is deterministic and fail-closed. It does not use an LLM.
"""

from shariah_gate import check_symbol
from sec_edgar_screen import check_us_symbol


def detect_market(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if normalized.isdigit():
        return "MY"
    return "US"


def _evaluate_malaysia(symbol: str) -> dict:
    result = check_symbol(symbol)
    gate_status = result.get("status", "UNKNOWN")
    if gate_status not in ("PASS", "REJECT", "UNKNOWN"):
        gate_status = "UNKNOWN"
    return {
        "agent": "shariah",
        "market": "MY",
        "provider": "SC_MY_APPROVED_PUBLICATION",
        "status": gate_status,
        "symbol": symbol,
        "reason": result.get("reason"),
        "publication_id": result.get("publication_id"),
        "publication_date": result.get("publication_date"),
        "details": result,
    }


def _evaluate_us(symbol: str) -> dict:
    result = check_us_symbol(symbol)
    status = "PASS" if result.get("status") == "COMPLIANT" else "REJECT"
    return {
        "agent": "shariah",
        "market": "US",
        "provider": "SEC_EDGAR",
        "status": status,
        "symbol": symbol,
        "reason": result.get("reason") or result.get("status"),
        "details": result,
    }


def evaluate_shariah(symbol: str) -> dict:
    normalized = symbol.strip().upper()
    market = detect_market(normalized)
    if market == "MY":
        return _evaluate_malaysia(normalized)
    return _evaluate_us(normalized)
