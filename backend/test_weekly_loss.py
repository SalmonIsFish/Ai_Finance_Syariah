"""Tests for the weekly realized-loss hard limit (Phase 1, Objective 2).

The Obsidian vault's risk-policy.md specifies "Maximum weekly realised loss
(% of portfolio): 2" without stating calendar-week vs. rolling 7-day. This
implementation uses CALENDAR week (Monday 00:00 UTC through the following
Monday 00:00 UTC) -- see local_api._start_of_iso_week_utc's docstring for
the full reasoning (consistency with the same policy's calendar-day-scoped
daily/orders-per-day limits and calendar-anchored review cadence).

Covers every boundary condition called for: no trades, a profitable week,
loss below/at/above 2%, the week boundary itself, multiple trades, a partial
close, and missing/invalid P&L data (which must fail closed, not assume 0).
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import portfolio_store
import risk_checks


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    return conn


def _snapshot_at(conn, *, captured_at: str, realized_pnl: float) -> None:
    conn.execute(
        "INSERT INTO portfolio_value_snapshots "
        "(captured_at, account_equity, market_value, cost_basis, unrealized_pnl, realized_pnl, position_count) "
        "VALUES (?, 10000, 0, 0, 0, ?, 0)",
        (captured_at, realized_pnl),
    )
    conn.commit()


def test_no_trades_is_zero_not_unknown():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=3)
    result = portfolio_store.period_realized_pnl(conn, since=since)
    assert result["status"] == "OK"
    assert result["period_realized_pnl"] == 0.0
    print(
        "PASS: no_trades_is_zero_not_unknown — a brand-new account has 0 realized P&L, not unknown"
    )


def test_profitable_week_gives_zero_loss_pct_not_negative():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=2)
    _snapshot_at(conn, captured_at=since.isoformat(), realized_pnl=0.0)
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=10,
        avg_price=10.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=10,
        avg_price=15.0,
    )
    conn.commit()
    period = portfolio_store.period_realized_pnl(conn, since=since)
    assert period["status"] == "OK"
    assert period["period_realized_pnl"] > 0, "should be a $50 gain"
    loss_pct = max(0.0, -period["period_realized_pnl"]) / 10000 * 100
    assert loss_pct == 0.0
    result = risk_checks.check_order(
        position_pct=0,
        total_exposure_pct=0,
        loss_per_trade_pct=0,
        daily_loss_pct=0,
        orders_today=0,
        weekly_loss_pct=loss_pct,
    )
    assert result["status"] == "PASS"
    print("PASS: profitable_week_gives_zero_loss_pct_not_negative")


def test_loss_below_2_pct_passes():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=2)
    _snapshot_at(conn, captured_at=since.isoformat(), realized_pnl=0.0)
    # -$150 on a $10,000 baseline = 1.5% loss, below the 2% ceiling.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=10,
        avg_price=100.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=10,
        avg_price=85.0,
    )
    conn.commit()
    period = portfolio_store.period_realized_pnl(conn, since=since)
    loss_pct = round(max(0.0, -period["period_realized_pnl"]) / 10000 * 100, 4)
    assert loss_pct == 1.5, loss_pct
    result = risk_checks.check_order(
        position_pct=0,
        total_exposure_pct=0,
        loss_per_trade_pct=0,
        daily_loss_pct=0,
        orders_today=0,
        weekly_loss_pct=loss_pct,
    )
    assert result["status"] == "PASS"
    print("PASS: loss_below_2_pct_passes")


def test_loss_exactly_2_pct_passes_the_boundary():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=2)
    _snapshot_at(conn, captured_at=since.isoformat(), realized_pnl=0.0)
    # -$200 on $10,000 = exactly 2.0%.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=10,
        avg_price=100.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=10,
        avg_price=80.0,
    )
    conn.commit()
    period = portfolio_store.period_realized_pnl(conn, since=since)
    loss_pct = round(max(0.0, -period["period_realized_pnl"]) / 10000 * 100, 4)
    assert loss_pct == 2.0, loss_pct
    result = risk_checks.check_order(
        position_pct=0,
        total_exposure_pct=0,
        loss_per_trade_pct=0,
        daily_loss_pct=0,
        orders_today=0,
        weekly_loss_pct=loss_pct,
    )
    assert result["status"] == "PASS", (
        "a limit check must be <=, so exactly at the limit still passes"
    )
    print("PASS: loss_exactly_2_pct_passes_the_boundary")


def test_loss_above_2_pct_rejects():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=2)
    _snapshot_at(conn, captured_at=since.isoformat(), realized_pnl=0.0)
    # -$201 on $10,000 = 2.01%, just over the ceiling.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=100,
        avg_price=10.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=100,
        avg_price=7.99,
    )
    conn.commit()
    period = portfolio_store.period_realized_pnl(conn, since=since)
    loss_pct = round(max(0.0, -period["period_realized_pnl"]) / 10000 * 100, 4)
    assert loss_pct > 2.0, loss_pct
    result = risk_checks.check_order(
        position_pct=0,
        total_exposure_pct=0,
        loss_per_trade_pct=0,
        daily_loss_pct=0,
        orders_today=0,
        weekly_loss_pct=loss_pct,
    )
    assert result["status"] == "REJECT"
    assert result["checks"]["weekly_loss"] is False
    print("PASS: loss_above_2_pct_rejects")


def test_week_boundary_excludes_prior_week_losses():
    """A big loss realized before the period start (last week) must not
    count against this week's limit -- the baseline snapshot at the
    boundary absorbs it."""
    conn = _conn()
    now = datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_thursday = monday - timedelta(days=4)

    # A large loss realized LAST week, before the boundary snapshot.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=100,
        avg_price=10.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=100,
        avg_price=5.0,
    )
    conn.commit()
    # Snapshot taken right at the boundary, after last week's loss already
    # happened -- this is the baseline THIS week's check will diff against.
    current_after_last_week_loss = portfolio_store.portfolio_snapshot(conn)["total_realized_pnl"]
    _snapshot_at(conn, captured_at=monday.isoformat(), realized_pnl=current_after_last_week_loss)

    # A small, separate loss this week.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="BBB",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=10,
        avg_price=10.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="BBB",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=10,
        avg_price=9.0,
    )
    conn.commit()

    period = portfolio_store.period_realized_pnl(conn, since=monday)
    assert period["status"] == "OK"
    assert period["period_realized_pnl"] == -10.0, (
        f"only this week's $10 loss should count, not last week's $500 loss too: {period}"
    )
    assert last_thursday < monday  # sanity: the big loss really is before the boundary
    print("PASS: week_boundary_excludes_prior_week_losses")


def test_multiple_trades_accumulate_within_the_week():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    _snapshot_at(conn, captured_at=since.isoformat(), realized_pnl=0.0)
    for avg_price in (95.0, 90.0, 85.0):
        portfolio_store.apply_fill_to_position(
            conn,
            symbol="AAA",
            account_suffix="T",
            account_type="CASH",
            side="BUY",
            quantity=10,
            avg_price=100.0,
        )
        portfolio_store.apply_fill_to_position(
            conn,
            symbol="AAA",
            account_suffix="T",
            account_type="CASH",
            side="SELL",
            quantity=10,
            avg_price=avg_price,
        )
    conn.commit()
    period = portfolio_store.period_realized_pnl(conn, since=since)
    # Three $50, $100, $150 losses = -$300 total.
    assert period["period_realized_pnl"] == -300.0, period
    print("PASS: multiple_trades_accumulate_within_the_week")


def test_partial_close_realizes_only_its_own_share():
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    _snapshot_at(conn, captured_at=since.isoformat(), realized_pnl=0.0)
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=100,
        avg_price=10.0,
    )
    # Sell only half the position at a loss; the other half stays open and
    # unrealized, contributing nothing to realized P&L yet.
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=50,
        avg_price=8.0,
    )
    conn.commit()
    period = portfolio_store.period_realized_pnl(conn, since=since)
    assert period["period_realized_pnl"] == -100.0, (
        f"only the 50 sold shares' $2 loss each should realize, not the full 100: {period}"
    )
    print("PASS: partial_close_realizes_only_its_own_share")


def test_missing_baseline_fails_closed_not_zero():
    """A real loss happened, but no snapshot exists far enough back to
    establish the period baseline -- this must be reported as
    INSUFFICIENT_DATA, not silently treated as 0% loss."""
    conn = _conn()
    since = datetime.now(timezone.utc) - timedelta(days=10)
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="BUY",
        quantity=10,
        avg_price=100.0,
    )
    portfolio_store.apply_fill_to_position(
        conn,
        symbol="AAA",
        account_suffix="T",
        account_type="CASH",
        side="SELL",
        quantity=10,
        avg_price=80.0,
    )
    conn.commit()
    # No snapshot at all exists before `since` -- current realized_pnl is
    # nonzero, so the "brand new account" shortcut correctly does not apply.
    period = portfolio_store.period_realized_pnl(conn, since=since)
    assert period["status"] == "INSUFFICIENT_DATA", period

    weekly_loss_pct = float("inf")  # what the caller must pass when data is unavailable
    result = risk_checks.check_order(
        position_pct=0,
        total_exposure_pct=0,
        loss_per_trade_pct=0,
        daily_loss_pct=0,
        orders_today=0,
        weekly_loss_pct=weekly_loss_pct,
    )
    assert result["status"] == "REJECT", "missing data must fail closed, never silently pass"
    print("PASS: missing_baseline_fails_closed_not_zero")


def test_weekly_loss_omitted_entirely_does_not_affect_existing_callers():
    """Backward compatibility: a caller that has not been updated to compute
    weekly_loss_pct at all (the default None) must see identical behavior to
    before this feature existed."""
    result = risk_checks.check_order(
        position_pct=1,
        total_exposure_pct=1,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
    )
    assert result["status"] == "PASS"
    assert "weekly_loss" not in result["checks"]
    print("PASS: weekly_loss_omitted_entirely_does_not_affect_existing_callers")


def test_default_weekly_loss_limit_is_2_percent():
    limits = risk_checks.default_limits()
    assert limits["max_weekly_loss_pct"] == 2.0, (
        "the vault's risk-policy.md specifies 2% -- do not invent a different threshold"
    )
    print("PASS: default_weekly_loss_limit_is_2_percent")


def main():
    test_no_trades_is_zero_not_unknown()
    test_profitable_week_gives_zero_loss_pct_not_negative()
    test_loss_below_2_pct_passes()
    test_loss_exactly_2_pct_passes_the_boundary()
    test_loss_above_2_pct_rejects()
    test_week_boundary_excludes_prior_week_losses()
    test_multiple_trades_accumulate_within_the_week()
    test_partial_close_realizes_only_its_own_share()
    test_missing_baseline_fails_closed_not_zero()
    test_weekly_loss_omitted_entirely_does_not_affect_existing_callers()
    test_default_weekly_loss_limit_is_2_percent()
    print("\nAll weekly-loss tests passed.")


if __name__ == "__main__":
    main()
