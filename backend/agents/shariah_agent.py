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


def _evaluate_us(symbol: str, methodology: str | None = None, price_lookup=None) -> dict:
    # The order gate never passes a methodology, so it always screens on SC SAC.
    if methodology is None:
        result = check_us_symbol(symbol)
    else:
        result = check_us_symbol(symbol, methodology=methodology, price_lookup=price_lookup)
    raw = result.get("status")
    if raw == "COMPLIANT":
        status = "PASS"
    elif raw == "NON_COMPLIANT":
        status = "REJECT"
    else:
        status = "UNKNOWN"
    return {
        "agent": "shariah",
        "market": "US",
        "provider": "SEC_EDGAR",
        "status": status,
        "symbol": symbol,
        "reason": result.get("reason") or result.get("status"),
        "details": result,
    }


def evaluate_shariah(symbol: str, *, methodology: str | None = None, price_lookup=None) -> dict:
    """`methodology`/`price_lookup` serve the monitor's AAOIFI comparison view only;
    the order gate calls this with neither. Malaysian securities are always settled
    by the SC list."""
    normalized = symbol.strip().upper()
    market = detect_market(normalized)
    if market == "MY":
        return _evaluate_malaysia(normalized)
    return _evaluate_us(normalized, methodology, price_lookup)
