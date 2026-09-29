from dataclasses import dataclass, field
from typing import Dict, Optional

from numeric_guards import first_non_finite


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


def calculate_risk_exposure(
    state: RiskState, candidate: str, price: float, target_qty: float
) -> float:
    """Calculates the new exposure if target_qty is added."""
    current_qty = state.positions.get(candidate, 0.0)
    return (current_qty + target_qty) * price


class LossPerUnitRequiredError(ValueError):
    pass


class NonFiniteRiskInputError(ValueError):
    """A sizing input could not be computed, so nothing is sized.

    The result is min(cash, position, total, sector, per_trade_loss). `min` keeps its
    running best unless a later value compares smaller, and every comparison against NaN
    is False -- so a NaN after the first argument was silently dropped, and `max(0.0, inf)`
    passed an infinite cap straight through. loss_per_unit=nan sized 10x over the per-trade
    loss cap; equity=inf sized to all available cash with every percentage cap gone. The
    inputs that did fail closed did so by accident of argument order.

    `field` names the input, so the refusal can say what could not be computed.
    """

    def __init__(self, field: str):
        super().__init__("non_finite_risk_input")
        self.field = field


def _refuse_non_finite(**values) -> None:
    bad = first_non_finite(**values)
    if bad is not None:
        raise NonFiniteRiskInputError(bad)


def calculate_target_quantity(
    state: RiskState,
    candidate: str,
    price: float,
    sector: str,
    policy: RiskPolicy,
    side: str = "BUY",
    loss_per_unit: Optional[float] = None,
) -> float:
    """
    Pure mathematical function enforcing P3 risk constraints.
    Returns the maximum allowable quantity.
    """
    existing_qty = state.positions.get(candidate, 0.0)

    # Before the SELL branch: a SELL returns the held quantity as the order size.
    _refuse_non_finite(price=price, position=existing_qty)

    if price <= 0:
        return 0.0

    if side == "SELL":
        return existing_qty

    current_sector_exposure = state.sector_exposure.get(sector, 0.0)
    _refuse_non_finite(
        cash=state.cash,
        equity=state.equity,
        total_exposure=state.total_exposure,
        sector_exposure=current_sector_exposure,
        max_position_pct=policy.max_position_pct,
        max_total_exposure_pct=policy.max_total_exposure_pct,
        max_sector_exposure_pct=policy.max_sector_exposure_pct,
    )
    if policy.max_loss_per_trade_pct is not None:
        _refuse_non_finite(max_loss_per_trade_pct=policy.max_loss_per_trade_pct)

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
    max_sector_exposure = state.equity * (policy.max_sector_exposure_pct / 100.0)
    allowed_sector_add_exposure = max(0.0, max_sector_exposure - current_sector_exposure)
    sector_limit_qty = allowed_sector_add_exposure / price

    # 5. Max loss per trade rule
    if policy.max_loss_per_trade_pct is not None and policy.max_loss_per_trade_pct > 0.0:
        if loss_per_unit is None:
            raise LossPerUnitRequiredError("loss_per_unit_required")
        _refuse_non_finite(loss_per_unit=loss_per_unit)
        if loss_per_unit <= 0.0:
            raise LossPerUnitRequiredError("loss_per_unit_required")
        max_loss_exposure = state.equity * (policy.max_loss_per_trade_pct / 100.0)
        per_trade_loss_qty = max_loss_exposure / loss_per_unit
    else:
        # Deliberate: no loss cap configured. Every other term is finite by now, so this
        # sentinel can never be the value `min` returns.
        per_trade_loss_qty = float("inf")

    maximum_quantity = min(
        cash_limit_qty, position_limit_qty, total_exposure_qty, sector_limit_qty, per_trade_loss_qty
    )

    return max(0.0, maximum_quantity)
