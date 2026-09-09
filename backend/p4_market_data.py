from typing import Protocol, List, Dict, Any
from dataclasses import dataclass
import pandas as pd

@dataclass
class DataQualityEvent:
    security_id: str
    ticker: str
    date: str
    event_type: str # 'MISSING_VOLUME', 'MISSING_PRICE', 'UNSUPPORTED_CORP_ACTION'
    severity: str # 'WARNING', 'FATAL'
    details: str

class HistoricalMarketDataProvider(Protocol):
    """
    Protocol defining the contract for historical market data retrieval.
    The provider is responsible for ensuring prices are properly adjusted.
    """
    def get_ohlcv(self, security_id: str, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """
        Returns a DataFrame with at least columns: ['Open', 'High', 'Low', 'Close', 'Volume'].
        Index should be pandas datetime.
        """
        ...

    def get_corporate_actions(self, security_id: str, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Returns a list of corporate actions (splits, dividends) for the given date range.
        """
        ...

class YahooFinanceHistoricalProvider:
    """
    V1 implementation using Yahoo Finance.
    LIMITATION: Inherently suffers from survivorship bias (delisted stocks are dropped).
    LIMITATION: Does not natively support point-in-time ticker changes accurately.
    LIMITATION: Historical snapshots are not immutable, making runs LIMITED_REPRODUCIBILITY.
    """
    def get_ohlcv(self, security_id: str, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        # Stub implementation. The backtest engine is deferred to Phase 4B.
        return pd.DataFrame()

    def get_corporate_actions(self, security_id: str, ticker: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        # Stub implementation.
        return []

class DataSlice:
    """
    A rigorously enforced window into historical data that absolutely prevents look-ahead bias.
    """
    signal_as_of: str # e.g. "2024-05-24 23:59:59+00:00"
    quality_events: List[DataQualityEvent]
    
    def __init__(self, signal_as_of: str, data: pd.DataFrame, quality_events: List[DataQualityEvent]):
        self.signal_as_of = signal_as_of
        self.quality_events = quality_events
        
        cutoff = pd.to_datetime(self.signal_as_of)
        if cutoff.tzinfo is None:
            raise ValueError("signal_as_of must be timezone-aware.")
            
        if not data.empty:
            if data.index.tzinfo is None:
                raise ValueError("DataFrame index must be timezone-aware.")
            self._data = data[data.index <= cutoff].copy()
        else:
            self._data = pd.DataFrame()
    
    def get_closing_prices(self) -> pd.Series:
        """
        Returns closing prices guaranteed to be available <= signal_as_of.
        """
        if self._data.empty:
            return pd.Series(dtype=float)
        
        # Re-enforce cutoff against internal state mutation
        cutoff = pd.to_datetime(self.signal_as_of)
        valid_data = self._data[self._data.index <= cutoff]
        return valid_data['Close'].copy()
