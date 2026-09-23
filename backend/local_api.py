"""Minimal local API for the paper-trading dashboard."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, APIRouter
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

import portfolio_metrics
import copilot_api
from agent_coordinator import evaluate_candidate

# Imported as a module, not `from ... import record_decision`, so the swappable
# seam stays swappable: tests replace agent_coordinator.record_decision, and a
# from-import would bind the original here and ignore the swap.
import agent_coordinator

# Imported as a module, not `from ... import record_decision`, so the swappable
# seam stays swappable: tests replace agent_coordinator.record_decision, and a
# from-import would bind the original here and quietly ignore the swap.
from agents.risk_engine import evaluate_risk
from agents.shariah_agent import detect_market, evaluate_shariah
from alpaca_market_data import fetch_news
from alpaca_paper_adapter import (
    ALPACA_ADAPTERS,
    alpaca_credentials,
    check_alpaca_status,
    fetch_broker_positions,
)
from approval_queue import ensure_approval_queue, get_approval, list_approvals, record_approval
from approval_workflow import approve_candidate
from config import allowed_origins, load_settings
from market_data import summarize_history
from moomoo_status import check_moomoo_status
from news_summarizer import attach_ai_summaries
from opportunity_scanner import scan_opportunities
from option_permissibility import REASON_NOT_PERMITTED as REASON_OPTION_NOT_PERMITTED
from option_permissibility import determination_summary as option_determination_summary
from option_strategy_api import propose_option_strategy
import screening_api
from paper_execution import (
    execute_paper_order,
    reconcile_submitted_paper_order,
    validate_approval_payload_for_execution,
)
from sector_concentration import check_sector_concentration, sectors_for_symbols
from portfolio_store import (
    ensure_portfolio_tables,
    list_portfolio_snapshots,
    open_position_quantity,
    period_realized_pnl,
    portfolio_snapshot,
    record_portfolio_snapshot,
    sync_filled_order,
)
from shariah_candidate import build_shariah_candidate
from shariah_screen_store import (
    ensure_shariah_screen_tables,
    latest_screen_per_symbol,
    list_shariah_screens,
)
from shariah_explain import explain_symbol
import holdings_compliance
from shariah_trace import describe_approval
from trading_modes import execution_markets, trading_mode_status
from watchlist_store import (
    ensure_watchlist_tables,
    get_watchlist_settings,
    list_latest_scan_results,
    latest_scan_snapshot,
    list_alert_events,
    save_opportunity_scan,
    save_watchlist_settings,
)


BACKEND_DIR = Path(__file__).resolve().parent
DB_PATH = BACKEND_DIR / "paper_trading.db"
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import auth


class P3ProposalRequest(BaseModel):
    ticker: str
    side: str


class P3ApprovalRequest(BaseModel):
    pass


class P3PortfolioCreateRequest(BaseModel):
    name: str
    initial_cash: float


security = HTTPBasic()


def get_current_actor(credentials: HTTPBasicCredentials = Depends(security)) -> auth.Actor:
    try:
        return auth.authenticate_credentials(credentials.username, credentials.password)
    except auth.AuthenticationError:
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )


def get_owner_actor(actor: auth.Actor = Depends(get_current_actor)) -> auth.Actor:
    if actor.username != "project_owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return actor


def get_propose_actor(actor: auth.Actor = Depends(get_current_actor)) -> auth.Actor:
    try:
        auth.authorize(actor, "propose")
    except auth.AuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return actor


def get_approve_actor(actor: auth.Actor = Depends(get_current_actor)) -> auth.Actor:
    try:
        auth.authorize(actor, "approve")
    except auth.AuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return actor


def get_execute_actor(actor: auth.Actor = Depends(get_current_actor)) -> auth.Actor:
    try:
        auth.authorize(actor, "execute")
    except auth.AuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return actor


app = FastAPI(title="Amanah Trader Local API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


DASHBOARD_V2_DIR = Path(__file__).resolve().parent.parent / "dashboard-v2" / "dist"
dashboard_router = APIRouter(dependencies=[Depends(get_owner_actor)])


@dashboard_router.get("/dashboard/")
def get_dashboard_index():
    return FileResponse(DASHBOARD_V2_DIR / "index.html")


@dashboard_router.get("/dashboard/{path:path}")
def get_dashboard_file(path: str):
    file_path = DASHBOARD_V2_DIR / path
    resolved_path = file_path.resolve()
    if not resolved_path.is_relative_to(DASHBOARD_V2_DIR.resolve()):
        raise HTTPException(status_code=404, detail="Not Found")
    if resolved_path.is_file():
        return FileResponse(resolved_path)
    return FileResponse(DASHBOARD_V2_DIR / "index.html")


app.include_router(dashboard_router)


def db() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        "CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL)"
    )
    ensure_approval_queue(connection)
    ensure_watchlist_tables(connection)
    ensure_portfolio_tables(connection)
    ensure_shariah_screen_tables(connection)
    connection.commit()
    return connection


def broker_submission_configured(settings) -> bool:
    return settings.paper_execution_enabled and settings.paper_execution_adapter in {
        "fake",
        "moomoo",
        "alpaca",
        "alpaca_mcp",
    }


class PaperPreviewRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    side: str = "BUY"
    quantity: int = Field(gt=0, le=1_000_000)
    price: float | None = Field(default=None, gt=0)
    position_pct: float = Field(ge=0)
    total_exposure_pct: float = Field(ge=0)
    loss_per_trade_pct: float = Field(ge=0)
    daily_loss_pct: float = Field(ge=0)
    orders_today: int = Field(ge=0)
    test_fixture: bool = False
    asset_class: str = "equity"
    option_contract: dict | None = None


class PaperApprovalRequest(BaseModel):
    preview: dict
    approved: bool


class PaperExecutionRequest(BaseModel):
    confirmation_phrase: str | None = None


class WatchlistRequest(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=30)
    alert_threshold_pct: float = Field(ge=0.1, le=20.0)


PAPER_EXECUTION_CONFIRMATION = "EXECUTE PAPER"
DEFAULT_SCAN_THROTTLE_MINUTES = 10
PAPER_TEST_SYMBOLS = {"AAPL"}


def add_audit_event(event_type: str, payload: dict) -> dict:
    created_at = datetime.now(timezone.utc).isoformat()
    connection = db()
    try:
        cursor = connection.execute(
            "INSERT INTO audit_events (created_at, event_type, payload) VALUES (?, ?, ?)",
            (created_at, event_type, json.dumps(payload, sort_keys=True)),
        )
        connection.commit()
        return {"id": cursor.lastrowid, "created_at": created_at, "event_type": event_type}
    finally:
        connection.close()


def _test_fixture_gate_open(symbol: str, settings) -> bool:
    """Whether the AAPL paper-execution demo fixture is currently unlockable.

    Server-controlled only -- symbol string plus config, never a caller's
    claim that a fixture applies -- so the identical check can gate both the
    preview-time override and the approval-time re-derivation below without
    either one trusting the other.
    """
    return (
        symbol in PAPER_TEST_SYMBOLS
        and settings.moomoo_mode == "paper"
        and settings.trading_mode == "approval"
        and settings.paper_execution_enabled
    )


def _test_fixture_shariah_verdict(symbol: str) -> dict:
    return {
        "agent": "shariah",
        "market": "US",
        "provider": "PAPER_TEST_FIXTURE",
        "status": "PASS",
        "symbol": symbol,
        "reason": "paper_execution_test_fixture",
        "details": {"status": "COMPLIANT", "symbol": symbol, "fixture": True},
    }


def authoritative_shariah_verdict(symbol: str) -> dict:
    """The server-derived Shariah verdict for a symbol -- never a caller-supplied one.

    /paper/approval must call this instead of trusting the `shariah` field on
    the client-echoed preview body: a client can hand-edit that JSON before
    resubmitting it, and nothing about the approval endpoint's own request
    schema ties it back to a specific server-computed /paper/preview response.
    Without this, a caller could submit a fabricated PASS for a Malaysian
    ticker that is actually REJECT or UNKNOWN in the approved SC publication
    (or any US ticker SEC EDGAR would reject) directly to /paper/approval,
    bypassing the SC Malaysia gate entirely. This re-runs the same
    authoritative check /paper/preview performs -- the one recognized
    override remains the AAPL demo fixture, itself re-verified here from
    symbol + settings alone, never from anything the client claims.
    """
    normalized = str(symbol or "").strip().upper()
    settings = load_settings()
    if _test_fixture_gate_open(normalized, settings):
        return _test_fixture_shariah_verdict(normalized)
    return evaluate_shariah(normalized)


def _start_of_today_utc(now: datetime | None = None) -> datetime:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_iso_week_utc(now: datetime | None = None) -> datetime:
    """Monday 00:00 UTC of the current ISO week.

    The Obsidian vault's risk-policy.md specifies "maximum weekly realised
    loss (% of portfolio)" without stating calendar-week vs. rolling 7-day.
    Calendar week is used here -- not guessed -- for three reasons found by
    inspecting the same document and this codebase: (1) the sibling limits
    "daily loss" and "orders per day" are both unambiguously calendar-day
    scoped (MAX_ORDERS_PER_DAY resets at midnight, never a rolling 24h
    window), so weekly should follow the same convention as its neighbors
    rather than introduce a different one; (2) the same policy document's SC
    reclassification review cadence is anchored to calendar dates (last
    Friday of May/November), reinforcing calendar-boundary semantics
    throughout; (3) a rolling window never cleanly resets, which makes "did
    we breach this week's limit" unanswerable for a human reviewing the
    audit trail -- a hard trading limit needs a clean boundary.
    """
    today = _start_of_today_utc(now)
    return today - timedelta(days=today.weekday())


def _orders_today_count(connection: sqlite3.Connection) -> int:
    """Real count of orders actually approved today (UTC calendar day) --
    never the client's claimed `orders_today`. Counts APPROVED_PAPER_READY
    rows specifically: a rejected attempt never placed an order, so it must
    not count against the daily order-count circuit breaker.
    """
    since = _start_of_today_utc().isoformat()
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM approval_queue "
        "WHERE approval_status = 'APPROVED_PAPER_READY' AND created_at >= ?",
        (since,),
    ).fetchone()
    return int(row["n"] or 0)


def _period_loss_pct(
    connection: sqlite3.Connection, *, since: datetime, account_equity: float
) -> float:
    """Realized loss over [since, now) as a percentage of account equity.

    Returns float('inf') -- never a guessed number -- when account_equity is
    unusable or period_realized_pnl reports insufficient data, so the
    ceiling check in risk_checks.check_order() fails closed (inf can never
    be <= a finite limit) instead of silently passing on missing data.
    """
    if not account_equity or account_equity <= 0:
        return float("inf")
    period = period_realized_pnl(connection, since=since)
    if period["status"] != "OK":
        return float("inf")
    pnl = period["period_realized_pnl"]
    return round(max(0.0, -pnl) / account_equity * 100, 4)


def authoritative_risk_verdict(
    connection: sqlite3.Connection,
    *,
    symbol: str,
    side: str,
    quantity,
    price,
    asset_class: str,
) -> dict:
    """The server-derived risk verdict for the candidate being approved --
    never a caller-supplied one.

    Mirrors authoritative_shariah_verdict: /paper/approval must call this
    instead of trusting the `risk` field on the client-echoed preview body,
    for exactly the same reason -- nothing ties that field back to a
    specific server-computed /paper/preview response, so a client can claim
    any verdict it likes. Every dimension below is recomputed from
    server-controlled state:

      - position / total-exposure / sector-concentration / same-symbol /
        sell-sizing limits: portfolio_risk_overlay(), reused as-is. It
        already reads live portfolio state via portfolio_snapshot and
        already ignores the client's claimed percentages for gating them
        (they are informational-only fields there too) -- nothing new is
        introduced here, this just calls it again at approval time instead
        of trusting the copy computed once at preview time.
      - orders_today: a real count from approval_queue (_orders_today_count),
        not the client's claim.
      - daily_loss_pct / weekly_loss_pct: realized P&L computed from
        portfolio_store.period_realized_pnl, not the client's claim. Missing
        history fails closed (see _period_loss_pct) rather than assuming a
        clean day/week.

    loss_per_trade_pct (Phase 2A): the vault's risk-policy.md specifies
    "Maximum loss per trade (% of portfolio): 0.5", so this is not a
    no-op -- see MAX_LOSS_PER_TRADE_PCT in risk_checks.py / config.py, which
    already existed but was never fed a real value from this function. No
    stop-loss/exit-distance model exists anywhere in this codebase (S001's
    exit rule in the vault is a next-session technical exit -- SMA cross or
    a percentage pullback from the peak since entry -- not a fixed
    stop-distance from the entry price, and there is no automated
    position-management engine that would execute a stop-loss order; only
    entry signals are implemented in agents/quant_agent.py). Absent a real
    stop-distance, the only bound on a single trade's downside that is
    actually true given this policy's own constraints (long-only, no
    margin, no leverage, no short selling -- risk-policy.md's "Required
    checks") is: the position cannot lose more than what was paid for it.
    So for a BUY, loss_per_trade_pct is the candidate order's own notional
    (portfolio_detail["order_notional"], from the same overlay computed
    above -- no new data source) as a percentage of server-side account
    equity -- a conservative worst-case bound, not a precise estimate, and
    documented as such. A SELL only reduces exposure and cannot create new
    downside, so it is exempt (0.0), matching how sector-concentration and
    other BUY-only overlay checks already treat SELL. Equity unavailable or
    the candidate fields invalid both fail closed to float('inf') rather
    than silently pass.

    Options are not sized against the equity overlay here, matching
    preview-time behavior (CLAUDE.md known limitation: contracts and premium
    are not shares and share-price) -- their collateral sufficiency is
    independently verified via option_structure_gate / account_shariah_gate
    regardless of this function's result. Portfolio-concentration limits for
    options are a pre-existing gap (no such overlay exists for options at
    preview time either); this function does not newly introduce or close
    it, and it is reported as a remaining finding, not silently assumed
    solved. loss_per_trade_pct also stays 0.0 for options for the same
    reason -- contract premium is not comparable to equity notional, and
    building an options-specific loss model is out of scope for this phase
    (see test_options_gap_boundary.py for the regression test proving this
    boundary cannot be used to bypass equity or Shariah checks).
    """
    settings = load_settings()
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_side = str(side or "BUY").strip().upper()

    orders_today = _orders_today_count(connection)
    daily_loss_pct = _period_loss_pct(
        connection, since=_start_of_today_utc(), account_equity=settings.paper_account_equity
    )
    weekly_loss_pct = _period_loss_pct(
        connection, since=_start_of_iso_week_utc(), account_equity=settings.paper_account_equity
    )

    portfolio_detail: dict = {"status": "NOT_EVALUATED", "reason": "option_or_invalid_candidate"}
    position_pct = 0.0
    total_exposure_pct = 0.0
    loss_per_trade_pct = 0.0

    if asset_class != "option":
        try:
            quantity_int = int(quantity)
            price_float = float(price) if price is not None else None
            if (
                quantity_int <= 0
                or not normalized_symbol
                or price_float is None
                or price_float <= 0
            ):
                raise ValueError("invalid candidate fields")
            synthetic_request = PaperPreviewRequest(
                symbol=normalized_symbol,
                side=normalized_side,
                quantity=quantity_int,
                price=price_float,
                position_pct=0.0,
                total_exposure_pct=0.0,
                loss_per_trade_pct=0.0,
                daily_loss_pct=0.0,
                orders_today=0,
                asset_class="equity",
            )
        except (TypeError, ValueError, ValidationError):
            portfolio_detail = {
                "status": "REJECT",
                "reason": "invalid_order_parameters_for_risk_recomputation",
            }
            loss_per_trade_pct = float("inf")
        else:
            overlay = portfolio_risk_overlay(
                connection,
                synthetic_request,
                {"symbol": normalized_symbol, "price": price_float},
            )
            position_pct = overlay["projected_position_pct"]
            total_exposure_pct = overlay["projected_total_exposure_pct"]
            portfolio_detail = overlay
            if normalized_side == "BUY":
                if not settings.paper_account_equity or settings.paper_account_equity <= 0:
                    loss_per_trade_pct = float("inf")
                else:
                    order_notional = overlay.get("order_notional") or 0.0
                    loss_per_trade_pct = round(
                        (order_notional / settings.paper_account_equity) * 100, 4
                    )

    risk = evaluate_risk(
        position_pct=position_pct,
        total_exposure_pct=total_exposure_pct,
        loss_per_trade_pct=loss_per_trade_pct,
        daily_loss_pct=daily_loss_pct,
        orders_today=orders_today,
        weekly_loss_pct=weekly_loss_pct,
    )
    if portfolio_detail.get("status") == "REJECT":
        risk = {
            **risk,
            "status": "REJECT",
            "reason": portfolio_detail.get("reason", "portfolio_limit_failed"),
        }
    risk["details"] = {
        **risk.get("details", {}),
        "portfolio": portfolio_detail,
        "orders_today": orders_today,
        "daily_loss_pct": daily_loss_pct,
        "weekly_loss_pct": weekly_loss_pct,
        "loss_per_trade_pct": loss_per_trade_pct,
    }
    return risk


def paper_test_overrides(request: PaperPreviewRequest) -> dict:
    if not request.test_fixture:
        return {}

    settings = load_settings()
    symbol = request.symbol.strip().upper()
    if not _test_fixture_gate_open(symbol, settings):
        return {}

    price = request.price or 1.0
    return {
        "shariah_override": _test_fixture_shariah_verdict(symbol),
        "quant_override": {
            "agent": "quant",
            "status": "PASS",
            "symbol": symbol,
            "signal": "BUY",
            "reason": "paper_execution_test_fixture",
            "price": price,
            "bars": 220,
            "price_source": "paper_test_fixture",
            "strategy": {
                "signal": "BUY",
                "sma50": price,
                "sma200": round(price * 0.95, 4),
                "trend_ok": True,
                "breakout_ok": True,
                "breakout_level": round(price * 0.99, 4),
                "breakout_gap_pct": 1.0101,
            },
        },
    }


def portfolio_price_lookup(symbol: str) -> dict:
    return summarize_history(
        symbol,
        days=14,
        min_bars=1,
        allow_fallback=False,
        allow_stale_cache=True,
    )


def exposure_value(position: dict) -> float:
    value = position.get("market_value")
    if value is None:
        value = position.get("cost_basis")
    return float(value or 0)


def add_exposure_metadata(snapshot: dict, *, account_equity: float) -> dict:
    total_exposure = round(
        sum(exposure_value(position) for position in snapshot.get("positions", [])), 4
    )
    snapshot["paper_account_equity"] = account_equity
    snapshot["total_exposure"] = total_exposure
    snapshot["total_exposure_pct"] = round((total_exposure / account_equity) * 100, 4)
    for position in snapshot.get("positions", []):
        value = exposure_value(position)
        position["exposure_value"] = round(value, 4)
        position["account_exposure_pct"] = round((value / account_equity) * 100, 4)
    return snapshot


def portfolio_snapshot_with_exposure(connection: sqlite3.Connection) -> dict:
    settings = load_settings()
    snapshot = add_exposure_metadata(
        portfolio_snapshot(connection, price_lookup=portfolio_price_lookup),
        account_equity=settings.paper_account_equity,
    )
    snapshot["risk_limits"] = risk_limits_from_settings(settings)
    return snapshot


def positions_snapshot(connection: sqlite3.Connection) -> dict:
    portfolio = portfolio_snapshot_with_exposure(connection)
    limits = portfolio.get("risk_limits", {})
    max_position_pct = float(limits.get("max_position_pct") or 0)
    positions = []
    for position in portfolio.get("positions", []):
        quantity = round(float(position.get("quantity") or 0), 4)
        account_exposure_pct = round(float(position.get("account_exposure_pct") or 0), 4)
        valuation_status = position.get("valuation_status") or portfolio.get("valuation_status")
        positions.append(
            {
                "symbol": position.get("symbol"),
                "account_suffix": position.get("account_suffix"),
                "account_type": position.get("account_type"),
                "quantity": quantity,
                "average_cost": position.get("average_cost"),
                "cost_basis": position.get("cost_basis"),
                "latest_price": position.get("latest_price"),
                "price_source": position.get("price_source"),
                "price_date": position.get("price_date"),
                "market_value": position.get("market_value"),
                "unrealized_pnl": position.get("unrealized_pnl"),
                "unrealized_pnl_pct": position.get("unrealized_pnl_pct"),
                "realized_pnl": position.get("realized_pnl"),
                "exposure_value": position.get("exposure_value"),
                "account_exposure_pct": account_exposure_pct,
                "exposure_weight_pct": position.get("exposure_weight_pct"),
                "position_limit_status": "PASS"
                if account_exposure_pct <= max_position_pct
                else "BREACH",
                "valuation_status": valuation_status,
                "valuation_error": position.get("valuation_error"),
                "reduce_eligible": quantity > 0,
                "max_reduce_quantity": quantity,
                "updated_at": position.get("updated_at"),
            }
        )
    return {
        "status": "OK",
        "positions": positions,
        "position_count": len(positions),
        "paper_account_equity": portfolio.get("paper_account_equity"),
        "total_exposure": portfolio.get("total_exposure"),
        "total_exposure_pct": portfolio.get("total_exposure_pct"),
        "total_exposure_status": "PASS"
        if float(portfolio.get("total_exposure_pct") or 0)
        <= float(limits.get("max_total_exposure_pct") or 0)
        else "BREACH",
        "risk_limits": limits,
        "valuation_status": portfolio.get("valuation_status"),
        "valuation_errors": portfolio.get("valuation_errors", []),
        "updated_at": portfolio.get("updated_at"),
    }


def hours_since_iso(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - parsed).total_seconds() / 3600, 2)


def increment_count(counts: dict, key: str | None) -> None:
    label = key or "unknown"
    counts[label] = counts.get(label, 0) + 1


def compact_market_candidate(row: dict) -> dict:
    item = row.get("payload") or {}
    return {
        "symbol": row.get("symbol") or item.get("symbol"),
        "watch_status": row.get("watch_status") or item.get("watch_status"),
        "alert_status": row.get("alert_status") or item.get("alert_status"),
        "ready_for_approval": bool(row.get("ready_for_approval") or item.get("ready_for_approval")),
        "price": row.get("price") if row.get("price") is not None else item.get("price"),
        "trigger_price": row.get("trigger_price")
        if row.get("trigger_price") is not None
        else item.get("trigger_price"),
        "breakout_gap_pct": row.get("breakout_gap_pct")
        if row.get("breakout_gap_pct") is not None
        else item.get("breakout_gap_pct"),
        "distance_to_trigger": item.get("distance_to_trigger"),
        # The market the Shariah gate actually routed this symbol to, straight
        # from the scan payload -- i.e. the authoritative detect_market() result
        # rather than a rule re-implemented in the client. Stays None for rows
        # scanned before this field existed; a consumer must render that as
        # unknown, never default it to US.
        "market": item.get("shariah_market"),
        "shariah_status": item.get("shariah_status"),
        "quant_signal": item.get("quant_signal"),
        "risk_status": item.get("risk_status"),
        "price_source": item.get("price_source"),
        "data_freshness": item.get("data_freshness"),
        "cache_age_hours": item.get("cache_age_hours"),
        "bars": item.get("bars"),
        "blockers": item.get("blockers", []),
        "latest_scan": {
            "scan_id": row.get("scan_id"),
            "scanned_at": row.get("scanned_at"),
            "age_hours": hours_since_iso(row.get("scanned_at")),
            "event_status": row.get("event_status"),
        },
    }


def market_overview_snapshot(
    connection: sqlite3.Connection, *, stale_cache_hours: float = 24.0
) -> dict:
    settings = load_settings()
    watchlist = get_watchlist_settings(connection)
    symbols = watchlist["symbols"]
    latest_results = list_latest_scan_results(connection, symbols=symbols, limit=200)
    result_symbols = {row.get("symbol") for row in latest_results}
    candidates = [compact_market_candidate(row) for row in latest_results]
    portfolio = portfolio_snapshot_with_exposure(connection)
    freshness_counts = {}
    source_counts = {}
    status_counts = {
        "ready": 0,
        "alerts": 0,
        "near_breakout": 0,
        "blocked": 0,
        "data_errors": 0,
        "not_ready": 0,
    }
    stale_cache_symbols = []
    # Counted over every candidate, deliberately before the [:10] slices below.
    # A client that splits the *sliced* ready_candidates by market would report
    # "2 Malaysian" when five exist, with nothing on screen to reveal the
    # truncation. These counts are what let the UI say "showing N of M".
    market_counts = {}
    ready_market_counts = {}
    for candidate in candidates:
        watch_status = candidate.get("watch_status")
        increment_count(market_counts, candidate.get("market"))
        if candidate.get("ready_for_approval"):
            increment_count(ready_market_counts, candidate.get("market"))
        if candidate.get("ready_for_approval"):
            status_counts["ready"] += 1
        if candidate.get("alert_status") == "ALERT":
            status_counts["alerts"] += 1
        if watch_status == "NEAR_BREAKOUT":
            status_counts["near_breakout"] += 1
        elif watch_status == "BLOCKED":
            status_counts["blocked"] += 1
        elif watch_status == "DATA_ERROR":
            status_counts["data_errors"] += 1
        elif watch_status == "NOT_READY":
            status_counts["not_ready"] += 1
        increment_count(freshness_counts, candidate.get("data_freshness"))
        increment_count(source_counts, candidate.get("price_source"))
        cache_age = candidate.get("cache_age_hours")
        if cache_age is not None and float(cache_age) > stale_cache_hours:
            stale_cache_symbols.append(candidate["symbol"])

    latest_scan_times = [
        candidate["latest_scan"]["scanned_at"]
        for candidate in candidates
        if candidate["latest_scan"]["scanned_at"]
    ]
    latest_scan_at = max(latest_scan_times) if latest_scan_times else None
    unscanned_symbols = [symbol for symbol in symbols if symbol not in result_symbols]
    ready_candidates = [candidate for candidate in candidates if candidate["ready_for_approval"]]
    alert_candidates = [
        candidate for candidate in candidates if candidate.get("alert_status") == "ALERT"
    ]
    data_error_candidates = [
        candidate for candidate in candidates if candidate.get("watch_status") == "DATA_ERROR"
    ]
    return {
        "status": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trading_mode": settings.trading_mode,
        "broker_submission": broker_submission_configured(settings),
        "paper_execution_enabled": settings.paper_execution_enabled,
        "watchlist": {
            "symbols": symbols,
            "count": len(symbols),
            "alert_threshold_pct": watchlist["alert_threshold_pct"],
            "updated_at": watchlist["updated_at"],
            "unscanned_symbols": unscanned_symbols,
        },
        "latest_scan": {
            "scanned_at": latest_scan_at,
            "age_hours": hours_since_iso(latest_scan_at),
            "scanned_count": len(candidates),
            "coverage_pct": round((len(candidates) / len(symbols)) * 100, 4) if symbols else 0,
        },
        "counts": status_counts,
        "by_market": {
            "scanned": market_counts,
            "ready": ready_market_counts,
            "ready_total": len(ready_candidates),
        },
        "data_health": {
            "freshness_counts": freshness_counts,
            "source_counts": source_counts,
            "stale_cache_hours": stale_cache_hours,
            "stale_cache_symbols": stale_cache_symbols,
        },
        "portfolio": {
            "open_positions": portfolio.get("position_count", 0),
            "valuation_status": portfolio.get("valuation_status"),
            "total_exposure": portfolio.get("total_exposure"),
            "total_exposure_pct": portfolio.get("total_exposure_pct"),
            "paper_account_equity": portfolio.get("paper_account_equity"),
        },
        "risk_limits": portfolio.get("risk_limits", risk_limits_from_settings(settings)),
        "ready_candidates": ready_candidates[:10],
        "alert_candidates": alert_candidates[:10],
        "data_error_candidates": data_error_candidates[:10],
        "recent_alert_events": list_alert_events(connection, limit=10),
    }


def parse_json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def filled_queue_ids(connection: sqlite3.Connection) -> set[int]:
    ensure_portfolio_tables(connection)
    rows = connection.execute("SELECT queue_id FROM paper_fills").fetchall()
    return {int(row["queue_id"]) for row in rows}


def latest_execution_events(connection: sqlite3.Connection, *, limit: int = 25) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, created_at, event_type, payload
        FROM audit_events
        WHERE event_type IN ('paper_execution', 'paper_execution_rejected', 'paper_reconciliation')
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    events = []
    for row in rows:
        payload = parse_json_object(row["payload"])
        events.append(
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "queue_id": payload.get("queue_id"),
                "status": payload.get("status"),
                "broker_submission": bool(payload.get("broker_submission")),
                "message": payload.get("message")
                or payload.get("execution_message")
                or payload.get("reason"),
            }
        )
    return events


def execution_audit_row(approval: dict, synced_fills: set[int]) -> dict:
    payload = parse_json_object(approval.get("payload"))
    preview = payload.get("preview") if isinstance(payload.get("preview"), dict) else {}
    quote = (
        preview.get("quote_snapshot") if isinstance(preview.get("quote_snapshot"), dict) else None
    )
    broker_submission = (
        payload.get("broker_submission")
        if isinstance(payload.get("broker_submission"), dict)
        else None
    )
    broker_reconciliation = (
        payload.get("broker_reconciliation")
        if isinstance(payload.get("broker_reconciliation"), dict)
        else None
    )
    approval_audit = (
        validate_approval_payload_for_execution(approval)
        if approval.get("approval_status") == "APPROVED_PAPER_READY"
        else {"status": "SKIPPED", "errors": []}
    )
    queue_id = int(approval["id"])
    return {
        "id": queue_id,
        "created_at": approval.get("created_at"),
        "symbol": approval.get("symbol"),
        "side": approval.get("side"),
        "quantity": approval.get("quantity"),
        "price": approval.get("price"),
        "notional": approval.get("notional"),
        "approval_status": approval.get("approval_status"),
        "execution_status": approval.get("execution_status"),
        "execution_message": approval.get("execution_message"),
        "executed_at": approval.get("executed_at"),
        "execution_environment": approval.get("execution_environment"),
        "broker_submission": bool(approval.get("broker_submission")),
        "broker_order_id": broker_submission.get("broker_order_id") if broker_submission else None,
        "broker_reconciliation_status": broker_reconciliation.get("status")
        if broker_reconciliation
        else None,
        "broker_order_status": broker_reconciliation.get("order_status")
        if broker_reconciliation
        else None,
        "fill_synced": queue_id in synced_fills,
        "has_quote_snapshot": quote is not None,
        "quote_snapshot_source": quote.get("source") if quote else None,
        "shariah_status": approval.get("shariah_status"),
        "quant_signal": approval.get("quant_signal"),
        "risk_status": approval.get("risk_status"),
        "approval_audit_status": approval_audit["status"],
        "approval_audit_errors": approval_audit["errors"],
    }


def execution_audit_snapshot(connection: sqlite3.Connection, *, limit: int = 100) -> dict:
    approvals = list_approvals(connection, limit=max(1, min(500, limit)))
    synced_fills = filled_queue_ids(connection)
    rows = [execution_audit_row(approval, synced_fills) for approval in approvals]
    failed_payload_rows = [row for row in rows if row["approval_audit_status"] == "REJECT"]
    pending_execution = [
        row
        for row in rows
        if row["approval_status"] == "APPROVED_PAPER_READY"
        and not row["broker_submission"]
        and row["execution_status"] in {None, "NOT_EXECUTED"}
    ]
    locked_or_rejected = [
        row
        for row in rows
        if row["execution_status"]
        in {
            "CONFIRMATION_REQUIRED",
            "TRADING_MODE_BLOCKED",
            "EXECUTION_LOCKED",
            "SHARIAH_GATE_FAILED",
            "RISK_GATE_FAILED",
            "APPROVAL_AUDIT_FAILED",
            "MOOMOO_NOT_READY",
            "PORTFOLIO_SELL_GATE_FAILED",
            "ADAPTER_NOT_CONFIGURED",
            "NOT_APPROVED",
        }
    ]
    missing_fill_sync = [
        row
        for row in rows
        if row["broker_reconciliation_status"] == "BROKER_FILLED" and not row["fill_synced"]
    ]
    broker_submitted = [row for row in rows if row["broker_submission"]]
    return {
        "status": "OK",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "approval_rows": len(rows),
            "pending_execution": len(pending_execution),
            "broker_submitted": len(broker_submitted),
            "broker_filled": sum(
                1 for row in rows if row["broker_reconciliation_status"] == "BROKER_FILLED"
            ),
            "fill_synced": sum(1 for row in rows if row["fill_synced"]),
            "missing_fill_sync": len(missing_fill_sync),
            "payload_audit_failures": len(failed_payload_rows),
            "locked_or_rejected": len(locked_or_rejected),
        },
        "pending_execution": pending_execution[:25],
        "broker_submitted": broker_submitted[:25],
        "payload_audit_failures": failed_payload_rows[:25],
        "locked_or_rejected": locked_or_rejected[:25],
        "missing_fill_sync": missing_fill_sync[:25],
        "recent_execution_events": latest_execution_events(connection),
    }


def stock_profile_snapshot(connection: sqlite3.Connection, symbol: str) -> dict:
    normalized_symbol = symbol.strip().upper()
    settings = load_settings()
    profile = {
        "status": "OK" if normalized_symbol else "INVALID_SYMBOL",
        "symbol": normalized_symbol,
        "market": detect_market(normalized_symbol) if normalized_symbol else None,
        "shariah": None,
        "market_data": None,
        "latest_opportunity": None,
        "portfolio": {
            "positions": [],
            "quantity": 0.0,
            "cost_basis": 0.0,
            "market_value": 0.0,
            "unrealized_pnl": 0.0,
            "account_exposure_pct": 0.0,
        },
        "risk_limits": risk_limits_from_settings(settings),
        "errors": [],
    }
    if not normalized_symbol:
        profile["errors"].append({"section": "symbol", "reason": "symbol_required"})
        return profile

    try:
        profile["shariah"] = evaluate_shariah(normalized_symbol)
    except Exception as exc:
        profile["errors"].append({"section": "shariah", "reason": type(exc).__name__})

    try:
        profile["market_data"] = summarize_history(
            normalized_symbol,
            days=365,
            min_bars=200,
            allow_fallback=False,
            allow_stale_cache=True,
        )
    except Exception as exc:
        profile["errors"].append({"section": "market_data", "reason": type(exc).__name__})

    latest_results = list_latest_scan_results(connection, symbols=[normalized_symbol])
    if latest_results:
        profile["latest_opportunity"] = latest_results[0]

    portfolio = portfolio_snapshot_with_exposure(connection)
    positions = [
        position
        for position in portfolio.get("positions", [])
        if position.get("symbol") == normalized_symbol
    ]
    market_value = round(sum(float(position.get("market_value") or 0) for position in positions), 4)
    cost_basis = round(sum(float(position.get("cost_basis") or 0) for position in positions), 4)
    quantity = round(sum(float(position.get("quantity") or 0) for position in positions), 4)
    unrealized_pnl = round(
        sum(float(position.get("unrealized_pnl") or 0) for position in positions), 4
    )
    exposure_value_total = round(sum(exposure_value(position) for position in positions), 4)
    profile["portfolio"] = {
        "positions": positions,
        "quantity": quantity,
        "cost_basis": cost_basis,
        "market_value": market_value,
        "unrealized_pnl": unrealized_pnl,
        "account_exposure_pct": round(
            (exposure_value_total / settings.paper_account_equity) * 100, 4
        ),
        "valuation_status": portfolio.get("valuation_status"),
        "valuation_errors": [
            error
            for error in portfolio.get("valuation_errors", [])
            if error.get("symbol") == normalized_symbol
        ],
    }
    return profile


def compact_approval(approval: dict) -> dict:
    return {
        "id": approval.get("id"),
        "created_at": approval.get("created_at"),
        "symbol": approval.get("symbol"),
        "side": approval.get("side"),
        "quantity": approval.get("quantity"),
        "price": approval.get("price"),
        "notional": approval.get("notional"),
        "approval_status": approval.get("approval_status"),
        "execution_status": approval.get("execution_status"),
        "execution_message": approval.get("execution_message"),
        "broker_submission": approval.get("broker_submission"),
        "shariah_status": approval.get("shariah_status"),
        "quant_signal": approval.get("quant_signal"),
        "risk_status": approval.get("risk_status"),
    }


def committee_status(item: dict) -> str:
    if item.get("ready_for_approval") is True:
        return "READY_FOR_REVIEW"
    if item.get("watch_status") == "DATA_ERROR":
        return "DATA_ERROR"
    if item.get("alert_status") == "ALERT":
        return "WATCH_ALERT"
    if item.get("blockers"):
        return "BLOCKED"
    return "WATCH"


def investment_committee_snapshot(connection: sqlite3.Connection, *, limit: int = 50) -> dict:
    settings = load_settings()
    watchlist = get_watchlist_settings(connection)
    latest_results = list_latest_scan_results(
        connection, symbols=watchlist["symbols"], limit=max(1, min(200, limit))
    )
    approvals = list_approvals(connection, limit=max(1, min(200, limit)))
    portfolio = portfolio_snapshot_with_exposure(connection)

    approvals_by_symbol: dict[str, list[dict]] = {}
    for approval in approvals:
        symbol = str(approval.get("symbol") or "").upper()
        approvals_by_symbol.setdefault(symbol, []).append(compact_approval(approval))

    candidates = []
    for row in latest_results:
        item = row.get("payload") or {}
        symbol = row.get("symbol") or item.get("symbol")
        candidates.append(
            {
                "symbol": symbol,
                "committee_status": committee_status(item),
                "decision": item.get("decision"),
                "ready_for_approval": bool(item.get("ready_for_approval")),
                "blockers": item.get("blockers", []),
                "shariah_status": item.get("shariah_status"),
                "quant_signal": item.get("quant_signal"),
                "risk_status": item.get("risk_status"),
                "watch_status": item.get("watch_status"),
                "alert_status": item.get("alert_status"),
                "price": item.get("price"),
                "trigger_price": item.get("trigger_price"),
                "breakout_gap_pct": item.get("breakout_gap_pct"),
                "latest_scan": {
                    "scan_id": row.get("scan_id"),
                    "scanned_at": row.get("scanned_at"),
                    "event_status": row.get("event_status"),
                },
                "recent_approvals": approvals_by_symbol.get(symbol, [])[:5],
            }
        )

    pending_approvals = [
        compact_approval(approval)
        for approval in approvals
        if approval.get("approval_status") == "APPROVED_PAPER_READY"
        and not approval.get("broker_submission")
    ]
    submitted_orders = [
        compact_approval(approval) for approval in approvals if approval.get("broker_submission")
    ]
    return {
        "status": "OK",
        "trading_mode": settings.trading_mode,
        "paper_execution_enabled": settings.paper_execution_enabled,
        "paper_execution_adapter": settings.paper_execution_adapter,
        "broker_submission": broker_submission_configured(settings),
        "watchlist": {
            "symbols": watchlist["symbols"],
            "alert_threshold_pct": watchlist["alert_threshold_pct"],
            "updated_at": watchlist["updated_at"],
        },
        "counts": {
            "candidates": len(candidates),
            "ready_for_review": sum(
                1 for candidate in candidates if candidate["committee_status"] == "READY_FOR_REVIEW"
            ),
            "watch_alerts": sum(
                1 for candidate in candidates if candidate["committee_status"] == "WATCH_ALERT"
            ),
            "blocked": sum(
                1 for candidate in candidates if candidate["committee_status"] == "BLOCKED"
            ),
            "data_errors": sum(
                1 for candidate in candidates if candidate["committee_status"] == "DATA_ERROR"
            ),
            "pending_approvals": len(pending_approvals),
            "submitted_orders": len(submitted_orders),
            "open_positions": portfolio.get("position_count", 0),
        },
        "risk_limits": risk_limits_from_settings(settings),
        "portfolio": {
            "valuation_status": portfolio.get("valuation_status"),
            "total_exposure": portfolio.get("total_exposure"),
            "total_exposure_pct": portfolio.get("total_exposure_pct"),
            "paper_account_equity": portfolio.get("paper_account_equity"),
            "positions": portfolio.get("positions", []),
        },
        "candidates": candidates,
        "pending_approvals": pending_approvals,
        "submitted_orders": submitted_orders,
    }


def risk_limits_from_settings(settings) -> dict:
    return {
        "max_position_pct": settings.max_position_pct,
        "max_total_exposure_pct": settings.max_total_exposure_pct,
        "max_loss_per_trade_pct": settings.max_loss_per_trade_pct,
        "max_daily_loss_pct": settings.max_daily_loss_pct,
        "max_orders_per_day": settings.max_orders_per_day,
        "max_sector_exposure_pct": settings.max_sector_exposure_pct,
    }


def portfolio_risk_overlay(
    connection: sqlite3.Connection, request: PaperPreviewRequest, evaluation: dict
) -> dict:
    settings = load_settings()
    limits = risk_limits_from_settings(settings)
    symbol = evaluation.get("symbol") or request.symbol.strip().upper()
    side = request.side.strip().upper()
    selected_price = evaluation.get("price") or request.price
    notional = round(request.quantity * selected_price, 4) if selected_price else 0
    snapshot = portfolio_snapshot_with_exposure(connection)
    matching_positions = [
        position for position in snapshot.get("positions", []) if position.get("symbol") == symbol
    ]
    current_position_exposure = round(
        sum(exposure_value(position) for position in matching_positions),
        4,
    )
    current_position_quantity = round(
        sum(float(position.get("quantity") or 0) for position in matching_positions), 4
    )
    current_total_exposure = round(snapshot.get("total_exposure") or 0, 4)
    if side == "SELL":
        projected_position_quantity = max(0, round(current_position_quantity - request.quantity, 4))
        remaining_ratio = (
            projected_position_quantity / current_position_quantity
            if current_position_quantity
            else 0
        )
        projected_position_exposure = round(current_position_exposure * remaining_ratio, 4)
        reduced_exposure = round(current_position_exposure - projected_position_exposure, 4)
        projected_total_exposure = max(0, round(current_total_exposure - reduced_exposure, 4))
    else:
        projected_position_quantity = round(current_position_quantity + request.quantity, 4)
        projected_position_exposure = max(0, round(current_position_exposure + notional, 4))
        projected_total_exposure = max(0, round(current_total_exposure + notional, 4))
    projected_position_pct = round(
        (projected_position_exposure / settings.paper_account_equity) * 100, 4
    )
    projected_total_pct = round((projected_total_exposure / settings.paper_account_equity) * 100, 4)
    effective_position_pct = max(request.position_pct, projected_position_pct)
    effective_total_pct = max(request.total_exposure_pct, projected_total_pct)
    blockers = []
    warnings = []
    messages = {}
    if projected_position_pct > limits["max_position_pct"]:
        blockers.append("portfolio_position_limit")
        messages["portfolio_position_limit"] = (
            f"{symbol} would become {projected_position_pct:.2f}% of paper account equity, above the {limits['max_position_pct']:.2f}% position limit."
        )
    if projected_total_pct > limits["max_total_exposure_pct"]:
        blockers.append("portfolio_total_exposure_limit")
        messages["portfolio_total_exposure_limit"] = (
            f"Total projected exposure would become {projected_total_pct:.2f}% of paper account equity, above the {limits['max_total_exposure_pct']:.2f}% total exposure limit."
        )
    if side == "BUY" and current_position_exposure > 0:
        blockers.append("portfolio_existing_position")
        messages["portfolio_existing_position"] = (
            f"{symbol} is already held locally; same-symbol BUY add-ons are blocked until the risk policy is changed."
        )
    if side == "SELL" and current_position_quantity <= 0:
        blockers.append("portfolio_sell_without_position")
        messages["portfolio_sell_without_position"] = (
            f"{symbol} cannot be sold because no local paper position is recorded."
        )
    # Sector concentration. Only a BUY can increase it, and a SELL reducing an
    # over-concentrated sector must never be blocked for being in that sector.
    # This sits inside the equity-only overlay by design: an option order returns
    # before apply_portfolio_risk_overlay is ever reached, because contracts and
    # premium are not shares and share-price (CLAUDE.md known limitation 4).
    positions = snapshot.get("positions", [])
    sectors = sectors_for_symbols(
        connection, [position.get("symbol") for position in positions] + [symbol]
    )
    sector_result = check_sector_concentration(
        symbol=symbol,
        added_exposure=notional if side == "BUY" else 0.0,
        positions=positions,
        sectors=sectors,
        account_equity=settings.paper_account_equity,
        max_sector_pct=limits["max_sector_exposure_pct"],
    )
    if side == "BUY" and sector_result["status"] == "REJECT":
        blockers.append("portfolio_sector_concentration_limit")
        messages["portfolio_sector_concentration_limit"] = sector_result["message"]
    if (
        side == "SELL"
        and current_position_quantity > 0
        and request.quantity > current_position_quantity
    ):
        blockers.append("portfolio_sell_exceeds_position")
        messages["portfolio_sell_exceeds_position"] = (
            f"Sell quantity {request.quantity} exceeds the local {symbol} position of {current_position_quantity:g}."
        )
    if (
        side == "SELL"
        and current_position_quantity > 0
        and request.quantity <= current_position_quantity
    ):
        warnings.append("portfolio_reduce_position")
        messages["portfolio_reduce_position"] = (
            f"{symbol} SELL would reduce the local position from {current_position_quantity:g} to {projected_position_quantity:g} shares."
        )
    return {
        "status": "PASS" if not blockers else "REJECT",
        "reason": "portfolio_limits_passed" if not blockers else "portfolio_limit_failed",
        "blockers": blockers,
        "warnings": warnings,
        "messages": messages,
        "account_equity": settings.paper_account_equity,
        "symbol": symbol,
        "side": side,
        "order_notional": notional,
        "current_position_exposure": current_position_exposure,
        "current_position_quantity": current_position_quantity,
        "current_total_exposure": current_total_exposure,
        "projected_position_exposure": projected_position_exposure,
        "projected_position_quantity": projected_position_quantity,
        "projected_total_exposure": projected_total_exposure,
        "submitted_position_pct": request.position_pct,
        "submitted_total_exposure_pct": request.total_exposure_pct,
        "projected_position_pct": projected_position_pct,
        "projected_total_exposure_pct": projected_total_pct,
        "effective_position_pct": round(effective_position_pct, 4),
        "effective_total_exposure_pct": round(effective_total_pct, 4),
        "limits": limits,
        "sector_concentration": sector_result,
        "valuation_status": snapshot.get("valuation_status"),
        "valuation_errors": snapshot.get("valuation_errors", []),
    }


def apply_portfolio_risk_overlay(
    connection: sqlite3.Connection, request: PaperPreviewRequest, evaluation: dict
) -> dict:
    overlay = portfolio_risk_overlay(connection, request, evaluation)
    risk = evaluation.setdefault("agent_summary", {}).setdefault("risk", {})
    details = risk.setdefault("details", {})
    checks = details.setdefault("checks", {})
    limits = overlay["limits"]
    checks["portfolio_position_ceiling"] = (
        overlay["projected_position_pct"] <= limits["max_position_pct"]
    )
    checks["portfolio_total_exposure"] = (
        overlay["projected_total_exposure_pct"] <= limits["max_total_exposure_pct"]
    )
    details["portfolio"] = overlay
    if request.side.strip().upper() == "SELL" and overlay["status"] == "PASS":
        blockers = evaluation.setdefault("blockers", [])
        blockers[:] = [
            blocker
            for blocker in blockers
            if blocker not in {"only_buy_side_supported", "quant_no_buy_signal"}
        ]
        if not blockers:
            evaluation["decision"] = "READY_FOR_APPROVAL"
    if overlay["status"] != "PASS":
        risk["status"] = "REJECT"
        risk["reason"] = overlay["reason"]
        details["status"] = "REJECT"
        blockers = evaluation.setdefault("blockers", [])
        for blocker in overlay["blockers"]:
            if blocker not in blockers:
                blockers.append(blocker)
        if "risk_rejected" not in blockers:
            blockers.append("risk_rejected")
        evaluation["decision"] = "BLOCKED"
    evaluation["blocker_messages"] = blocker_messages_for_evaluation(evaluation)
    return evaluation


def blocker_messages_for_evaluation(evaluation: dict) -> list[dict]:
    agents = evaluation.get("agent_summary", {})
    quant = agents.get("quant", {})
    risk = agents.get("risk", {})
    portfolio = risk.get("details", {}).get("portfolio", {})
    portfolio_messages = portfolio.get("messages", {})
    messages = []
    for blocker in evaluation.get("blockers", []):
        message = portfolio_messages.get(blocker)
        if message is None and blocker == "quant_no_buy_signal":
            strategy = quant.get("strategy", {})
            gap = strategy.get("breakout_gap_pct")
            breakout_level = strategy.get("breakout_level")
            if gap is not None and breakout_level is not None:
                message = f"Quant signal is {quant.get('signal', 'NO_SIGNAL')} because price is {abs(float(gap)):.2f}% below breakout level {float(breakout_level):.2f}."
            else:
                message = f"Quant signal is {quant.get('signal', 'NO_SIGNAL')}; strategy conditions are not met."
        if message is None and blocker == "only_buy_side_supported":
            message = "This side is not supported by the current approval workflow."
        if message is None and blocker == "risk_rejected":
            message = risk.get("reason", "Risk engine rejected this order.")
        if message is None and blocker == "shariah_rejected":
            message = "Shariah agent rejected this symbol."
        if message is None and blocker == REASON_OPTION_NOT_PERMITTED:
            message = option_determination_summary()
        if message is None:
            message = blocker
        messages.append({"blocker": blocker, "message": message})
    return messages


def _record_preview_evidence(evaluation: dict) -> dict:
    """Write the evidence record for a preview, after the decision is final.

    evaluate_candidate deliberately does not record for this path: the overlay
    below can turn a BLOCKED sell into READY_FOR_APPROVAL, and a trail that
    disagrees with the order it describes is not evidence. Recorded here, once,
    against whatever the answer actually turned out to be.
    """
    try:
        agent_coordinator.record_decision(evaluation, source="preview")
    except Exception:
        # Same reasoning as the coordinator's own guard: bookkeeping must not
        # turn a correctly-made decision into an error.
        pass
    return evaluation


def evaluate_preview_request(request: PaperPreviewRequest) -> dict:
    evaluation = evaluate_candidate(
        symbol=request.symbol,
        side=request.side,
        quantity=request.quantity,
        price=request.price,
        position_pct=request.position_pct,
        total_exposure_pct=request.total_exposure_pct,
        loss_per_trade_pct=request.loss_per_trade_pct,
        daily_loss_pct=request.daily_loss_pct,
        orders_today=request.orders_today,
        asset_class=request.asset_class,
        record_evidence=False,
        **paper_test_overrides(request),
    )
    if request.asset_class == "option":
        # The equity position/exposure overlay below treats `quantity`/`price`
        # as shares/share-price; for an option order those are contracts and
        # premium, so applying it here would size a covered call as if it
        # were an equity trade on the same notional (see CLAUDE.md Known
        # limitations). Option-native sizing (ownership/collateral) happens
        # at approval time via option_structure_gate/account_shariah_gate,
        # which don't have this unit mismatch.
        evaluation["blocker_messages"] = blocker_messages_for_evaluation(evaluation)
        return _record_preview_evidence(evaluation)
    connection = db()
    try:
        return _record_preview_evidence(
            apply_portfolio_risk_overlay(connection, request, evaluation)
        )
    finally:
        connection.close()


def quote_snapshot_for_preview(evaluation: dict, request: PaperPreviewRequest) -> dict:
    quant = evaluation.get("agent_summary", {}).get("quant", {})
    symbol = evaluation.get("symbol") or request.symbol.strip().upper()
    snapshot = {
        "symbol": symbol,
        "latest_close": quant.get("price") or evaluation.get("price"),
        "latest_date": quant.get("latest_date"),
        "source": quant.get("price_source"),
        "bars": quant.get("bars"),
        "min_bars": quant.get("min_bars"),
        "enough_history": quant.get("enough_history"),
        "data_freshness": quant.get("data_freshness"),
        "cache_cached_at": quant.get("cache_cached_at"),
        "cache_age_hours": quant.get("cache_age_hours"),
        "fallback_allowed": False,
        "stale_cache_allowed": True,
        "quote_snapshot_source": "quant_agent",
    }
    if snapshot["latest_date"] is not None and snapshot["source"] is not None:
        return snapshot

    try:
        market = summarize_history(
            symbol,
            days=14,
            min_bars=1,
            allow_fallback=False,
            allow_stale_cache=True,
        )
    except Exception as exc:
        snapshot["quote_snapshot_source"] = "market_data_error"
        snapshot["error"] = type(exc).__name__
        return snapshot

    snapshot.update(
        {
            "latest_close": market.get("latest_close"),
            "latest_date": market.get("latest_date"),
            "source": market.get("source"),
            "bars": market.get("bars"),
            "min_bars": market.get("min_bars"),
            "enough_history": market.get("enough_history"),
            "start_date": market.get("start_date"),
            "end_date": market.get("end_date"),
            "quote_snapshot_source": "market_data",
        }
    )
    return snapshot


def mounted_dashboard_url() -> str | None:
    """The dashboard's URL when something has mounted it onto this app.

    backend/replit_app.py mounts dashboard/ at /dashboard for the combined
    deployment; running local_api alone does not. Reported only when the mount
    is actually present, so this never advertises a path that would 404.
    """
    return (
        "/dashboard/"
        if any(getattr(route, "path", None) == "/dashboard" for route in app.routes)
        else None
    )


@app.get("/")
def home() -> dict:
    settings = load_settings()
    broker_submission = broker_submission_configured(settings)
    return {
        "name": "Amanah Trader Local API",
        "status": "running",
        # First field a browser sees on the deployed root, which otherwise shows
        # only this JSON. Additive: the route keeps its existing shape.
        "dashboard_url": mounted_dashboard_url(),
        "routes": [
            "/health",
            "/system/mode",
            "/paper/status",
            "/moomoo/status",
            "/market-data/{symbol}",
            "/market-overview",
            "/news",
            "/stock/{symbol}/profile",
            "/investment-committee",
            "/watchlist",
            "/opportunities",
            "/opportunity-alerts",
            "/agent/evaluate",
            "/stock/{symbol}/explain",
            "/stock/{symbol}/option-strategy",
            "/paper/preview",
            "/paper/approval",
            "/paper/execute/{queue_id}",
            "/paper/reconcile/{queue_id}",
            "/execution-audit",
            "/positions",
            "/portfolio",
            "/portfolio/history",
            "/portfolio/history/live",
            "/paper/account",
            "/paper/positions/live",
            "/approvals",
            "/audit",
        ],
        "live_trading": False,
        "trading_mode": settings.trading_mode,
        "paper_execution_enabled": settings.paper_execution_enabled,
        "paper_execution_adapter": settings.paper_execution_adapter,
        "broker_submission": broker_submission,
    }


@app.get("/health")
def health(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    settings = load_settings()
    return {
        "status": "ok",
        "mode": settings.moomoo_mode,
        "trading_mode": settings.trading_mode,
        "paper_execution_enabled": settings.paper_execution_enabled,
        "paper_execution_adapter": settings.paper_execution_adapter,
        "broker_submission": broker_submission_configured(settings),
    }


@app.get("/paper/status")
def paper_status(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    settings = load_settings()
    return {
        "mode": "SIMULATE",
        "trading_mode": settings.trading_mode,
        "approval_required": settings.trading_mode == "approval",
        "paper_execution_enabled": settings.paper_execution_enabled,
        "paper_execution_adapter": settings.paper_execution_adapter,
        "live_trading": False,
        "broker_submission": broker_submission_configured(settings),
        # Which markets this instance will actually submit for. The bridge relay reads
        # this to decide whether to offer an execute button, rather than restating the
        # routing rule. It was added to /system/mode first and wired here only after a
        # contract test caught that the relay was reading a key this route never sent.
        "execution_markets": execution_markets(settings),
    }


@app.get("/paper/account")
def paper_account(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    """Live broker account facts -- equity, cash, buying power, daily change.

    Display only. account_shariah_gate and the risk overlay still size off the
    static PAPER_ACCOUNT_EQUITY baseline (provision_cash_account.py), not this.
    """
    return check_alpaca_status()


@app.get("/paper/positions/live")
def paper_positions_live(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    """Live broker positions, including options -- read-only, never booked.

    Separate from GET /positions, which is the local equity-only ledger (see
    CLAUDE.md Known limitation 2: option fills are audited, not tracked as
    positions there). This is a direct read of what Alpaca itself reports.
    """
    return fetch_broker_positions()


@app.get("/paper/risk-snapshot")
def paper_risk_snapshot(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    """Returns the current risk metrics (orders today, daily loss pct, weekly loss pct)
    using the same helpers as authoritative_risk_verdict."""
    connection = db()
    try:
        settings = load_settings()
        orders_today = _orders_today_count(connection)
        daily_loss_pct = _period_loss_pct(
            connection, since=_start_of_today_utc(), account_equity=settings.paper_account_equity
        )
        weekly_loss_pct = _period_loss_pct(
            connection, since=_start_of_iso_week_utc(), account_equity=settings.paper_account_equity
        )
        return {
            "orders_today": orders_today,
            "daily_loss_pct": daily_loss_pct,
            "weekly_loss_pct": weekly_loss_pct,
        }
    finally:
        connection.close()


@app.get("/portfolio/history/live")
def portfolio_history_live(
    period: str = "1M", timeframe: str | None = None, actor: auth.Actor = Depends(get_owner_actor)
) -> dict:
    """The account's real equity curve, straight from the broker.

    Backs the dashboard's Portfolio Value History chart with actual mark-to-
    market history instead of locally-accumulated snapshots. fetch_portfolio_history
    raises SystemExit on a broker failure (by design, for its CLI use) -- caught
    here and reported as a status field instead, matching every other endpoint's
    fail-closed shape.
    """
    credentials = alpaca_credentials()
    if credentials is None:
        return {"status": "credentials_missing", "timestamps": [], "equity": []}

    resolved_timeframe = timeframe or ("5Min" if period == "1D" else "1D")
    try:
        history = portfolio_metrics.fetch_portfolio_history(
            credentials=credentials, period=period, timeframe=resolved_timeframe
        )
    except SystemExit as exc:
        return {"status": "unreachable", "reason": str(exc), "timestamps": [], "equity": []}

    effective_timeframe = history.get("timeframe") or resolved_timeframe
    return {
        "status": "ok",
        "period": period,
        "timeframe": effective_timeframe,
        "timestamps": history.get("timestamp") or [],
        "equity": history.get("equity") or [],
        "profit_loss": history.get("profit_loss") or [],
        "profit_loss_pct": history.get("profit_loss_pct") or [],
        "base_value": history.get("base_value"),
        "metrics": daily_bar_metrics(history, timeframe=effective_timeframe),
    }


def daily_bar_metrics(history: dict, *, timeframe: str) -> dict:
    """compute_metrics, but only where its own definitions hold.

    portfolio_metrics annualizes by a hardcoded 252 trading days and names its
    outputs mean_daily_return / daily_volatility / best_day / worst_day. That is
    correct for timeframe=1D, which is the only granularity its CLI ever passed.
    This endpoint is the first caller that can hand it 5Min bars (period=1D
    resolves to them above), and on those the ratios are off by a factor of
    sqrt(bars per day) while still being labelled "daily" -- a wrong number that
    reads as a right one. Refusing to compute is the honest answer; widening
    compute_metrics to take an annualization factor is a portfolio_metrics
    change, not a reporting-endpoint change.
    """
    if timeframe != "1D":
        return {
            "status": "NOT_APPLICABLE",
            "timeframe": timeframe,
            "reason": (
                f"risk metrics are defined on daily bars; this window is {timeframe}, "
                "and annualizing it as daily would misreport every ratio"
            ),
        }
    return portfolio_metrics.compute_metrics(history)


@app.get("/system/mode")
def system_mode(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    return trading_mode_status()


@app.get("/market-data/{symbol}")
def market_data_status(symbol: str) -> dict:
    return summarize_history(symbol, days=365, min_bars=200, allow_fallback=True)


@app.get("/market-overview")
def market_overview(
    stale_cache_hours: float = 24.0, actor: auth.Actor = Depends(get_owner_actor)
) -> dict:
    connection = db()
    try:
        return market_overview_snapshot(
            connection, stale_cache_hours=max(0.0, min(168.0, stale_cache_hours))
        )
    finally:
        connection.close()


@app.get("/news")
def news(
    symbols: str | None = None,
    limit: int = 20,
    actor: auth.Actor = Depends(get_owner_actor),
) -> dict:
    """Recent articles for the requested symbols, with optional AI summaries.

    The response is fetch_news's raw Alpaca pass-through, plus an `ai_summary`
    key on the first few articles when OpenRouter is configured. That summary
    explains the article and restates the Shariah verdict this app has already
    computed -- it never screens and never advises; see news_summarizer.py.
    """
    connection = db()
    try:
        if symbols:
            selected = [s.strip() for s in symbols.split(",") if s.strip()]
        else:
            watchlist = get_watchlist_settings(connection)["symbols"]
            positions = [
                p["symbol"] for p in portfolio_snapshot_with_exposure(connection)["positions"]
            ]
            selected = sorted(set(watchlist) | set(positions))

        try:
            result = fetch_news(selected, limit=limit)
        except Exception as e:
            result = {"news": [], "status": "unavailable", "reason": str(e)}

        # Summarized inside the connection's lifetime, unlike the contract sketch
        # in PORTFOLIO_HISTORY_AND_NEWS_CONTRACT.md which closes it first: the
        # summarizer reads each symbol's already-recorded verdict from this same
        # database, so closing early would cost it every Shariah badge.
        try:
            result["news"] = attach_ai_summaries(
                result["news"], connection=connection, requested_symbols=selected
            )
        except Exception:
            # attach_ai_summaries documents that it never raises. The endpoint
            # does not rely on that promise -- a cosmetic summary layer must
            # never turn a working articles response into a 500.
            pass
        return result
    finally:
        connection.close()


@app.get("/stock/{symbol}/profile")
def stock_profile(symbol: str) -> dict:
    connection = db()
    try:
        return stock_profile_snapshot(connection, symbol)
    finally:
        connection.close()


@app.get("/stock/{symbol}/option-strategy")
def stock_option_strategy(symbol: str, strategy: str | None = None) -> dict:
    """Propose a Level 1 contract. Selecting is not approving -- see next_step.

    Account facts come from broker_account_context, the same resolver the approval
    path uses, so settled cash (never buying power) backs a cash-secured put.
    """
    connection = db()
    try:
        account = broker_account_context(connection, {"symbol": symbol, "asset_class": "option"})
    finally:
        connection.close()
    return propose_option_strategy(symbol, account=account, strategy=strategy)


@app.get("/stock/{symbol}/explain")
def stock_explain(symbol: str) -> dict:
    """Verdict -> rule fired -> fiqh basis -> citation, for the Shariah Trace panel.

    Explains a screening decision; it never makes one. Carries a live SEC fetch
    per call until the screening store lands -- see NEXT_STEPS.md.
    """
    return explain_symbol(symbol)


@app.get("/investment-committee")
def investment_committee(limit: int = 50, actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        return investment_committee_snapshot(connection, limit=limit)
    finally:
        connection.close()


@app.get("/positions")
def positions(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        return positions_snapshot(connection)
    finally:
        connection.close()


@app.get("/watchlist")
def watchlist(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        return get_watchlist_settings(connection)
    finally:
        connection.close()


@app.post("/watchlist")
def update_watchlist(request: WatchlistRequest) -> dict:
    connection = db()
    try:
        previous = get_watchlist_settings(connection)
        settings = save_watchlist_settings(
            connection,
            symbols=request.symbols,
            alert_threshold_pct=request.alert_threshold_pct,
        )
    finally:
        connection.close()
    changed = (
        previous["symbols"] != settings["symbols"]
        or previous["alert_threshold_pct"] != settings["alert_threshold_pct"]
    )
    if changed:
        add_audit_event(
            "watchlist_updated",
            {
                "symbols": settings["symbols"],
                "alert_threshold_pct": settings["alert_threshold_pct"],
            },
        )
    return settings


@app.get("/opportunities")
def opportunities(
    symbols: str | None = None,
    alert_threshold_pct: float | None = None,
    force: bool = False,
    min_scan_interval_minutes: int = DEFAULT_SCAN_THROTTLE_MINUTES,
) -> dict:
    connection = db()
    try:
        settings = get_watchlist_settings(connection)
        snapshot = latest_scan_snapshot(
            connection, symbols=settings["symbols"] if symbols is None else symbols.split(",")
        )
    finally:
        connection.close()
    selected_symbols = symbols if symbols is not None else ",".join(settings["symbols"])
    selected_threshold = (
        alert_threshold_pct if alert_threshold_pct is not None else settings["alert_threshold_pct"]
    )
    throttle_minutes = max(0, min(120, min_scan_interval_minutes))
    if not force and throttle_minutes and snapshot is not None:
        last_scan_at = datetime.fromisoformat(snapshot["created_at"])
        if last_scan_at.tzinfo is None:
            last_scan_at = last_scan_at.replace(tzinfo=timezone.utc)
        seconds_since_scan = (datetime.now(timezone.utc) - last_scan_at).total_seconds()
        wait_seconds = max(0, int((throttle_minutes * 60) - seconds_since_scan))
        if wait_seconds > 0:
            return {
                "status": "THROTTLED",
                "throttled": True,
                "scan_id": None,
                "created_at": snapshot["created_at"],
                "last_scan_at": snapshot["created_at"],
                "wait_seconds": wait_seconds,
                "min_scan_interval_minutes": throttle_minutes,
                "alert_events": [],
                **snapshot,
            }
    scan = scan_opportunities(selected_symbols, alert_threshold_pct=selected_threshold)
    audit = add_audit_event(
        "opportunity_scan",
        {
            "count": scan["count"],
            "ready_count": scan["ready_count"],
            "alert_count": scan["alert_count"],
            "alert_threshold_pct": selected_threshold,
            "symbols": [item["symbol"] for item in scan["items"]],
        },
    )
    connection = db()
    try:
        alert_events = save_opportunity_scan(
            connection,
            scan_id=audit["id"],
            scanned_at=audit["created_at"],
            scan=scan,
        )
    finally:
        connection.close()
    return {
        "status": "SCANNED",
        "throttled": False,
        "scan_id": audit["id"],
        "created_at": audit["created_at"],
        "alert_events": alert_events,
        **scan,
    }


@app.get("/opportunity-alerts")
def opportunity_alerts(limit: int = 50, actor: auth.Actor = Depends(get_owner_actor)) -> list[dict]:
    connection = db()
    try:
        return list_alert_events(connection, limit=max(1, min(200, limit)))
    finally:
        connection.close()


@app.get("/shariah/screens")
def shariah_screens(
    symbol: str | None = None, limit: int = 50, latest_only: bool = False
) -> list[dict]:
    """The append-only record of every US screen that actually ran.

    Read-only and derived: nothing here decides anything, it reports what the
    screen already decided and when. The verdict a caller should act on is the
    one /paper/preview returns now, not the newest row in this log.

    `latest_only=true` returns the current verdict for each distinct symbol
    instead of recent activity -- one row per company, ordered by symbol. Use
    it for anything that reads as a list of companies; the default log repeats
    a symbol once per screen (14,213 rows across 16 symbols in production) and,
    being capped, can omit a symbol entirely. `symbol` and `limit` do not apply
    to it: the result is bounded by how many companies exist, not by traffic.
    """
    connection = db()
    try:
        if latest_only:
            return latest_screen_per_symbol(connection)
        return list_shariah_screens(connection, symbol=symbol, limit=max(1, min(200, limit)))
    finally:
        connection.close()


@app.get("/moomoo/status")
def moomoo_status() -> dict:
    return check_moomoo_status()


@app.post("/agent/evaluate")
def evaluate_agents(request: PaperPreviewRequest) -> dict:
    evaluation = evaluate_preview_request(request)
    audit = add_audit_event("agent_evaluation", evaluation)
    return {
        "evaluation_id": audit["id"],
        "created_at": audit["created_at"],
        "broker_submission": False,
        "evaluation": evaluation,
    }


@app.post("/paper/preview")
def preview_paper_order(
    request: PaperPreviewRequest, actor: auth.Actor = Depends(get_owner_actor)
) -> dict:
    evaluation = evaluate_preview_request(request)
    quote_snapshot = quote_snapshot_for_preview(evaluation, request)
    side = request.side.strip().upper()
    if evaluation["decision"] != "READY_FOR_APPROVAL" or evaluation["price"] is None:
        preview = {
            "status": "REJECT",
            "reason": "agent_evaluation_blocked",
            "blockers": evaluation["blockers"],
            "symbol": evaluation["symbol"],
            "quantity": evaluation["quantity"],
            "price": evaluation["price"],
            "notional": evaluation["notional"],
            "side": side,
            "broker_submission": False,
            "quote_snapshot": quote_snapshot,
            "blocker_messages": evaluation.get("blocker_messages", []),
            "agent_summary": evaluation["agent_summary"],
        }
    else:
        preview = {
            "status": "READY_FOR_APPROVAL",
            "execution": "PAPER_ONLY",
            "broker_submission": False,
            "symbol": evaluation["symbol"],
            "side": side,
            "quantity": evaluation["quantity"],
            "price": evaluation["price"],
            "notional": evaluation["notional"],
            "blockers": evaluation.get("blockers", []),
            "quote_snapshot": quote_snapshot,
            "shariah": evaluation["agent_summary"]["shariah"],
            "risk": evaluation["agent_summary"]["risk"],
            "blocker_messages": evaluation.get("blocker_messages", []),
            "agent_summary": evaluation["agent_summary"],
        }
    preview["asset_class"] = request.asset_class
    preview["option_contract"] = request.option_contract

    audit = add_audit_event("paper_preview", preview)
    return {
        "preview_id": audit["id"],
        "created_at": audit["created_at"],
        "broker_submission": False,
        "preview": preview,
    }


def broker_account_context(connection, preview: dict) -> dict:
    """Resolve the live broker facts the Shariah account/structure gates need.

    Falls back to a cash, unleveraged, empty-position view for non-Alpaca adapters
    so the legacy equity path behaves exactly as before; an option order on those
    adapters reports UNKNOWN and therefore fails closed at the gate.
    """
    settings = load_settings()
    is_option = str(preview.get("asset_class") or "equity").lower() == "option"
    if settings.paper_execution_adapter not in ALPACA_ADAPTERS:
        return {
            "account_type": "UNKNOWN" if is_option else "CASH",
            "shares_held": 0,
            "cash_collateral": 0.0,
            "uses_margin": False,
            "broker_status": "adapter_not_alpaca",
        }

    status = check_alpaca_status()
    account_type = str(status.get("account_type") or "UNKNOWN").upper()
    shares_held = open_position_quantity(
        connection,
        symbol=str(preview.get("symbol") or ""),
        account_suffix=status.get("account_suffix"),
    )
    return {
        "account_type": account_type,
        "shares_held": int(shares_held or 0),
        # Settled cash, never buying_power: margin leverage cannot back a
        # cash-secured put.
        "cash_collateral": float(status.get("cash") or 0.0),
        # We cannot prove an individual order avoids borrowed funds on a margin
        # account, so treat the account type as the conservative answer.
        "uses_margin": account_type == "MARGIN",
        # Whether the broker actually answered. Without this a failed account
        # query is indistinguishable from a real account holding nothing, and
        # the strategy layer restates a network blip as "no settled cash
        # available to secure a put" -- sending the operator after a collateral
        # problem that does not exist. The gates already fail closed on
        # account_type UNKNOWN; this is so the *reason* is honest and the caller
        # can see the condition is retryable.
        "broker_status": str(status.get("status") or "unknown"),
    }


@app.post("/paper/approval")
def approve_paper_order(
    request: PaperApprovalRequest, actor: auth.Actor = Depends(get_owner_actor)
) -> dict:
    settings = load_settings()
    preview = request.preview
    side = preview.get("side", "BUY")
    connection = db()
    try:
        account = broker_account_context(connection, preview)
        # Both server-derived, never trusted from the client-echoed preview --
        # see authoritative_shariah_verdict's and authoritative_risk_verdict's
        # docstrings for why. A client can hand-edit the preview JSON before
        # resubmitting it; nothing ties /paper/approval's request back to a
        # specific server-computed /paper/preview response.
        shariah = authoritative_shariah_verdict(preview.get("symbol"))
        risk = authoritative_risk_verdict(
            connection,
            symbol=preview.get("symbol"),
            side=side,
            quantity=preview.get("quantity"),
            price=preview.get("price"),
            asset_class=preview.get("asset_class", "equity"),
        )
    finally:
        connection.close()
    candidate = build_shariah_candidate(
        symbol=preview.get("symbol"),
        side=side,
        signal=str(side).upper() if preview.get("status") == "READY_FOR_APPROVAL" else "HOLD",
        quantity=preview.get("quantity"),
        price=preview.get("price"),
        account_type=account["account_type"],
        option_contract=preview.get("option_contract"),
        shares_held=account["shares_held"],
        cash_collateral=account["cash_collateral"],
        uses_margin=account["uses_margin"],
        shariah_override=shariah or None,
    )
    candidate["notional"] = preview.get("notional")
    shariah_trace = describe_approval(
        symbol=candidate["symbol"], shariah=shariah, candidate=candidate
    )
    if preview.get("status") != "READY_FOR_APPROVAL":
        approval = {
            "status": "REJECT",
            "reason": "preview_not_ready_for_approval",
            "broker_submission": False,
        }
    elif risk.get("status") != "PASS":
        approval = {"status": "REJECT", "reason": "risk_gate_failed", "broker_submission": False}
    else:
        approval = approve_candidate(candidate, approved_by_user=request.approved)
    approval["broker_submission"] = False
    approval["paper_execution_enabled"] = settings.paper_execution_enabled
    approval["shariah_trace"] = shariah_trace
    connection = db()
    try:
        queue_item = record_approval(
            connection,
            preview=preview,
            approval=approval,
            approved_by_user=request.approved,
            verified_shariah=shariah,
            verified_risk=risk,
        )
    finally:
        connection.close()
    payload = {"approved_by_user": request.approved, "approval": approval, "preview": preview}
    audit = add_audit_event("paper_approval", payload)
    return {
        "approval_id": audit["id"],
        "queue_id": queue_item["id"],
        "created_at": audit["created_at"],
        "broker_submission": False,
        "approval": approval,
    }


@app.post("/audit")
def record_audit(event_type: str, payload: str) -> dict:
    return add_audit_event(event_type, {"payload": payload})


@app.get("/execution-audit")
def execution_audit(limit: int = 100, actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        return execution_audit_snapshot(connection, limit=limit)
    finally:
        connection.close()


@app.get("/audit")
def list_audit(actor: auth.Actor = Depends(get_owner_actor)) -> list[dict]:
    connection = db()
    try:
        rows = connection.execute(
            "SELECT id, created_at, event_type, payload FROM audit_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


@app.get("/approvals")
def approvals(actor: auth.Actor = Depends(get_owner_actor)) -> list[dict]:
    connection = db()
    try:
        return list_approvals(connection)
    finally:
        connection.close()


@app.post("/paper/execute/{queue_id}")
def execute_paper(
    queue_id: int,
    request: PaperExecutionRequest | None = None,
    actor: auth.Actor = Depends(get_owner_actor),
) -> dict:
    if request is None or request.confirmation_phrase != PAPER_EXECUTION_CONFIRMATION:
        result = {
            "status": "CONFIRMATION_REQUIRED",
            "queue_id": queue_id,
            "message": "confirmation_phrase must exactly equal EXECUTE PAPER",
            "required_confirmation": PAPER_EXECUTION_CONFIRMATION,
            "broker_submission": False,
        }
        audit = add_audit_event("paper_execution_rejected", result)
        return {"execution_id": audit["id"], "created_at": audit["created_at"], **result}

    connection = db()
    try:
        result = execute_paper_order(connection, queue_id)
    finally:
        connection.close()
    audit = add_audit_event("paper_execution", result)
    return {"execution_id": audit["id"], "created_at": audit["created_at"], **result}


@app.post("/paper/reconcile/{queue_id}")
def reconcile_paper(queue_id: int, actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        result = reconcile_submitted_paper_order(connection, queue_id)
        portfolio_sync = None
        if result.get("status") == "BROKER_FILLED":
            approval = get_approval(connection, queue_id)
            if approval is not None:
                portfolio_sync = sync_filled_order(connection, approval)
    finally:
        connection.close()
    payload = {**result, "portfolio_sync": portfolio_sync}
    audit = add_audit_event("paper_reconciliation", payload)
    return {"reconciliation_id": audit["id"], "created_at": audit["created_at"], **payload}


def compliance_price_lookup(symbol: str) -> float | None:
    """Adapt portfolio_price_lookup's bar summary to a bare price.

    Returns None rather than raising on any failure, because
    purification_ledger reports an unpriced holding as unpriced instead of
    silently valuing it at zero -- which would understate what is owed.
    """
    try:
        summary = portfolio_price_lookup(symbol)
    except Exception:
        return None
    latest_close = summary.get("latest_close")
    return float(latest_close) if latest_close is not None else None


@app.get("/portfolio/compliance")
def portfolio_compliance(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    """Re-screen held positions against the current Shariah authority.

    Read-only and advisory. It reports what the authority currently says about
    what is held; it never trades and never decides. A NON_COMPLIANT holding is
    the owner's decision to act on, against the SC paper.
    """
    connection = db()
    try:
        screening = holdings_compliance.screen_holdings(connection)
        # Records when each alert was first raised and annotates the one-month
        # disposal deadline. Separate from screening because this one writes:
        # without a persisted first-flagged date, "dispose within one month" is
        # prose rather than something that can be measured or breached.
        screening = holdings_compliance.apply_disposal_clock(connection, screening)
        purification = holdings_compliance.purification_ledger(
            screening, price_lookup=compliance_price_lookup
        )
    finally:
        connection.close()
    return {"screening": screening, "purification": purification}


@app.get("/portfolio")
def portfolio(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        snapshot = portfolio_snapshot_with_exposure(connection)
        # Throttled to one row per 15 minutes inside record_portfolio_snapshot;
        # a fallback history source for when the live broker curve
        # (/portfolio/history/live) is unreachable, not the primary one.
        #
        # Best-effort, for the same reason sec_edgar_screen.check_us_symbol
        # swallows its own audit-log write: this is bookkeeping hanging off a
        # read, and a locked SQLite file must not turn a working portfolio
        # read into a 500. The dashboard hits this on every refresh, and
        # FastAPI runs sync handlers in a threadpool, so concurrent refreshes
        # really can collide on the write lock.
        try:
            record_portfolio_snapshot(connection, snapshot)
        except Exception:
            pass
        return snapshot
    finally:
        connection.close()


@app.get("/portfolio/history")
def portfolio_history(actor: auth.Actor = Depends(get_owner_actor)) -> dict:
    connection = db()
    try:
        return {"snapshots": list_portfolio_snapshots(connection)}
    finally:
        connection.close()


# --- Malaysian screening/eligibility API -----------------------------------
#
# Read-only. Every handler below delegates to screening_api.py, which itself
# calls straight into the same deterministic domain modules the trading gate
# chain uses (shariah_gate, sc_malaysia_store, agents.quant_agent,
# confidence, risk_checks, evidence, vault_indexer). No Shariah, risk, or
# quant logic is duplicated here -- these routes only shape HTTP responses.


@app.get("/api/universe")
def api_universe(limit: int = 200, offset: int = 0, shariah_status: str = "PASS") -> dict:
    connection = db()
    try:
        return screening_api.universe_list(
            connection, limit=limit, offset=offset, shariah_status=shariah_status
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        connection.close()


@app.get("/api/universe/publications")
def api_universe_publications() -> dict:
    connection = db()
    try:
        return screening_api.universe_publications(connection)
    finally:
        connection.close()


@app.get("/api/universe/{ticker}")
def api_universe_ticker(ticker: str) -> dict:
    connection = db()
    try:
        return screening_api.universe_ticker(connection, ticker)
    finally:
        connection.close()


@app.get("/api/screen/{ticker}")
def api_screen_ticker(ticker: str) -> dict:
    return screening_api.screen_ticker(ticker)


@app.get("/api/shariah/{ticker}")
def api_shariah_ticker(ticker: str) -> dict:
    connection = db()
    try:
        return screening_api.universe_ticker(connection, ticker)
    finally:
        connection.close()


@app.get("/api/shariah/publication/{publication_id}")
def api_shariah_publication(publication_id: str) -> dict:
    connection = db()
    try:
        detail = screening_api.publication_detail(connection, publication_id)
    finally:
        connection.close()
    if detail is None:
        raise HTTPException(status_code=404, detail="publication_not_found")
    return detail


@app.get("/api/quant/{ticker}")
def api_quant_ticker(ticker: str) -> dict:
    result = screening_api.screen_ticker(ticker)
    return {
        "ticker": result["ticker"],
        "quant": result["quant"],
        "attractiveness": result["attractiveness"],
    }


@app.get("/api/risk")
def api_risk() -> dict:
    return screening_api.risk_limits()


@app.get("/api/evidence/{ticker}")
def api_evidence_ticker(
    ticker: str,
    limit: int = 20,
    actor: auth.Actor = Depends(get_owner_actor),
) -> dict:
    """The decision trail for one ticker. Owner-only, deliberately.

    This was public, on the reasoning that it is "read-only screening evidence".
    That was true when the trail was empty. It now records `source: "preview"`
    -- orders the owner actually put through the gate chain -- alongside prices,
    blockers and the verdicts reached. That is a log of what someone is
    considering trading, which is account activity, not a screening lookup.

    Public screening remains public and unchanged: /api/shariah/{ticker} and
    /api/universe answer "is this security eligible" for anyone. This answers
    "what did this operator do", which is a different question and belongs
    behind the same door as /portfolio/compliance.
    """
    return screening_api.evidence_for_ticker(ticker, limit=max(1, min(limit, 200)))


@app.get("/api/knowledge/search")
def api_knowledge_search(q: str, limit: int = 20) -> dict:
    settings = load_settings()
    if not settings.shariah_wiki_path:
        return {"status": "vault_not_configured", "results": []}
    return screening_api.knowledge_search(
        settings.shariah_wiki_path, q, limit=max(1, min(limit, 100))
    )


class ExplainRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=16)
    question: str = Field(min_length=1, max_length=1000)


@app.post("/copilot/explain")
def api_explain(req: ExplainRequest, actor: auth.Actor = Depends(get_current_actor)) -> dict:
    try:
        return copilot_api.explain_ticker(req.ticker, req.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/research/{ticker}")
def api_research_ticker(ticker: str) -> dict:
    settings = load_settings()
    vault_path = settings.shariah_wiki_path
    return screening_api.research_intelligence_for_ticker(ticker, vault_path)


@app.post("/copilot/research")
def api_research_copilot(
    req: ExplainRequest, actor: auth.Actor = Depends(get_current_actor)
) -> dict:
    try:
        return copilot_api.research_copilot_ticker(req.ticker, req.question)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/knowledge/note/{note_path:path}")
def api_knowledge_note(note_path: str) -> dict:
    settings = load_settings()
    if not settings.shariah_wiki_path:
        raise HTTPException(status_code=404, detail="vault_not_configured")
    note = screening_api.knowledge_note(settings.shariah_wiki_path, note_path)
    if note is None:
        raise HTTPException(status_code=404, detail="note_not_found")
    return note


# --- Phase 3 Paper Portfolio Routes ---
import p3_portfolio_engine
from fastapi import Depends


@app.get("/p3/portfolios")
def api_p3_list_portfolios(actor: auth.Actor = Depends(get_current_actor)):
    connection = db()
    try:
        p3_portfolio_engine.ensure_p3_tables(connection)
        rows = connection.execute("SELECT * FROM p3_portfolios").fetchall()
        return {"portfolios": [dict(r) for r in rows]}
    finally:
        connection.close()


@app.post("/p3/portfolios")
def api_p3_create_portfolio(
    req: P3PortfolioCreateRequest, actor: auth.Actor = Depends(get_owner_actor)
):
    connection = db()
    try:
        return p3_portfolio_engine.create_portfolio(connection, req.name, req.initial_cash)
    finally:
        connection.close()


@app.get("/p3/portfolios/{portfolio_id}")
def api_p3_get_portfolio(portfolio_id: int, actor: auth.Actor = Depends(get_current_actor)):
    connection = db()
    try:
        return p3_portfolio_engine.get_portfolio(connection, portfolio_id)
    finally:
        connection.close()


@app.get("/p3/portfolios/{portfolio_id}/positions")
def api_p3_get_portfolio_positions(
    portfolio_id: int, actor: auth.Actor = Depends(get_current_actor)
):
    connection = db()
    try:
        return {"positions": p3_portfolio_engine.get_portfolio_positions(connection, portfolio_id)}
    finally:
        connection.close()


@app.get("/p3/portfolios/{portfolio_id}/orders")
def api_p3_get_portfolio_orders(portfolio_id: int, actor: auth.Actor = Depends(get_current_actor)):
    connection = db()
    try:
        return {"orders": p3_portfolio_engine.get_portfolio_orders(connection, portfolio_id)}
    finally:
        connection.close()


@app.post("/p3/portfolios/{portfolio_id}/proposals")
def api_p3_propose_order(
    portfolio_id: int, req: P3ProposalRequest, actor: auth.Actor = Depends(get_propose_actor)
):
    connection = db()
    try:
        import p3_decision_engine

        return p3_decision_engine.propose_order(
            connection,
            portfolio_id,
            req.ticker.strip().upper(),
            req.side.strip().upper(),
            actor.username,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        connection.close()


@app.post("/p3/portfolios/{portfolio_id}/orders/{order_id}/approve")
def api_p3_approve_order(
    portfolio_id: int,
    order_id: int,
    req: P3ApprovalRequest,
    actor: auth.Actor = Depends(get_approve_actor),
):
    connection = db()
    try:
        import p3_decision_engine

        return p3_decision_engine.approve_order(connection, order_id, actor.username)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        connection.close()


@app.post("/p3/portfolios/{portfolio_id}/orders/{order_id}/execute")
def api_p3_execute_order(
    portfolio_id: int,
    order_id: int,
    req: P3ApprovalRequest,
    actor: auth.Actor = Depends(get_execute_actor),
):
    connection = db()
    try:
        import p3_decision_engine

        return p3_decision_engine.execute_order(connection, order_id, actor.username)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        connection.close()
