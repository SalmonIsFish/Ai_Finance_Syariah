import sqlite3
import math
from typing import List, Dict, Optional, Protocol
from dataclasses import dataclass, field
import pandas as pd
from datetime import datetime, timezone, timedelta
from backend.p4_market_data import DataSlice
from backend.p4_historical_sc import authoritative_publication_as_of, get_historical_shariah_status, NoAuthoritativeUniverseError
from backend.p4_identity import resolve_historical_ticker
from backend.pure_risk import RiskPolicy

@dataclass(frozen=True)
class BacktestAssumptions:
    starting_cash: float
    slippage_pct: float
    transaction_cost_pct: float
    transaction_cost_fixed: float
    risk_policy: RiskPolicy
    provider_name: str

@dataclass
class TargetOrder:
    ticker: str
    side: str
    quantity: float
    loss_per_unit: Optional[float] = None

@dataclass
class Fill:
    ticker: str
    side: str
    quantity: float
    price: float
    transaction_cost: float
    timestamp: str

@dataclass
class SimulatedPosition:
    ticker: str
    quantity: float
    average_cost: float

@dataclass
class BacktestEvent:
    timestamp: str
    event_type: str
    security: str
    details: str

@dataclass
class BacktestResult:
    strategy_id: str
    assumptions: BacktestAssumptions
    final_cash: float
    final_positions: Dict[str, SimulatedPosition]
    realized_pnl: float
    fills: List[Fill]
    events: List[BacktestEvent]
    is_reproducible: bool

class StrategyProtocol(Protocol):
    def generate_orders(self, data_slice: DataSlice, portfolio: Dict[str, SimulatedPosition], cash: float) -> List[TargetOrder]:
        ...

class MarketDataProvider(Protocol):
    def get_data_slice(self, signal_as_of: str) -> DataSlice:
        ...
    def get_execution_data(self, ticker: str, date_iso: str) -> Optional[Dict]:
        """Returns {'open': float, 'volume': float, 'dividend': float, 'split_ratio': float}"""
        ...

