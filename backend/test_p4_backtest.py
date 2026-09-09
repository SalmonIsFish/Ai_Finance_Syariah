import sqlite3
import pytest
import pandas as pd
from typing import List, Dict, Optional
import ast
from pathlib import Path

from backend.p4_backtest import (
    run_backtest, BacktestAssumptions, SimulatedPosition, TargetOrder,
    StrategyProtocol, MarketDataProvider
)
from backend.p4_market_data import DataSlice
from backend.pure_risk import RiskPolicy

class MockMarketDataProvider:
    def __init__(self, data: pd.DataFrame, exec_data: Dict[str, Dict[str, Dict]]):
        self.data = data
        self.exec_data = exec_data

    def get_data_slice(self, signal_as_of: str) -> DataSlice:
        cutoff = pd.to_datetime(signal_as_of)
        valid_data = self.data[self.data.index <= cutoff].copy()
        return DataSlice(signal_as_of, valid_data, [])

    def get_execution_data(self, ticker: str, date_iso: str) -> Optional[Dict]:
        return self.exec_data.get(date_iso, {}).get(ticker)

    def is_deterministic(self) -> bool:
        return True

class MockStrategy:
    def __init__(self, orders: List[TargetOrder]):
        self.orders_to_return = orders
        self.received_data = []

    def generate_orders(self, data_slice: DataSlice, portfolio: Dict[str, SimulatedPosition], cash: float) -> List[TargetOrder]:
        self.received_data.append(data_slice.get_closing_prices())
        return self.orders_to_return

@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    
    # Setup SC tables
    conn.execute("CREATE TABLE sc_publications (id TEXT PRIMARY KEY, effective_date TEXT, publication_date TEXT, human_review_status TEXT)")
    conn.execute("CREATE TABLE sc_security_status (publication_id TEXT, ticker TEXT, shariah_status TEXT, sector TEXT)")
    conn.execute("INSERT INTO sc_publications VALUES ('pub1', '2023-01-01', '2023-01-01', 'approved')")
    conn.execute("INSERT INTO sc_publications VALUES ('pub2', '2023-06-01', '2023-06-01', 'pending')") # Pending pub
    conn.execute("INSERT INTO sc_security_status VALUES ('pub1', 'VALID_TK', 'Compliant', 'Tech')")
    conn.execute("INSERT INTO sc_security_status VALUES ('pub1', 'REJECT_TK', 'Non-Compliant', 'Finance')")
    conn.execute("INSERT INTO sc_security_status VALUES ('pub1', 'UNKNOWN_TK', 'Unknown', 'Unknown')")
    
    # Setup Identity tables
    conn.execute("CREATE TABLE p4_security_identity (security_id TEXT PRIMARY KEY, issuer_name TEXT)")
    conn.execute("CREATE TABLE p4_historical_tickers (id INTEGER PRIMARY KEY, security_id TEXT, historical_ticker TEXT, exchange TEXT, valid_from TEXT, valid_to TEXT, status TEXT)")
    conn.execute("INSERT INTO p4_security_identity VALUES ('sec1', 'Valid Co')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec1', 'VALID_TK', 'MYX', '2020-01-01', NULL, 'ACTIVE')")
    conn.execute("INSERT INTO p4_security_identity VALUES ('sec2', 'Reject Co')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec2', 'REJECT_TK', 'MYX', '2020-01-01', NULL, 'ACTIVE')")
    conn.execute("INSERT INTO p4_security_identity VALUES ('sec3', 'Unknown Co')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec3', 'UNKNOWN_TK', 'MYX', '2020-01-01', NULL, 'ACTIVE')")
    # AMBIG_TK maps to two securities
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec4', 'AMBIG_TK', 'MYX', '2020-01-01', NULL, 'ACTIVE')")
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec5', 'AMBIG_TK', 'MYX', '2020-01-01', NULL, 'ACTIVE')")
    conn.execute("INSERT INTO sc_security_status VALUES ('pub1', 'AMBIG_TK', 'Compliant', 'Tech')")
    
    # PEND_TK only compliant in pending pub
    conn.execute("INSERT INTO p4_historical_tickers (security_id, historical_ticker, exchange, valid_from, valid_to, status) VALUES ('sec6', 'PEND_TK', 'MYX', '2020-01-01', NULL, 'ACTIVE')")
    conn.execute("INSERT INTO sc_security_status VALUES ('pub2', 'PEND_TK', 'Compliant', 'Tech')")
    
    conn.commit()
    return conn

