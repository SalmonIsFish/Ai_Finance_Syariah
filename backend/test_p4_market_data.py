import pandas as pd
import pytest
from backend.p4_market_data import DataSlice

def test_data_slice_enforces_lookahead_cutoff():
    dates = pd.date_range("2024-05-20", periods=10, tz="UTC")
    df = pd.DataFrame({"Close": range(10, 20)}, index=dates)
    
    slice_cutoff = "2024-05-24 23:59:59+00:00"
    data_slice = DataSlice(signal_as_of=slice_cutoff, data=df, quality_events=[])
    
    closes = data_slice.get_closing_prices()
    
    assert len(closes) == 5
    assert closes.index[-1].strftime("%Y-%m-%d") == "2024-05-24"
    assert "2024-05-25" not in closes.index.strftime("%Y-%m-%d")
    
    # Internal df must not hold the future rows
    assert len(data_slice._data) == 5

def test_data_slice_empty_handling():
    data_slice = DataSlice(signal_as_of="2024-05-24 23:59:59+00:00", data=pd.DataFrame(), quality_events=[])
    closes = data_slice.get_closing_prices()
    assert closes.empty

def test_data_slice_timezone_aware():
    dates = pd.date_range("2024-05-20", periods=2) # tz-naive
    df = pd.DataFrame({"Close": [1, 2]}, index=dates)
    
    with pytest.raises(ValueError, match="timezone-aware"):
        DataSlice(signal_as_of="2024-05-24 23:59:59+00:00", data=df, quality_events=[])
        
    dates_tz = pd.date_range("2024-05-20", periods=2, tz="UTC")
    df_tz = pd.DataFrame({"Close": [1, 2]}, index=dates_tz)
    
    with pytest.raises(ValueError, match="timezone-aware"):
        DataSlice(signal_as_of="2024-05-24 23:59:59", data=df_tz, quality_events=[])

def test_data_slice_mutation_resistance():
    dates = pd.date_range("2024-05-20", periods=5, tz="UTC")
    df = pd.DataFrame({"Close": [10.0, 11.0, 12.0, 13.0, 14.0]}, index=dates)
    data_slice = DataSlice(signal_as_of="2024-05-25 23:59:59+00:00", data=df, quality_events=[])
    
    closes = data_slice.get_closing_prices()
    future_date = pd.to_datetime("2024-05-26 00:00:00+00:00")
    closes.loc[future_date] = 999.0
    
    fresh_closes = data_slice.get_closing_prices()
    assert future_date not in fresh_closes.index, "Mutation leaked!"
    assert len(fresh_closes) == 5

def test_data_slice_internal_mutation_resistance():
    dates = pd.date_range("2024-05-20", periods=5, tz="UTC")
    df = pd.DataFrame({"Close": [10.0, 11.0, 12.0, 13.0, 14.0]}, index=dates)
    data_slice = DataSlice(signal_as_of="2024-05-25 23:59:59+00:00", data=df, quality_events=[])
    
    # Client maliciously mutates the private backing store (e.g. via reflection/direct access)
    future_date = pd.to_datetime("2024-05-26 00:00:00+00:00")
    data_slice._data.loc[future_date] = 999.0
    
    # Public accessor must STILL re-enforce the cutoff and exclude the future row
    fresh_closes = data_slice.get_closing_prices()
    assert future_date not in fresh_closes.index, "Internal mutation leaked future data!"
    assert len(fresh_closes) == 5
