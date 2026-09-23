"""Deterministic, non-AI risk checks for paper-order preparation."""

from numeric_guards import is_finite_number

MAX_POSITION_PCT = 5.0
MAX_TOTAL_EXPOSURE_PCT = 25.0
MAX_LOSS_PER_TRADE_PCT = 0.5
MAX_DAILY_LOSS_PCT = 1.0
MAX_WEEKLY_LOSS_PCT = 2.0
MAX_ORDERS_PER_DAY = 5


def default_limits() -> dict:
    return {
        "max_position_pct": MAX_POSITION_PCT,
        "max_total_exposure_pct": MAX_TOTAL_EXPOSURE_PCT,
        "max_loss_per_trade_pct": MAX_LOSS_PER_TRADE_PCT,
        "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
        "max_weekly_loss_pct": MAX_WEEKLY_LOSS_PCT,
        "max_orders_per_day": MAX_ORDERS_PER_DAY,
    }


def check_order(
    *,
    position_pct: float,
    total_exposure_pct: float,
    loss_per_trade_pct: float,
    daily_loss_pct: float,
    orders_today: int,
    weekly_loss_pct: float | None = None,
    limits: dict | None = None,
) -> dict:
    """weekly_loss_pct is optional and defaults to not-evaluated (omitted from
    `checks`, no effect on `status`) so existing callers that have not been
    updated to compute it are unaffected. A caller enforcing the weekly-loss
    hard limit passes the computed percentage; pass float('inf') rather than
    None when the caller has determined the data needed to compute it is
    unavailable, so the check fails closed (inf can never be <= a finite
    limit) instead of silently being skipped.
    """
    active_limits = {**default_limits(), **(limits or {})}

    def within(value, limit) -> bool:
        """True only when a real number is genuinely under the limit.

        The finiteness requirement preserves the `inf` idiom this function's docstring
        documents -- `inf` still fails, because it is not finite rather than because
        `inf <= limit` happens to be False -- and closes the two cases that did not
        work: `nan`, which compares False by accident rather than by rule, and `-inf`,
        which compared True and passed as "comfortably under the limit".

        A value that could not be computed is not a value that is within a limit.
        """
        return is_finite_number(value) and value <= limit

    checks = {
        "position_ceiling": within(position_pct, active_limits["max_position_pct"]),
        "total_exposure": within(total_exposure_pct, active_limits["max_total_exposure_pct"]),
        "loss_per_trade": within(loss_per_trade_pct, active_limits["max_loss_per_trade_pct"]),
        "daily_loss": within(daily_loss_pct, active_limits["max_daily_loss_pct"]),
        "daily_order_cap": orders_today < active_limits["max_orders_per_day"],
    }
    if weekly_loss_pct is not None:
        checks["weekly_loss"] = within(weekly_loss_pct, active_limits["max_weekly_loss_pct"])
    return {
        "status": "PASS" if all(checks.values()) else "REJECT",
        "checks": checks,
        "limits": active_limits,
    }
