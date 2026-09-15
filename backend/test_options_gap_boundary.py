"""Regression test: authoritative_risk_verdict() must not size options
against the equity overlay, and this exemption must not leak into equity
evaluation or Shariah screening.

Phase 2A objective 7. Why options skip the equity overlay: contract
quantity and premium are not comparable to equity share-count and
share-price (CLAUDE.md known limitation #4) -- portfolio_risk_overlay()'s
position/exposure/sector math assumes quantity * price is a share notional,
which is meaningless for an options contract. No options-specific overlay
exists at preview time either (pre-existing gap, not introduced by this
phase); an option's collateral sufficiency is independently verified by
option_structure_gate / account_shariah_gate, not by this function. This
test proves the boundary: an option order with parameters that would
clearly breach every equity limit if misread as equity exposure is not
rejected by the equity overlay, and evaluating one has no effect on a
separate equity candidate's own evaluation or on Shariah screening (a
completely separate function, taking no asset_class parameter at all).
"""

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

_ENV_ORIG = {k: os.environ.get(k) for k in ["PAPER_ACCOUNT_EQUITY", "MAX_POSITION_PCT", "MAX_TOTAL_EXPOSURE_PCT", "MAX_LOSS_PER_TRADE_PCT", "MAX_DAILY_LOSS_PCT", "MAX_WEEKLY_LOSS_PCT", "MAX_ORDERS_PER_DAY"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"
    os.environ["MAX_POSITION_PCT"] = "5.0"
    os.environ["MAX_TOTAL_EXPOSURE_PCT"] = "25.0"
    os.environ["MAX_LOSS_PER_TRADE_PCT"] = "0.5"
    os.environ["MAX_DAILY_LOSS_PCT"] = "1.0"
    os.environ["MAX_WEEKLY_LOSS_PCT"] = "2.0"
    os.environ["MAX_ORDERS_PER_DAY"] = "5"
    yield
    for _k in _ENV_ORIG:
        if _ENV_ORIG[_k] is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _ENV_ORIG[_k]

os.environ["PAPER_ACCOUNT_EQUITY"] = "10000"
os.environ["MAX_POSITION_PCT"] = "5.0"
os.environ["MAX_TOTAL_EXPOSURE_PCT"] = "25.0"
os.environ["MAX_LOSS_PER_TRADE_PCT"] = "0.5"
os.environ["MAX_DAILY_LOSS_PCT"] = "1.0"
os.environ["MAX_WEEKLY_LOSS_PCT"] = "2.0"
os.environ["MAX_ORDERS_PER_DAY"] = "5"

import local_api
import portfolio_store
import sc_malaysia_store
from approval_queue import ensure_approval_queue


def _conn(fixture_dir: str) -> sqlite3.Connection:
    db_path = Path(fixture_dir) / "paper_trading.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    portfolio_store.ensure_portfolio_tables(conn)
    ensure_approval_queue(conn)
    sc_malaysia_store.ensure_sc_tables(conn)
    return conn


def test_option_skips_equity_overlay_even_at_extreme_size():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = _conn(tmp)
        risk = local_api.authoritative_risk_verdict(
            conn,
            symbol="AAPL",
            side="BUY",
            quantity=100_000,
            price=100_000.0,  # notional far beyond every equity limit if misapplied
            asset_class="option",
        )
        conn.close()
    portfolio_detail = risk["details"]["portfolio"]
    assert portfolio_detail["status"] == "NOT_EVALUATED", portfolio_detail
    assert risk["details"]["loss_per_trade_pct"] == 0.0, risk
    # Not rejected by the equity overlay it never ran -- with a clean DB and
    # no other orders, nothing else fails it either.
    assert risk["status"] == "PASS", risk
    print("PASS: an option order at extreme notional is not evaluated against the equity overlay")


def test_option_exemption_does_not_leak_into_a_separate_equity_candidate():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        conn = _conn(tmp)
        # An option order first, at a size that would breach every equity
        # limit if it were (incorrectly) read as equity exposure.
        local_api.authoritative_risk_verdict(
            conn, symbol="AAPL", side="BUY", quantity=100_000, price=100_000.0, asset_class="option"
        )
        # A genuinely small, separate equity candidate must be evaluated
        # purely on its own merits -- evaluating the option candidate above
        # wrote no position row (nothing about risk evaluation persists
        # anything), so it cannot have changed what portfolio_risk_overlay
        # sees for MSFT.
        equity_risk = local_api.authoritative_risk_verdict(
            conn, symbol="MSFT", side="BUY", quantity=1, price=40.0, asset_class="equity"
        )
        conn.close()
    assert equity_risk["details"]["portfolio"]["status"] == "PASS", equity_risk
    assert equity_risk["status"] == "PASS", equity_risk
    print(
        "PASS: evaluating an option order does not leak into a separate equity candidate's own evaluation"
    )


def test_option_asset_class_does_not_affect_shariah_screening():
    """asset_class is a risk-side concept only -- authoritative_shariah_verdict
    takes no asset_class parameter at all, so a ticker absent from any
    approved publication is UNKNOWN regardless of what asset_class a risk
    candidate for the same ticker might carry. This proves the options risk
    exemption cannot be used to smuggle a ticker past the Shariah gate."""
    result = local_api.authoritative_shariah_verdict("9999")
    assert result["status"] == "UNKNOWN", result
    print(
        "PASS: the options risk exemption has no effect on Shariah screening -- it is a separate function entirely"
    )


def main():
    test_option_skips_equity_overlay_even_at_extreme_size()
    test_option_exemption_does_not_leak_into_a_separate_equity_candidate()
    test_option_asset_class_does_not_affect_shariah_screening()
    print("\nAll options-risk-boundary regression tests passed.")


if __name__ == "__main__":
    main()
