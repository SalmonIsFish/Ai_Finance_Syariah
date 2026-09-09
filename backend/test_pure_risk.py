import pytest
from backend.pure_risk import RiskState, RiskPolicy, calculate_target_quantity, LossPerUnitRequiredError, calculate_risk_exposure

def test_pure_risk_05_percent_limit():
    state = RiskState(cash=100000.0, equity=100000.0)
    policy = RiskPolicy(max_loss_per_trade_pct=0.5, max_position_pct=100.0, max_total_exposure_pct=100.0, max_sector_exposure_pct=100.0)
    
    # Missing stop loss input should fail closed (raise Exception)
    import pytest
    with pytest.raises(LossPerUnitRequiredError, match="loss_per_unit_required"):
        calculate_target_quantity(state, "AAPL", 10.0, "Tech", policy, "BUY")
    
    # Explicit loss_per_unit = 2.0. Max loss = 500. 500 / 2.0 = 250 qty.
    qty = calculate_target_quantity(state, "AAPL", 10.0, "Tech", policy, "BUY", loss_per_unit=2.0)
    assert qty == 250.0

def test_pure_risk_cash_limit():
    state = RiskState(cash=100.0, equity=100000.0)
    policy = RiskPolicy(max_loss_per_trade_pct=None, max_position_pct=100.0, max_total_exposure_pct=100.0, max_sector_exposure_pct=100.0)
    
    # max allowed notional by cash = 100
    # price = 10 -> qty = 10
    qty = calculate_target_quantity(state, "AAPL", 10.0, "Tech", policy, "BUY")
    assert qty == 10.0

def test_pure_risk_sector_limit():
    # Equity = 100k
    # Sector limit = 20% (20k)
    # Current Tech exposure = 19900
    state = RiskState(
        cash=100000.0, 
        equity=100000.0, 
        sector_exposure={"Tech": 19900.0}
    )
    policy = RiskPolicy(max_loss_per_trade_pct=None, max_position_pct=100.0, max_total_exposure_pct=100.0, max_sector_exposure_pct=20.0)
    
    # available sector notional = 100
    # price = 10 -> qty = 10
    qty = calculate_target_quantity(state, "AAPL", 10.0, "Tech", policy, "BUY")
    assert qty == 10.0

def test_pure_risk_already_over_exposure():
    state = RiskState(
        cash=100000.0,
        equity=100000.0,
        positions={"AAPL": 60.0}
    )
    # Position limit = 5% (5k). Current exposure = 600. Allowed is plenty.
    policy = RiskPolicy(max_loss_per_trade_pct=None, max_position_pct=0.5, max_total_exposure_pct=100.0, max_sector_exposure_pct=100.0)
    # Wait, max_position_pct is 0.5% (500). Current exposure = 600. So we are over exposed.
    
    qty = calculate_target_quantity(state, "AAPL", 10.0, "Tech", policy, "BUY")
    assert qty == 0.0

def test_calculate_risk_exposure():
    state = RiskState(cash=100000.0, equity=100000.0, positions={"AAPL": 10.0})
    exposure = calculate_risk_exposure(state, "AAPL", 10.0, 5.0)
    assert exposure == 150.0

def test_pure_risk_sell_side():
    state = RiskState(cash=100000.0, equity=100000.0, positions={"AAPL": 10.0})
    policy = RiskPolicy()
    qty = calculate_target_quantity(state, "AAPL", 10.0, "Tech", policy, "SELL")
    assert qty == 10.0

def test_invalid_inputs():
    state = RiskState(cash=100000.0, equity=100000.0)
    policy = RiskPolicy()
    assert calculate_target_quantity(state, "AAPL", -5.0, "Tech", policy, "BUY") == 0.0
    assert calculate_target_quantity(state, "AAPL", 0.0, "Tech", policy, "BUY") == 0.0
