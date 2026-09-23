"""The named, typed tool surface. This is what stops a model inventing a request.

A free "call this API" tool with a URL argument gives a model a field to fill in. A named
tool with validated parameters gives it an enum. Every tool here names routes from
`routes.ROUTES`; none accepts a path, a URL, or a raw query.

EVERY HANDLER RETURNS THE SAME ENVELOPE
---------------------------------------
    {"status": "OK" | ...,
     "facts":  {...},   # the backend's own fields, sanitized
     "render": "...",   # a deterministic text block built by format.py
     "prose_slot": {...},   # what a model may add, and what it must not contradict
     "source": {"route": ..., "cached": bool}}

`render` is the point. The model receives it and cannot rewrite it -- compose.py (Phase 2)
assembles the outgoing message with `render` above any narration. A model may explain a
verdict; it may never author one.

WHAT IS DELIBERATELY ABSENT
---------------------------
* `/paper/approval` and `/paper/execute` appear in no tool. Tap 1 and tap 2 are human
  actions carried out by the relay. If a bot could queue an order, the two-tap flow would
  be one tap with extra steps.
* `/news` and `/copilot/*` appear in no tool. They spend real OpenRouter money per call
  with no spend cap anywhere in the backend, and paying a model to summarise for another
  model is the worst version of that bill. `/api/research/{ticker}` is the deterministic
  feed the copilot reads, and it is free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from bridge import format as render
from bridge.markets import detect_market, shariah_route_for


@dataclass(frozen=True)
class Tool:
    """One named operation. `routes` is what the enforcement tests read."""

    name: str
    description: str
    params: dict
    routes: tuple[str, ...]
    writes: bool
    handler: Callable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _envelope(result: dict, *, facts: dict, rendered: str, prose_slot: dict | None = None) -> dict:
    """Wrap a client result in the one shape every tool returns."""
    return {
        "status": result.get("status", "UNKNOWN"),
        "facts": facts,
        "render": rendered,
        "prose_slot": prose_slot or {"allowed": True, "must_mention": [], "polarity": None},
        "source": {
            "route": result.get("route"),
            "fetched_at": _now(),
            "cached": bool(result.get("cached")),
        },
    }


def _failed(result: dict, *, label: str) -> dict:
    """A failed fetch renders as a refusal, never as an empty or reassuring answer."""
    reason = result.get("reason") or result.get("status")
    return _envelope(
        result,
        facts={},
        rendered=f"[{label}] unavailable: {reason}. No verdict can be reported.",
        prose_slot={"allowed": False, "must_mention": [], "polarity": None},
    )


# --- Shariah ------------------------------------------------------------------------


def shariah_status(client, args: dict) -> dict:
    """The permissibility of the *security*, from whichever authority covers its market."""
    ticker = str(args["ticker"]).strip().upper()
    route = shariah_route_for(ticker)
    key = "symbol" if route == "stock_explain" else "ticker"
    result = client.call(route, path_params={key: ticker})
    if result["status"] != "OK":
        return _failed(result, label="Shariah")

    facts = result["data"]
    if route == "stock_explain":
        rendered = render.render_shariah_us(facts)
        polarity = (facts.get("verdict") or {}).get("status")
    else:
        rendered = render.render_shariah(facts)
        polarity = (facts.get("verdict") or {}).get("status")
    return _envelope(
        result,
        facts=facts,
        rendered=rendered,
        # A model narrating this must name the right ticker and carry the right polarity.
        prose_slot={"allowed": True, "must_mention": [ticker], "polarity": polarity},
    )


def screen_detail(client, args: dict) -> dict:
    """Shariah, quant and attractiveness side by side -- never fused into one number."""
    ticker = str(args["ticker"]).strip().upper()
    result = client.call("screen_detail", path_params={"ticker": ticker})
    if result["status"] != "OK":
        return _failed(result, label="Screen")
    facts = result["data"]
    rendered = render.render_shariah(facts) + "\n\n" + render.render_quant(facts)
    polarity = (facts.get("shariah") or {}).get("status")
    return _envelope(
        result,
        facts=facts,
        rendered=rendered,
        prose_slot={"allowed": True, "must_mention": [ticker], "polarity": polarity},
    )


def universe(client, args: dict) -> dict:
    """The Malaysian securities eligible under the active SC publication."""
    query = {
        "shariah_status": str(args.get("shariah_status") or "PASS").upper(),
        "limit": int(args.get("limit") or 50),
        "offset": int(args.get("offset") or 0),
    }
    result = client.call("universe", query=query)
    if result["status"] != "OK":
        return _failed(result, label="Universe")
    return _envelope(result, facts=result["data"], rendered=render.render_universe(result["data"]))


def publications(client, args: dict) -> dict:
    """Every SC publication ingested, newest first, with which one is active."""
    result = client.call("publications")
    if result["status"] != "OK":
        return _failed(result, label="SC publications")
    return _envelope(
        result, facts=result["data"], rendered=render.render_publications(result["data"])
    )


def publication(client, args: dict) -> dict:
    """One SC publication and its record counts -- the document a verdict rests on."""
    publication_id = str(args["publication_id"]).strip()
    result = client.call("publication", path_params={"publication_id": publication_id})
    if result["status"] != "OK":
        return _failed(result, label="SC publication")
    facts = result["data"]
    row = facts.get("publication") or {}
    rendered = render.render_publications({"publications": [row]})
    rendered += (
        f"\n  {facts.get('compliant_count')} compliant, "
        f"{facts.get('non_compliant_count')} non-compliant "
        f"of {facts.get('security_count')} securities"
    )
    return _envelope(result, facts=facts, rendered=rendered)


def screens_latest(client, args: dict) -> dict:
    """The latest recorded US screen per symbol, from the append-only verdict log."""
    result = client.call("screens_log", query={"latest_only": "true"})
    if result["status"] != "OK":
        return _failed(result, label="Screens")
    rows = result["data"] if isinstance(result["data"], list) else []
    lines = ["[Screen log]", f"  {len(rows)} symbols"]
    for row in rows[:40]:
        lines.append(
            f"  {str(row.get('symbol') or '?'):<8} {str(row.get('status') or '?'):<14} "
            f"{row.get('provider')}  {row.get('screened_at')}"
        )
    return _envelope(result, facts={"rows": rows}, rendered="\n".join(lines))


# --- Quant --------------------------------------------------------------------------


def quant_signal(client, args: dict) -> dict:
    """The deterministic signal and its provenance. Reported, never invented."""
    ticker = str(args["ticker"]).strip().upper()
    result = client.call("quant_signal", path_params={"ticker": ticker})
    if result["status"] != "OK":
        return _failed(result, label="Quant")
    facts = result["data"]
    return _envelope(
        result,
        facts=facts,
        rendered=render.render_quant(facts),
        prose_slot={"allowed": True, "must_mention": [ticker], "polarity": None},
    )


def market_data(client, args: dict) -> dict:
    """Price history summary, including which provider actually answered."""
    symbol = str(args["symbol"]).strip().upper()
    result = client.call("market_data", path_params={"symbol": symbol})
    if result["status"] != "OK":
        return _failed(result, label="Market data")
    facts = result["data"]
    lines = [
        f"[Market data] {symbol}",
        f"  Market        {detect_market(symbol)}",
        f"  Source        {facts.get('source')}",
        f"  Latest close  {facts.get('latest_close')} on {facts.get('latest_date')}",
        f"  Bars          {facts.get('bars')} (min {facts.get('min_bars')})",
        f"  Enough        {facts.get('enough_history')}",
    ]
    return _envelope(result, facts=facts, rendered="\n".join(lines))


def research(client, args: dict) -> dict:
    """The aggregated deterministic research feed -- what the copilot summarises, unpaid."""
    ticker = str(args["ticker"]).strip().upper()
    result = client.call("research", path_params={"ticker": ticker})
    if result["status"] != "OK":
        return _failed(result, label="Research")
    facts = result["data"]
    rendered = render.render_shariah(facts) if facts.get("shariah") else f"[Research] {ticker}"
    return _envelope(
        result,
        facts=facts,
        rendered=rendered,
        prose_slot={"allowed": True, "must_mention": [ticker], "polarity": None},
    )


# --- Risk ---------------------------------------------------------------------------


def preview_order(client, args: dict) -> dict:
    """Run an order through the gate chain WITHOUT queuing it.

    The only write any LLM-reachable tool performs, and it is deliberately the one that
    decides nothing: /paper/preview runs the gates and records evidence, but
    `broker_submission` is hardcoded false and no queue entry exists afterwards. Queuing
    is tap 1, which is a human action carried out by the relay.

    The five risk percentages are informational -- the server recomputes them from live
    state -- so they are sent as zeros rather than as numbers a model guessed.
    """
    symbol = str(args["symbol"]).strip().upper()
    body = {
        "symbol": symbol,
        "side": str(args.get("side") or "BUY").upper(),
        "quantity": int(args["quantity"]),
        "price": args.get("price"),
        "position_pct": 0.0,
        "total_exposure_pct": 0.0,
        "loss_per_trade_pct": 0.0,
        "daily_loss_pct": 0.0,
        "orders_today": 0,
        "asset_class": "equity",
    }
    result = client.call("paper_preview", body=body)
    if result["status"] != "OK":
        return _failed(result, label="Preview")

    payload = result["data"] or {}
    preview = payload.get("preview") or {}
    status = preview.get("status")
    lines = [
        f"[Preview] {symbol} {body['side']} x{body['quantity']} -> {status}",
        f"  Notional      {preview.get('notional')}",
        "",
        "  This is a preview. It has queued nothing and submitted nothing; only the",
        "  owner can queue it, and only from the proposal card in Telegram.",
    ]
    if status != "READY_FOR_APPROVAL":
        lines.append("")
        lines.append(
            render.render_blockers(preview.get("blocker_messages"), preview.get("blockers"))
        )
    return _envelope(
        result,
        facts=payload,
        rendered="\n".join(lines),
        prose_slot={"allowed": True, "must_mention": [symbol], "polarity": None},
    )


def risk_limits(client, args: dict) -> dict:
    """The configured limits. Config values, not positions."""
    result = client.call("risk_limits")
    if result["status"] != "OK":
        return _failed(result, label="Risk limits")
    limits = (result["data"] or {}).get("limits") or {}
    lines = ["[Risk limits]"] + [f"  {key:<26}{value}" for key, value in sorted(limits.items())]
    return _envelope(result, facts=result["data"], rendered="\n".join(lines))


def risk_snapshot(client, args: dict) -> dict:
    """Orders today and period losses. A non-finite loss is BLOCKING, never a dash."""
    result = client.call("risk_snapshot")
    if result["status"] != "OK":
        return _failed(result, label="Risk")
    facts = result["data"]
    return _envelope(result, facts=facts, rendered=render.render_risk(facts))


def paper_account(client, args: dict) -> dict:
    """Broker account state. Display only -- gates size off the configured equity baseline."""
    result = client.call("paper_account")
    if result["status"] != "OK":
        return _failed(result, label="Account")
    facts = result["data"]
    lines = [
        "[Account]",
        f"  Status        {facts.get('status')}",
        f"  Environment   {facts.get('environment')}",
        f"  Type          {facts.get('account_type')}",
        f"  Suffix        {facts.get('account_suffix')}",
        f"  Cash          {facts.get('cash')}",
        f"  Equity        {facts.get('equity')}",
        "  Note          display only; gates size off PAPER_ACCOUNT_EQUITY",
    ]
    return _envelope(result, facts=facts, rendered="\n".join(lines))


def positions(client, args: dict) -> dict:
    """Local equity ledger with per-position limit status. Options are not booked here."""
    result = client.call("positions")
    if result["status"] != "OK":
        return _failed(result, label="Positions")
    facts = result["data"]
    rows = facts.get("positions") or []
    lines = [
        "[Positions]",
        f"  Count         {facts.get('position_count')}",
        f"  Exposure      {facts.get('total_exposure_pct')}% ({facts.get('total_exposure_status')})",
    ]
    for row in rows:
        lines.append(
            f"  {str(row.get('symbol') or '?'):<8} qty {row.get('quantity')} @ "
            f"{row.get('average_cost')}  {row.get('position_limit_status')}"
        )
    return _envelope(result, facts=facts, rendered="\n".join(lines))


def portfolio(client, args: dict) -> dict:
    """Valued portfolio snapshot with exposure against the configured limits."""
    result = client.call("portfolio")
    if result["status"] != "OK":
        return _failed(result, label="Portfolio")
    facts = result["data"]
    lines = [
        "[Portfolio]",
        f"  Valuation     {facts.get('valuation_status')}",
        f"  Positions     {facts.get('position_count')}",
        f"  Market value  {facts.get('market_value')}",
        f"  Unrealized    {facts.get('unrealized_pnl')}",
        f"  Exposure      {facts.get('total_exposure_pct')}%",
    ]
    return _envelope(result, facts=facts, rendered="\n".join(lines))


# --- Portfolio steward --------------------------------------------------------------


def compliance_snapshot(client, args: dict) -> dict:
    """Holdings compliance and the disposal clock.

    Uses the CACHED read. The uncached path advances and persists the clock and belongs
    to the scheduled job (Phase 3), not to a bot answering a question -- a chatty bot
    must not be what drives a religious deadline forward.
    """
    result = client.read_compliance()
    if result["status"] != "OK":
        return _failed(result, label="Holdings compliance")
    facts = result["data"]
    return _envelope(result, facts=facts, rendered=render.render_compliance(facts))


def portfolio_history(client, args: dict) -> dict:
    """Locally accumulated portfolio value snapshots."""
    result = client.call("portfolio_history")
    if result["status"] != "OK":
        return _failed(result, label="Portfolio history")
    facts = result["data"]
    rows = facts.get("snapshots") or []
    lines = ["[Portfolio history]", f"  {len(rows)} snapshots"]
    for row in rows[-10:]:
        lines.append(
            f"  {row.get('captured_at') or row.get('created_at')}  {row.get('market_value')}"
        )
    return _envelope(result, facts=facts, rendered="\n".join(lines))


# --- Auditor ------------------------------------------------------------------------


def evidence(client, args: dict) -> dict:
    """Recorded decisions for a ticker.

    Defaults to `source == "preview"` -- orders the owner actually put through the gate
    chain. A watchlist scan writes up to 30 records per run, so including scans by default
    would drown the thing the auditor exists to find in noise it cannot be separated from
    afterwards.
    """
    ticker = str(args["ticker"]).strip().upper()
    limit = int(args.get("limit") or 20)
    result = client.call("evidence", path_params={"ticker": ticker}, query={"limit": limit})
    if result["status"] != "OK":
        return _failed(result, label="Evidence")
    facts = dict(result["data"] or {})
    if not bool(args.get("include_scans")):
        kept = [d for d in (facts.get("decisions") or []) if d.get("source") == "preview"]
        facts["decisions"] = kept
        facts["count"] = len(kept)
        facts["filtered"] = "source=preview (pass include_scans to see watchlist scans)"
    return _envelope(result, facts=facts, rendered=render.render_evidence(facts))


def execution_audit(client, args: dict) -> dict:
    """What happened to orders: submitted, filled, locked, or refused and why."""
    result = client.call("execution_audit")
    if result["status"] != "OK":
        return _failed(result, label="Execution audit")
    facts = result["data"]
    counts = facts.get("counts") or {}
    lines = ["[Execution audit]"] + [f"  {key:<24}{value}" for key, value in sorted(counts.items())]
    for row in (facts.get("locked_or_rejected") or [])[:10]:
        lines.append(
            f"  #{row.get('id')} {row.get('symbol')} {row.get('execution_status')}: "
            f"{row.get('execution_message')}"
        )
    return _envelope(result, facts=facts, rendered="\n".join(lines))


def approvals(client, args: dict) -> dict:
    """The approval queue, newest first."""
    result = client.call("approvals")
    if result["status"] != "OK":
        return _failed(result, label="Approvals")
    rows = result["data"] if isinstance(result["data"], list) else []
    lines = ["[Approvals]", f"  {len(rows)} entries"]
    for row in rows[:15]:
        lines.append(
            f"  #{row.get('id')} {row.get('symbol')} {row.get('side')} x{row.get('quantity')} "
            f"-> {row.get('approval_status')} / {row.get('execution_status')}"
        )
    return _envelope(result, facts={"rows": rows}, rendered="\n".join(lines))


def audit_log(client, args: dict) -> dict:
    """The raw append-only event ledger."""
    result = client.call("audit_log")
    if result["status"] != "OK":
        return _failed(result, label="Audit")
    rows = result["data"] if isinstance(result["data"], list) else []
    lines = ["[Audit log]", f"  {len(rows)} events"]
    for row in rows[:20]:
        lines.append(f"  {row.get('created_at')}  {row.get('event_type')}")
    return _envelope(result, facts={"rows": rows}, rendered="\n".join(lines))


_TICKER = {"ticker": {"type": "string", "description": "Ticker or Bursa code, e.g. AAPL or 4197"}}
_SYMBOL = {"symbol": {"type": "string", "description": "Ticker or Bursa code"}}

_TOOL_LIST = (
    Tool(
        "shariah_status",
        "Is this security permissible, and on whose authority? Routes to the SC Malaysia "
        "list for a Bursa code and to the SEC EDGAR screen for a US ticker.",
        _TICKER,
        ("shariah_status", "stock_explain"),
        False,
        shariah_status,
    ),
    Tool(
        "screen_detail",
        "Shariah verdict, quant signal and attractiveness for one security, reported "
        "separately and never combined into a single score.",
        _TICKER,
        ("screen_detail",),
        False,
        screen_detail,
    ),
    Tool(
        "universe",
        "Malaysian securities eligible under the active SC publication.",
        {
            "shariah_status": {"type": "string", "enum": ["PASS", "REJECT", "ALL"]},
            "limit": {"type": "integer"},
            "offset": {"type": "integer"},
        },
        ("universe",),
        False,
        universe,
    ),
    Tool(
        "publications",
        "Every SC publication ingested, and which one is currently active.",
        {},
        ("publications",),
        False,
        publications,
    ),
    Tool(
        "publication",
        "One SC publication, its document hash and its record counts.",
        {"publication_id": {"type": "string"}},
        ("publication",),
        False,
        publication,
    ),
    Tool(
        "screens_latest",
        "The latest recorded US screening verdict per symbol.",
        {},
        ("screens_log",),
        False,
        screens_latest,
    ),
    Tool(
        "quant_signal",
        "The deterministic quant signal, its price source and bar count.",
        _TICKER,
        ("quant_signal",),
        False,
        quant_signal,
    ),
    Tool(
        "market_data",
        "Price history summary and which provider answered.",
        _SYMBOL,
        ("market_data",),
        False,
        market_data,
    ),
    Tool(
        "research",
        "Aggregated deterministic research context for a ticker.",
        _TICKER,
        ("research",),
        False,
        research,
    ),
    Tool(
        "preview_order",
        "Run an equity order through the gate chain without queuing it. Queuing and "
        "executing are the owner's, from the proposal card.",
        {
            "symbol": {"type": "string"},
            "quantity": {"type": "integer"},
            "side": {"type": "string", "enum": ["BUY", "SELL"]},
            "price": {"type": "number"},
        },
        ("paper_preview",),
        True,
        preview_order,
    ),
    Tool("risk_limits", "The configured risk limits.", {}, ("risk_limits",), False, risk_limits),
    Tool(
        "risk_snapshot",
        "Orders placed today and period losses. An unbounded loss is blocking.",
        {},
        ("risk_snapshot",),
        False,
        risk_snapshot,
    ),
    Tool(
        "paper_account",
        "Broker paper account state. Display only.",
        {},
        ("paper_account",),
        False,
        paper_account,
    ),
    Tool(
        "positions",
        "Local equity positions with per-position limit status.",
        {},
        ("positions",),
        False,
        positions,
    ),
    Tool(
        "portfolio", "Valued portfolio snapshot and exposure.", {}, ("portfolio",), False, portfolio
    ),
    Tool(
        "compliance_snapshot",
        "Holdings compliance and disposal deadlines for non-compliant holdings.",
        {},
        ("portfolio_compliance",),
        False,
        compliance_snapshot,
    ),
    Tool(
        "portfolio_history",
        "Portfolio value snapshots over time.",
        {},
        ("portfolio_history",),
        False,
        portfolio_history,
    ),
    Tool(
        "evidence",
        "Decisions this system recorded for a ticker. Defaults to real considered orders; "
        "pass include_scans to add watchlist scan records.",
        {
            **_TICKER,
            "include_scans": {"type": "boolean"},
            "limit": {"type": "integer"},
        },
        ("evidence",),
        False,
        evidence,
    ),
    Tool(
        "execution_audit",
        "Order outcomes: submitted, filled, locked or refused, with the reason.",
        {},
        ("execution_audit",),
        False,
        execution_audit,
    ),
    Tool("approvals", "The approval queue.", {}, ("approvals",), False, approvals),
    Tool("audit_log", "The append-only event ledger.", {}, ("audit_log",), False, audit_log),
)

TOOLS: dict[str, Tool] = {tool.name: tool for tool in _TOOL_LIST}


ROLE_TOOLS: dict[str, frozenset[str]] = {
    "shariah_narrator": frozenset(
        {
            "shariah_status",
            "screen_detail",
            "universe",
            "publication",
            "publications",
            "screens_latest",
        }
    ),
    "quant": frozenset({"quant_signal", "market_data", "research", "screen_detail"}),
    # Option contracts are blocked system-wide pending a scholarly ruling, so the trader
    # proposes equities only. The option selection tool is deliberately not wired: it
    # would return the determination and nothing else.
    "trader": frozenset({"preview_order", "screen_detail", "quant_signal", "market_data"}),
    "risk_officer": frozenset(
        {"risk_limits", "risk_snapshot", "positions", "paper_account", "portfolio"}
    ),
    "portfolio_steward": frozenset({"compliance_snapshot", "portfolio_history", "positions"}),
    "auditor": frozenset({"evidence", "execution_audit", "approvals", "audit_log"}),
    "sc_watcher": frozenset({"publications", "publication", "universe"}),
    # No backend tools at all, deliberately. A chief of staff with data access sees a
    # choice point in code and can arbitrate; with none, it can only route and assemble.
    "chief_of_staff": frozenset(),
}


def tools_for(role: str) -> tuple[Tool, ...]:
    """The tools one role may call. Unknown role gets nothing, not everything."""
    names = ROLE_TOOLS.get(role, frozenset())
    return tuple(TOOLS[name] for name in sorted(names))


def call_tool(client, role: str, name: str, args: dict | None = None) -> dict:
    """Invoke a tool on behalf of a role, refusing anything outside that role's set."""
    if name not in TOOLS:
        return {
            "status": "REJECT",
            "reason": "unknown_tool",
            "render": f"No tool named '{name}'.",
            "facts": {},
        }
    if name not in ROLE_TOOLS.get(role, frozenset()):
        return {
            "status": "REJECT",
            "reason": "tool_not_available_to_role",
            "render": f"The {role} role may not call '{name}'.",
            "facts": {},
        }
    return TOOLS[name].handler(client, args or {})
