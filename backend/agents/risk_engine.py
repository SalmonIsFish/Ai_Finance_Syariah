"""Deterministic risk engine for paper-order eligibility."""

from config import load_settings
from risk_checks import check_order


def evaluate_risk(
    *,
    position_pct: float,
    total_exposure_pct: float,
    loss_per_trade_pct: float,
    daily_loss_pct: float,
    orders_today: int,
    weekly_loss_pct: float | None = None,
) -> dict:
    settings = load_settings()
    result = check_order(
        position_pct=position_pct,
        total_exposure_pct=total_exposure_pct,
        loss_per_trade_pct=loss_per_trade_pct,
        daily_loss_pct=daily_loss_pct,
        orders_today=orders_today,
        weekly_loss_pct=weekly_loss_pct,
        limits={
            "max_position_pct": settings.max_position_pct,
            "max_total_exposure_pct": settings.max_total_exposure_pct,
            "max_loss_per_trade_pct": settings.max_loss_per_trade_pct,
            "max_daily_loss_pct": settings.max_daily_loss_pct,
            "max_weekly_loss_pct": settings.max_weekly_loss_pct,
            "max_orders_per_day": settings.max_orders_per_day,
        },
    )
    return {
        "agent": "risk_engine",
        "status": result.get("status"),
        "reason": "risk_limits_passed" if result.get("status") == "PASS" else "risk_limit_failed",
        "details": result,
    }