def run_backtest(
    db_conn: sqlite3.Connection,
    strategy: StrategyProtocol,
    strategy_id: str,
    start_date: str,
    end_date: str,
    assumptions: BacktestAssumptions,
    provider: MarketDataProvider,
    exchange: str = "MYX"
) -> BacktestResult:
    
    cash = assumptions.starting_cash
    positions: Dict[str, SimulatedPosition] = {}
    realized_pnl = 0.0
    fills: List[Fill] = []
    events: List[BacktestEvent] = []
    
    start_dt = pd.to_datetime(start_date, utc=True)
    end_dt = pd.to_datetime(end_date, utc=True)
    
    current_dt = start_dt
    while current_dt <= end_dt:
        t_iso = current_dt.strftime("%Y-%m-%d")
        t_minus_1_cutoff = (current_dt - timedelta(days=1)).strftime("%Y-%m-%d") + "T23:59:59Z"
        execution_timestamp = t_iso + "T09:00:00Z"
        
        # 1. Pre-execution Corporate Actions
        for ticker in list(positions.keys()):
            exec_data = provider.get_execution_data(ticker, t_iso)
            if exec_data:
                div = exec_data.get('dividend', 0.0)
                if div > 0:
                    cash += div * positions[ticker].quantity
                    events.append(BacktestEvent(execution_timestamp, "CORP_ACTION", ticker, f"Dividend {div}"))
                
                split = exec_data.get('split_ratio', 1.0)
                if split != 1.0 and split > 0:
                    pos = positions[ticker]
                    pos.quantity *= split
                    pos.average_cost /= split
                    events.append(BacktestEvent(execution_timestamp, "CORP_ACTION", ticker, f"Split {split}"))
            
        # 2. Strategy Signal Phase
        data_slice = provider.get_data_slice(t_minus_1_cutoff)
        
        try:
            target_orders = strategy.generate_orders(data_slice, dict(positions), cash)
        except Exception as e:
            events.append(BacktestEvent(t_minus_1_cutoff, "DATA_QUALITY", "SYSTEM", f"Strategy crashed: {str(e)}"))
            target_orders = []

        # 3. Execution Phase
        try:
            pub = authoritative_publication_as_of(db_conn, t_iso, t_minus_1_cutoff)
            pub_id = pub["id"]
        except NoAuthoritativeUniverseError:
            pub_id = None
            events.append(BacktestEvent(execution_timestamp, "REJECTION", "ALL", "No authoritative SC publication"))
            target_orders = []
            
        for order in target_orders:
            if order.quantity <= 0:
                continue
                
            if pub_id is None:
                continue
                
            sc_status = get_historical_shariah_status(db_conn, order.ticker, pub_id)
            if sc_status["status"] != "PASS":
                events.append(BacktestEvent(execution_timestamp, "REJECTION", order.ticker, f"SC Status {sc_status['status']}"))
                continue
                
            sec_id = resolve_historical_ticker(db_conn, order.ticker, exchange, t_iso)
            if not sec_id:
                events.append(BacktestEvent(execution_timestamp, "DATA_QUALITY", order.ticker, "Unresolved/ambiguous identity"))
                continue
                
            exec_data = provider.get_execution_data(order.ticker, t_iso)
            if not exec_data or exec_data.get("open") is None or exec_data.get("volume") is None:
                events.append(BacktestEvent(execution_timestamp, "DATA_QUALITY", order.ticker, "Missing execution price/volume"))
                continue
                
            if assumptions.risk_policy.max_loss_per_trade_pct is not None and assumptions.risk_policy.max_loss_per_trade_pct > 0.0:
                if order.loss_per_unit is None or order.loss_per_unit <= 0:
                    events.append(BacktestEvent(execution_timestamp, "REJECTION", order.ticker, "Missing loss_per_unit"))
                    continue
            
            volume = exec_data["volume"]
            max_exec_shares = volume * 0.10
            exec_qty = min(order.quantity, max_exec_shares)
            
            if exec_qty < order.quantity:
                events.append(BacktestEvent(execution_timestamp, "PARTIAL_FILL", order.ticker, f"ADV Cap: requested {order.quantity}, max {max_exec_shares}"))
                
            if exec_qty <= 0:
                events.append(BacktestEvent(execution_timestamp, "DATA_QUALITY", order.ticker, "Zero liquidity"))
                continue
                
            raw_price = exec_data["open"]
            if order.side == "BUY":
                exec_price = raw_price * (1 + assumptions.slippage_pct / 100.0)
            else:
                exec_price = raw_price * (1 - assumptions.slippage_pct / 100.0)
                
            t_cost = (exec_qty * exec_price * (assumptions.transaction_cost_pct / 100.0)) + assumptions.transaction_cost_fixed
            
            if order.side == "BUY":
                total_cost = (exec_qty * exec_price) + t_cost
                if cash < total_cost:
                    events.append(BacktestEvent(execution_timestamp, "REJECTION", order.ticker, "Insufficient cash"))
                    continue
                
                cash -= total_cost
                if order.ticker in positions:
                    pos = positions[order.ticker]
                    new_qty = pos.quantity + exec_qty
                    new_cost = ((pos.quantity * pos.average_cost) + (exec_qty * exec_price)) / new_qty
                    pos.quantity = new_qty
                    pos.average_cost = new_cost
                else:
                    positions[order.ticker] = SimulatedPosition(order.ticker, exec_qty, exec_price)
                    
            elif order.side == "SELL":
                if order.ticker not in positions or positions[order.ticker].quantity < exec_qty:
                    events.append(BacktestEvent(execution_timestamp, "REJECTION", order.ticker, "No position to sell"))
                    continue
                        
                pos = positions[order.ticker]
                proceeds = (exec_qty * exec_price) - t_cost
                cash += proceeds
                
                realized_pnl += (exec_price - pos.average_cost) * exec_qty
                
                pos.quantity -= exec_qty
                if pos.quantity == 0:
                    del positions[order.ticker]
                    
            fills.append(Fill(order.ticker, order.side, exec_qty, exec_price, t_cost, execution_timestamp))
            
        current_dt += timedelta(days=1)
        
    return BacktestResult(
        strategy_id=strategy_id,
        assumptions=assumptions,
        final_cash=cash,
        final_positions=positions,
        realized_pnl=realized_pnl,
        fills=fills,
        events=events,
        is_reproducible="Yahoo" not in assumptions.provider_name
    )
