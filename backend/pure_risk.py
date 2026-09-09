from dataclasses import dataclass, field
from typing import Dict, Optional

@dataclass
class RiskState:
    cash: float
    equity: float
    positions: Dict[str, float] = field(default_factory=dict)
    sector_exposure: Dict[str, float] = field(default_factory=dict)
    realized_pnl: float = 0.0
    orders_today: int = 0
    total_exposure: float = 0.0

@dataclass
class RiskPolicy:
    max_position_pct: float = 5.0
    max_total_exposure_pct: float = 25.0
    max_sector_exposure_pct: float = 20.0
    max_loss_per_trade_pct: Optional[float] = None

def calculate_risk_exposure(state: RiskState, candidate: str, price: float, target_qty: float) -> float:
    """Calculates the new exposure if target_qty is added."""
    current_qty = state.positions.get(candidate, 0.0)
    return (current_qty + target_qty) * price

class LossPerUnitRequiredError(ValueError):
    pass

def calculate_target_quantity(state: RiskState, candidate: str, price: float, sector: str, policy: RiskPolicy, side: str = "BUY", loss_per_unit: Optional[float] = None) -> float:
    """
    Pure mathematical function enforcing P3 risk constraints.
    Returns the maximum allowable quantity.
    """
    if price <= 0:
        return 0.0

    existing_qty = state.positions.get(candidate, 0.0)
    
    if side == "SELL":
        return existing_qty
        
    # Sizing constraints (in terms of allowed quantity)
    
    # 1. Cash limit
    cash_limit_qty = state.cash / price
    
    # 2. Position limit
    existing_exposure = existing_qty * price
    max_position_exposure = state.equity * (policy.max_position_pct / 100.0)
    allowed_position_add_exposure = max(0.0, max_position_exposure - existing_exposure)
    position_limit_qty = allowed_position_add_exposure / price
    
    # 3. Total exposure limit
    max_total_exposure = state.equity * (policy.max_total_exposure_pct / 100.0)
    allowed_total_add_exposure = max(0.0, max_total_exposure - state.total_exposure)
    total_exposure_qty = allowed_total_add_exposure / price
    
    # 4. Sector limit
    current_sector_exposure = state.sector_exposure.get(sector, 0.0)
    max_sector_exposure = state.equity * (policy.max_sector_exposure_pct / 100.0)
    allowed_sector_add_exposure = max(0.0, max_sector_exposure - current_sector_exposure)
    sector_limit_qty = allowed_sector_add_exposure / price
    
    # 5. Max loss per trade rule
    if policy.max_loss_per_trade_pct is not None and policy.max_loss_per_trade_pct > 0.0:
        if loss_per_unit is None or loss_per_unit <= 0.0:
            raise LossPerUnitRequiredError("loss_per_unit_required")
        max_loss_exposure = state.equity * (policy.max_loss_per_trade_pct / 100.0)
        per_trade_loss_qty = max_loss_exposure / loss_per_unit
    else:
        per_trade_loss_qty = float('inf')
    
    maximum_quantity = min(
        cash_limit_qty,
        position_limit_qty,
        total_exposure_qty,
        sector_limit_qty,
        per_trade_loss_qty
    )
    
    return max(0.0, maximum_quantity)