def test_no_lookahead_and_reproducibility(db_conn):
    dates = pd.date_range("2023-01-01", periods=3, tz="UTC")
    df = pd.DataFrame({"Close": [10.0, 11.0, 12.0]}, index=dates)
    exec_data = {
        "2023-01-02": {"VALID_TK": {"open": 11.0, "volume": 10000}},
        "2023-01-03": {"VALID_TK": {"open": 12.0, "volume": 10000}}
    }
    provider = MockMarketDataProvider(df, exec_data)
    strategy = MockStrategy([TargetOrder("VALID_TK", "BUY", 100, 1.0)])
    
    assumptions = BacktestAssumptions(10000.0, 0.0, 0.0, 0.0, RiskPolicy(max_loss_per_trade_pct=0.5), "Fixture", 25.0)
    
    res = run_backtest(db_conn, strategy, "s1", "2023-01-02", "2023-01-03", assumptions, provider)
    
    # Ensure strategy didn't see T data
    assert len(strategy.received_data) == 2
    assert "2023-01-02" not in strategy.received_data[0].index.strftime("%Y-%m-%d")
    assert "2023-01-03" not in strategy.received_data[1].index.strftime("%Y-%m-%d")
    
    assert res.is_reproducible is True
    assert len(res.fills) > 0

