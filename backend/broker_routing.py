"""Which broker submits this order, decided per market rather than per process.

`PAPER_EXECUTION_ADAPTER` is one process-wide setting, and that was fine while Alpaca was
the only route. It stopped being fine when Bursa was enabled, because Alpaca has no
Malaysian access of any kind and Moomoo is the only route to one.

This is the execution twin of `market_data.provider_for`, which already routes *pricing*
per market for the same reason and whose docstring makes the point that a symbol must not
be "screened as Malaysian and priced as American". The same sentence applies to
submission, and the market is decided by the same `detect_market` the Shariah gate routes
on, so the three cannot disagree about one symbol.

## What was actually wrong before, stated accurately

A Malaysian order was **not** silently sent to Alpaca. `alpaca_paper_adapter` carries
`SUPPORTED_REAL_MARKETS = {"US"}` and refuses a non-US approval with `UNSUPPORTED_MARKET`
before building anything, so the outcome was already safe. Two things were wrong:

1. The **status probe** was chosen by the same global flag, so a Malaysian order was
   gated on whether the *Alpaca* account was ready -- a precondition with nothing to do
   with the order.
2. The **reason** was misleading. "paper adapter currently supports US only" describes
   Alpaca's limits, not the system's decision, and a refusal whose stated cause is not
   the real one is the failure mode CLAUDE.md keeps returning to.

## Fail closed, and stay explicit

Malaysian execution defaults to `disabled`, not to `moomoo`. No Moomoo order has ever
reached a broker, and OpenD -- a stateful, logged-in daemon -- has no runbook for the
droplet, so routing Bursa orders to it by default would turn an unproven path on by
implication. Enabling it is `PAPER_EXECUTION_ADAPTER_MY=moomoo`, deliberately typed.
"""

# Which markets each adapter can actually submit for. Sourced from the adapters' own
# SUPPORTED_REAL_MARKETS where they have one, so this table cannot quietly claim more
# than the adapter does.
ADAPTER_MARKETS: dict[str, set] = {
    "alpaca": {"US"},
    "alpaca_mcp": {"US"},
    "moomoo": {"US", "MY"},
    "fake": {"US", "MY"},
    "disabled": set(),
}

# `fake` and `disabled` are not brokers, they are test and off switches, so they apply to
# every market at once. Splitting them per market would let a test configure `fake` and
# still reach a real adapter for the other market.
GLOBAL_ADAPTERS = {"fake", "disabled"}

# The single source of truth for this default. config.py reads it rather than spelling
# "disabled" a second time -- a deliberate-break check caught exactly that duplication:
# changing one of the two changed nothing, because the other was the one in force.
DEFAULT_MY_ADAPTER = "disabled"

# Reason codes, so callers can tell apart three refusals that need different fixes:
# nothing is configured anywhere, nothing is configured for THIS market, or what is
# configured cannot serve it. The first keeps the pre-existing execution status so the
# common "execution is off" case reads exactly as it always did.
CODE_NO_ADAPTER = "no_adapter_configured"
CODE_MARKET_NOT_CONFIGURED = "market_not_configured"
CODE_MARKET_UNSUPPORTED = "adapter_cannot_serve_market"
CODE_UNKNOWN_ADAPTER = "unknown_adapter"


def market_for(approval: dict) -> str:
    """The order's market: the recorded verdict's, falling back to the symbol's.

    `shariah_market` is written by the gate that screened it, so it is the authoritative
    answer. detect_market is the fallback for a row predating that column, and it is the
    same function the Shariah gate and market_data route on.
    """
    # Imported lazily and deliberately: this module is imported by config.py for the
    # default below, and agents.shariah_agent pulls in the whole screening stack, which
    # imports config right back.
    from agents.shariah_agent import detect_market

    recorded = str(approval.get("shariah_market") or "").strip().upper()
    if recorded:
        return recorded
    return detect_market(str(approval.get("symbol") or ""))


def configured_adapter_for_market(market: str, settings) -> str:
    """The adapter configured for this market, before any support check."""
    primary = str(getattr(settings, "paper_execution_adapter", "disabled") or "disabled")
    if primary in GLOBAL_ADAPTERS:
        return primary
    if str(market).upper() == "MY":
        return str(
            getattr(settings, "paper_execution_adapter_my", DEFAULT_MY_ADAPTER)
            or DEFAULT_MY_ADAPTER
        )
    return primary


def adapter_for(approval: dict, settings) -> dict:
    """Resolve the adapter for one order. Dict with a `status` key; fails closed.

    A PASS carries the adapter name and the market it was chosen for. A REJECT says which
    of the two went wrong -- nothing configured, or configured for a market it cannot
    serve -- because those need different fixes.
    """
    market = market_for(approval)
    adapter = configured_adapter_for_market(market, settings)

    if adapter == "disabled":
        primary = str(getattr(settings, "paper_execution_adapter", "disabled") or "disabled")
        globally_off = primary in GLOBAL_ADAPTERS
        return {
            "status": "REJECT",
            "code": CODE_NO_ADAPTER if globally_off else CODE_MARKET_NOT_CONFIGURED,
            "reason": (
                "PAPER_EXECUTION_ADAPTER is disabled"
                if globally_off
                else f"no execution adapter is configured for {market}; "
                f"set PAPER_EXECUTION_ADAPTER_{market} to enable it"
            ),
            "market": market,
            "adapter": adapter,
        }

    supported = ADAPTER_MARKETS.get(adapter)
    if supported is None:
        return {
            "status": "REJECT",
            "code": CODE_UNKNOWN_ADAPTER,
            "reason": f"unknown execution adapter '{adapter}'",
            "market": market,
            "adapter": adapter,
        }
    if market not in supported:
        return {
            "status": "REJECT",
            "code": CODE_MARKET_UNSUPPORTED,
            "reason": (
                f"adapter '{adapter}' cannot submit for {market}; it supports "
                f"{', '.join(sorted(supported)) or 'no market'}"
            ),
            "market": market,
            "adapter": adapter,
        }

    return {"status": "PASS", "adapter": adapter, "market": market}


def uses_alpaca(adapter: str) -> bool:
    """Whether this adapter name is one of the Alpaca transports."""
    from alpaca_paper_adapter import ALPACA_ADAPTERS

    return adapter in ALPACA_ADAPTERS
