"""Trading mode capabilities for Amanah Trader."""

from alpaca_paper_adapter import ALPACA_ADAPTERS
from config import load_settings


# The markets execution routing knows about. Sourced here rather than re-derived so
# a new market has one place to appear.
MARKETS_WITH_EXECUTION_ROUTING = {"US", "MY"}

AGENT_TEAM = [
    {"name": "Shariah Research", "role": "Compliance universe and evidence"},
    {"name": "Market Data", "role": "Prices, history, and market context"},
    {"name": "Quant Strategy", "role": "Signals and strategy rules"},
    {"name": "Risk Manager", "role": "Hard limits and exposure controls"},
    {"name": "Portfolio Manager", "role": "Watchlist and allocation workflow"},
    {"name": "Execution", "role": "Paper execution behind locks"},
    {"name": "Audit Compliance", "role": "Decision and action trail"},
]


def mode_capabilities(mode: str) -> dict:
    capabilities = {
        "advisory": {
            "agents_can_recommend": True,
            "human_approval_required": False,
            "paper_execution_allowed": False,
            "autonomous_execution_allowed": False,
        },
        "approval": {
            "agents_can_recommend": True,
            "human_approval_required": True,
            "paper_execution_allowed": True,
            "autonomous_execution_allowed": False,
        },
        "autonomous_paper": {
            "agents_can_recommend": True,
            "human_approval_required": False,
            "paper_execution_allowed": True,
            "autonomous_execution_allowed": True,
        },
    }
    return capabilities[mode]


def execution_markets(settings) -> dict:
    """Which markets this instance will actually submit for, and through what.

    Exposed so a client does not have to duplicate the routing rule. The bridge relay
    used to hardcode "Malaysia cannot execute", which was true but was a second copy of
    a decision that lives here -- and a duplicated rule drifts the moment one copy
    changes. The backend is the authority on what it will execute; callers ask.
    """
    from broker_routing import adapter_for

    markets = {}
    for market in sorted(MARKETS_WITH_EXECUTION_ROUTING):
        routing = adapter_for({"shariah_market": market}, settings)
        markets[market] = {
            "adapter": routing.get("adapter"),
            "enabled": routing["status"] == "PASS" and settings.paper_execution_enabled,
            "reason": routing.get("reason"),
        }
    return markets


def trading_mode_status() -> dict:
    settings = load_settings()
    capabilities = mode_capabilities(settings.trading_mode)
    # This allowlist omitted both Alpaca adapters, so the reported capability disagreed
    # with what paper_execution.py actually does -- and production runs alpaca_mcp, so it
    # reported broker_submission False while being fully able to submit. Sourced from
    # ALPACA_ADAPTERS rather than re-spelled, so the two cannot drift again.
    submitting_adapters = ALPACA_ADAPTERS | {"fake", "moomoo"}
    broker_submission = (
        settings.paper_execution_enabled and settings.paper_execution_adapter in submitting_adapters
    )
    return {
        "trading_mode": settings.trading_mode,
        "execution_markets": execution_markets(settings),
        "paper_execution_enabled": settings.paper_execution_enabled,
        "paper_execution_adapter": settings.paper_execution_adapter,
        "effective_paper_execution_allowed": capabilities["paper_execution_allowed"]
        and settings.paper_execution_enabled,
        "capabilities": capabilities,
        "agent_team": AGENT_TEAM,
        "live_trading": False,
        "broker_submission": broker_submission,
    }
