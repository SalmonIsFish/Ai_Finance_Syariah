"""Which market a symbol belongs to, and therefore which Shariah surface answers for it.

This mirrors `agents.shariah_agent.detect_market` rather than importing it. Importing the
real one would drag `shariah_gate` and `sec_edgar_screen` into the MCP process, which is a
stdio client that should not need the screening stack or its configuration to start.

Mirroring risks drift, and drift here is exactly the failure CLAUDE.md warns about -- "a
symbol cannot be screened as Malaysian and priced as American". So the copy is not trusted
on faith: `test_bridge_market_parity.py` asserts this function and the backend's agree
across a symbol table, and fails the moment they diverge.

**The two markets have different Shariah surfaces, and this is easy to get wrong.**
`/api/shariah/{ticker}` is the Securities Commission Malaysia SAC list *only*. Asking it
about a US symbol returns `UNKNOWN / not_present_in_approved_publication`, which is
correct and completely useless -- AAPL is not absent from the SC list because anything was
determined about AAPL. The US verdict comes from the SEC EDGAR screen at
`/stock/{symbol}/explain`.
"""

from __future__ import annotations

MARKET_MY = "MY"
MARKET_US = "US"


def detect_market(symbol: str) -> str:
    """MY for a numeric Bursa code, US otherwise. Mirrors agents.shariah_agent."""
    normalized = str(symbol or "").strip().upper()
    if normalized.isdigit():
        return MARKET_MY
    return MARKET_US


def shariah_route_for(symbol: str) -> str:
    """The route name that answers "is this security permissible" for this market.

    Sending a US ticker to the SC list route yields a verdict about the *list*, not about
    the company -- so the routing decision has to happen before the request, not after.
    """
    if detect_market(symbol) == MARKET_MY:
        return "shariah_status"
    return "stock_explain"
