"""The bridge's market detector must agree with the backend's, symbol for symbol.

bridge/markets.py mirrors agents.shariah_agent.detect_market rather than importing it, so
the MCP process does not drag the screening stack into a stdio client. Mirroring risks
drift, and drift here is exactly the failure CLAUDE.md names: "a symbol cannot be screened
as Malaysian and priced as American".

So the copy is not trusted. This asserts agreement across a symbol table and across the
routing decision that depends on it.
"""

import pytest
from agents.shariah_agent import detect_market as backend_detect_market
from bridge.markets import MARKET_MY, MARKET_US, detect_market, shariah_route_for

SYMBOLS = [
    # Bursa codes -- numeric, including the ones this project has actually screened.
    "4197",
    "5225",
    "0026",
    "1155",
    "7113",
    # US tickers.
    "AAPL",
    "CVX",
    "MSFT",
    "BRK.B",
    "T",
    # Shapes that are neither, where "fail to US" must match the backend's choice.
    "",
    "   ",
    "aapl",
    "4197.KL",
    "MY.5225",
    "123ABC",
    "ABC123",
]


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_the_bridge_and_the_backend_agree(symbol):
    assert detect_market(symbol) == backend_detect_market(symbol), (
        f"bridge and backend disagree about {symbol!r}; a symbol screened as Malaysian "
        "and priced as American is the failure this parity check exists to prevent"
    )


def test_numeric_codes_are_malaysian_and_tickers_are_not():
    assert detect_market("4197") == MARKET_MY
    assert detect_market("AAPL") == MARKET_US


def test_the_shariah_surface_is_chosen_by_market():
    """The two markets have different authorities, and sending one to the other lies.

    /api/shariah/{ticker} is the SC Malaysia list only. Asking it about AAPL returns
    UNKNOWN / not_present_in_approved_publication -- true of the list, and useless as an
    answer about the company. The US verdict comes from the SEC EDGAR screen.
    """
    assert shariah_route_for("4197") == "shariah_status"
    assert shariah_route_for("AAPL") == "stock_explain"


def test_both_shariah_routes_exist_in_the_allowlist():
    from bridge.routes import ROUTES

    for symbol in ("4197", "AAPL"):
        assert shariah_route_for(symbol) in ROUTES


def test_the_narrator_tool_asks_the_right_surface_for_each_market():
    """Assert the request that gets built, per this repo's testing convention."""
    from bridge.tools import shariah_status

    class Recorder:
        def __init__(self):
            self.calls = []

        def call(self, route_name, **kwargs):
            self.calls.append((route_name, kwargs))
            return {"status": "OK", "route": route_name, "data": {}, "cached": False}

    my_client = Recorder()
    shariah_status(my_client, {"ticker": "4197"})
    assert my_client.calls[0][0] == "shariah_status"
    assert my_client.calls[0][1]["path_params"] == {"ticker": "4197"}

    us_client = Recorder()
    shariah_status(us_client, {"ticker": "aapl"})
    assert us_client.calls[0][0] == "stock_explain"
    # The US route is /stock/{symbol}/explain -- a ticker path param would build a
    # malformed URL, so the key changes with the route.
    assert us_client.calls[0][1]["path_params"] == {"symbol": "AAPL"}