def test_pending_sc_and_bad_identities(db_conn):
    exec_data = {"2023-01-02": {
        "REJECT_TK": {"open": 10.0, "volume": 1000},
        "UNKNOWN_TK": {"open": 10.0, "volume": 1000},
        "AMBIG_TK": {"open": 10.0, "volume": 1000},
        "PEND_TK": {"open": 10.0, "volume": 1000}
    }}
    provider = MockMarketDataProvider(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC")), exec_data)
    strategy = MockStrategy([
        TargetOrder("REJECT_TK", "BUY", 10),
        TargetOrder("UNKNOWN_TK", "BUY", 10),
        TargetOrder("AMBIG_TK", "BUY", 10),
        TargetOrder("PEND_TK", "BUY", 10)
    ])
    assumptions = BacktestAssumptions(10000.0, 0.0, 0.0, 0.0, RiskPolicy(max_loss_per_trade_pct=0.0), "Fixture", 25.0)
    
    res = run_backtest(db_conn, strategy, "s1", "2023-01-02", "2023-01-02", assumptions, provider)
    
    assert len(res.fills) == 0
    rejections = [e for e in res.events if e.event_type == "REJECTION"]
    data_qual = [e for e in res.events if e.event_type == "DATA_QUALITY"]
    
    # REJECT_TK & UNKNOWN_TK & PEND_TK rejected by SC. PEND_TK has no active pub1 status (Unknown -> Reject)
    assert any("SC Status REJECT" in e.details for e in rejections if e.security == "REJECT_TK")
    assert any("SC Status UNKNOWN" in e.details for e in rejections if e.security == "UNKNOWN_TK")
    assert any("SC Status UNKNOWN" in e.details for e in rejections if e.security == "PEND_TK")
    
    # AMBIG_TK fails identity
    assert any("ambiguous" in e.details for e in data_qual if e.security == "AMBIG_TK")

def test_adv_cap_participation(db_conn):
    exec_data = {"2023-01-02": {"VALID_TK": {"open": 10.0, "volume": 1000}}} # 25% is 250 shares
    provider = MockMarketDataProvider(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC")), exec_data)
    strategy = MockStrategy([TargetOrder("VALID_TK", "BUY", 400)]) # Request 200
    assumptions = BacktestAssumptions(10000.0, 0.0, 0.0, 0.0, RiskPolicy(max_loss_per_trade_pct=0.0), "Fixture", 25.0)
    
    res = run_backtest(db_conn, strategy, "s1", "2023-01-02", "2023-01-02", assumptions, provider)
    
    assert len(res.fills) == 1
    assert res.fills[0].quantity == 250
    assert any(e.event_type == "PARTIAL_FILL" for e in res.events)

def test_slippage_and_costs(db_conn):
    exec_data = {"2023-01-02": {"VALID_TK": {"open": 100.0, "volume": 10000}}}
    provider = MockMarketDataProvider(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC")), exec_data)
    strategy = MockStrategy([TargetOrder("VALID_TK", "BUY", 10)])
    
    # Slippage 1%, cost 1% + 5
    assumptions = BacktestAssumptions(10000.0, 1.0, 1.0, 5.0, RiskPolicy(max_loss_per_trade_pct=0.0), "Fixture", 25.0)
    res = run_backtest(db_conn, strategy, "s1", "2023-01-02", "2023-01-02", assumptions, provider)
    
    fill = res.fills[0]
    assert fill.price == 101.0 # 100 * 1.01
    assert fill.transaction_cost == (10 * 101.0 * 0.01) + 5.0

def test_missing_price_volume(db_conn):
    exec_data = {"2023-01-02": {"VALID_TK": {"open": None, "volume": None}}}
    provider = MockMarketDataProvider(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC")), exec_data)
    strategy = MockStrategy([TargetOrder("VALID_TK", "BUY", 10)])
    assumptions = BacktestAssumptions(10000.0, 0.0, 0.0, 0.0, RiskPolicy(max_loss_per_trade_pct=0.0), "Fixture", 25.0)
    
    res = run_backtest(db_conn, strategy, "s1", "2023-01-02", "2023-01-02", assumptions, provider)
    assert len(res.fills) == 0
    assert any("Missing execution price/volume" in e.details for e in res.events)

def test_corporate_actions(db_conn):
    exec_data = {
        "2023-01-02": {"VALID_TK": {"open": 10.0, "volume": 10000}},
        "2023-01-03": {"VALID_TK": {"open": 5.0, "volume": 10000, "split_ratio": 2.0, "dividend": 1.0}}
    }
    provider = MockMarketDataProvider(pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC")), exec_data)
    
    # Buy on 01-02, hold on 01-03
    class CorpStrategy:
        def generate_orders(self, data_slice, portfolio, cash):
            if "2023-01-01" in data_slice.signal_as_of: # End of 01-01, signals for 01-02
                return [TargetOrder("VALID_TK", "BUY", 10)]
            return []
            
    assumptions = BacktestAssumptions(10000.0, 0.0, 0.0, 0.0, RiskPolicy(max_loss_per_trade_pct=0.0), "Fixture", 25.0)
    res = run_backtest(db_conn, CorpStrategy(), "s1", "2023-01-02", "2023-01-03", assumptions, provider)
    
    pos = res.final_positions["VALID_TK"]
    assert pos.quantity == 20 # 10 * 2 (split)
    assert pos.average_cost == 5.0 # 10 / 2 (split)
    
    # Cash: 10000 - 100 (buy) = 9900.
    # Dividend of 1.0 applied to 10 shares on morning of 01-03 before split?
    # Wait, in the logic I process corp actions on T for positions at end of T-1.
    # On 01-03 morning, we have 10 shares. div=1.0. Cash += 10 * 1.0 = 10. Total cash = 9910.
    assert res.final_cash == 9910.0
    
def test_ast_isolation():
    p4_file = Path("backend/p4_backtest.py")
    tree = ast.parse(p4_file.read_text(encoding="utf-8"))
    
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "p3_" not in alias.name
                assert "alpaca" not in alias.name
                assert "moomoo" not in alias.name
                assert "fastapi" not in alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert "p3_" not in node.module
                assert "alpaca" not in node.module
                assert "moomoo" not in node.module
                assert "fastapi" not in node.module
