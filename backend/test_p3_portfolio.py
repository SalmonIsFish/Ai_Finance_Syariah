import sqlite3
import pytest
from datetime import datetime
import json
import math

from backend import p3_portfolio_engine, p3_decision_engine, local_api

def setup_memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    p3_portfolio_engine.ensure_p3_tables(conn)
    return conn

def test_p3_schema():
    conn = setup_memory_db()
    # Create portfolio
    port = p3_portfolio_engine.create_portfolio(conn, "Test Portfolio", 10000.0)
    assert port["id"] == 1
    assert port["name"] == "Test Portfolio"
    assert port["status"] == "ACTIVE"
    assert port["current_cash"] == 10000.0

def test_state_machine_invalid_transitions():
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    # insert a mock proposed order
    conn.execute(
        "INSERT INTO p3_portfolio_orders (portfolio_id, ticker, side, quantity, requested_price, status, submitted_at, actor) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (port["id"], "AAPL", "BUY", 10, 150.0, "PROPOSED", "2026-09-08T00:00:00Z", "SYSTEM")
    )
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # invalid: PROPOSED -> EXECUTED
    with pytest.raises(ValueError, match="Invalid state transition"):
        p3_portfolio_engine.update_order_status(conn, order_id, "PROPOSED", "EXECUTED")

    # valid: PROPOSED -> APPROVED
    p3_portfolio_engine.update_order_status(conn, order_id, "PROPOSED", "APPROVED")

    # invalid: APPROVED -> APPROVED
    with pytest.raises(ValueError, match="Invalid state transition"):
        p3_portfolio_engine.update_order_status(conn, order_id, "APPROVED", "APPROVED")

    # valid: APPROVED -> EXECUTED
    p3_portfolio_engine.update_order_status(conn, order_id, "APPROVED", "EXECUTED")

    # invalid: EXECUTED -> REJECTED (Terminal)
    with pytest.raises(ValueError, match="Invalid state transition"):
        p3_portfolio_engine.update_order_status(conn, order_id, "EXECUTED", "REJECTED")

def test_concurrency_double_execute(monkeypatch):
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)
    conn.execute(
        "INSERT INTO p3_portfolio_orders (portfolio_id, ticker, side, quantity, requested_price, status, submitted_at, actor) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (port["id"], "AAPL", "BUY", 10, 5.0, "APPROVED", "2026-09-08T00:00:00Z", "SYSTEM")
    )
    order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Mock screen_ticker to always pass
    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}})

    # Mock yahoo_finance to return valid quote
    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", lambda t, start_date, end_date, allow_fallback, allow_stale_cache: ([{"close": 5.0}], "test"))
    monkeypatch.setattr(p3_decision_engine, "risk_limits", lambda: {"limits": {"max_loss_per_trade_pct": 0.0, "max_sector_exposure_pct": 100.0, "max_total_exposure_pct": 100.0, "max_position_pct": 100.0}})

    # Execution 1
    p3_decision_engine.execute_order(conn, order_id, "SYSTEM")

    # Execution 2 should fail since status is now EXECUTED not APPROVED
    with pytest.raises(ValueError, match="Only APPROVED orders can be executed"):
        p3_decision_engine.execute_order(conn, order_id, "SYSTEM")

def test_order_rejected_if_unknown(monkeypatch):
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "UNKNOWN"}})
    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", lambda t, start_date, end_date, allow_fallback, allow_stale_cache: ([{"close": 150.0}], "test"))

    with pytest.raises(ValueError, match="Proposal blocked: shariah_gate_failed: UNKNOWN"):
        p3_decision_engine.propose_order(conn, port["id"], "MSFT", "BUY")

def test_ai_isolation():
    # Verify that the copilot API does not import or call p3_decision_engine or p3_portfolio_engine
    import sys
    # reload copilot API just in case
    import backend.copilot_api as copilot_api
    assert not hasattr(copilot_api, "propose_order")
    assert not hasattr(copilot_api, "execute_order")

def test_missing_price_aborts_execution(monkeypatch):
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS"}})
    # Simulate missing price (empty list returned)
    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", lambda t, start_date, end_date, allow_fallback, allow_stale_cache: ([], None))

    with pytest.raises(ValueError, match="Proposal blocked: market_data_unavailable"):
        p3_decision_engine.propose_order(conn, port["id"], "AAPL", "BUY")


def test_order_rejected_due_to_missing_loss_input(monkeypatch):
    import p3_decision_engine
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, 'Test', 10000.0)
    monkeypatch.setattr(p3_decision_engine, 'screen_ticker', lambda t: {'shariah': {'status': 'PASS', 'sector': 'Tech'}})
    monkeypatch.setattr(p3_decision_engine.yahoo_finance, 'fetch_eod_prices', lambda t, start_date, end_date, allow_fallback, allow_stale_cache: ([{'close': 150.0}], 'test'))
    monkeypatch.setattr(p3_decision_engine, 'risk_limits', lambda: {'limits': {'max_loss_per_trade_pct': 0.5}})
    import pytest
    with pytest.raises(ValueError, match='loss_per_unit_required'):
        p3_decision_engine.propose_order(conn, port['id'], 'AAPL', 'BUY')

def test_order_rejected_or_sized_using_contemporaneous_valuation(monkeypatch):
    import p3_decision_engine
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    # Give portfolio a holding of MSFT: 100 shares at old stored price of $100 -> $10,000 market value.
    # Total equity would be $10,000 + $10,000 (cash) = $20,000.
    conn.execute(
        """
        INSERT INTO p3_portfolio_positions (portfolio_id, ticker, quantity, average_cost, market_value, unrealized_pnl, realized_pnl, sector, updated_at)
        VALUES (?, 'MSFT', 100, 100.0, 10000.0, 0.0, 0.0, 'Tech', '2024-01-01T00:00:00Z')
        """,
        (port['id'],)
    )

    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}})

    # Mock prices: Candidate AAPL = $150. Existing MSFT = $200!
    # So actual MSFT value is $20,000. Total equity is $30,000.
    def mock_fetch(ticker, start_date, end_date, allow_fallback, allow_stale_cache):
        if ticker == "AAPL":
            return [{"close": 150.0}], "test"
        elif ticker == "MSFT":
            return [{"close": 200.0}], "test"
        return [], "test"

    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", mock_fetch)
    monkeypatch.setattr(p3_decision_engine, "risk_limits", lambda: {"limits": {"max_loss_per_trade_pct": 0.0, "max_total_exposure_pct": 100.0, "max_position_pct": 50.0, "max_sector_exposure_pct": 100.0}})

    # With total equity $30,000, 50% max position = $15,000.
    # We can buy up to $15,000 of AAPL -> 100 shares.
    # If it wrongly used the stale $20,000 equity, max position = $10,000 -> 66 shares.
    order = p3_decision_engine.propose_order(conn, port['id'], 'AAPL', 'BUY')
    assert abs(order["quantity"] - 166.66666666666666) < 1e-5

    # Now simulate a failure to fetch MSFT
    def mock_fetch_fail_msft(ticker, start_date, end_date, allow_fallback, allow_stale_cache):
        if ticker == "AAPL":
            return [{"close": 150.0}], "test"
        return [], "test"

    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", mock_fetch_fail_msft)
    import pytest
    with pytest.raises(ValueError, match="portfolio_valuation_unavailable"):
        p3_decision_engine.propose_order(conn, port['id'], 'AAPL', 'BUY')

def test_order_rejected_if_valuation_raises_exception(monkeypatch):
    import p3_decision_engine
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    conn.execute(
        """
        INSERT INTO p3_portfolio_positions (portfolio_id, ticker, quantity, average_cost, market_value, unrealized_pnl, realized_pnl, sector, updated_at)
        VALUES (?, 'MSFT', 100, 100.0, 10000.0, 0.0, 0.0, 'Tech', '2024-01-01T00:00:00Z')
        """,
        (port['id'],)
    )

    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}})

    def mock_fetch_raises(ticker, start_date, end_date, allow_fallback, allow_stale_cache):
        if ticker == "AAPL":
            return [{"close": 150.0}], "test"
        raise RuntimeError("Network error")

    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", mock_fetch_raises)

    import pytest
    with pytest.raises(ValueError, match="portfolio_valuation_unavailable"):
        p3_decision_engine.propose_order(conn, port['id'], 'AAPL', 'BUY')


def test_allow_stale_cache_is_false_for_all_valuations(monkeypatch):
    import p3_decision_engine
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    conn.execute(
        """
        INSERT INTO p3_portfolio_positions (portfolio_id, ticker, quantity, average_cost, market_value, unrealized_pnl, realized_pnl, sector, updated_at)
        VALUES (?, 'MSFT', 100, 100.0, 10000.0, 0.0, 0.0, 'Tech', '2024-01-01T00:00:00Z')
        """,
        (port['id'],)
    )

    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}})

    calls = []
    def mock_fetch(ticker, start_date, end_date, allow_fallback, allow_stale_cache):
        calls.append({"ticker": ticker, "allow_stale_cache": allow_stale_cache})
        return [{"close": 150.0}], "test"

    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", mock_fetch)
    monkeypatch.setattr(p3_decision_engine, "risk_limits", lambda: {"limits": {"max_loss_per_trade_pct": 0.0, "max_sector_exposure_pct": 100.0, "max_total_exposure_pct": 100.0, "max_position_pct": 100.0}})

    p3_decision_engine.propose_order(conn, port['id'], 'AAPL', 'BUY')

    assert len(calls) == 2
    for call in calls:
        assert call["allow_stale_cache"] is False, f"Expected False for {call['ticker']}"

def test_order_rejected_if_candidate_valuation_raises_exception(monkeypatch):
    import p3_decision_engine
    conn = setup_memory_db()
    port = p3_portfolio_engine.create_portfolio(conn, "Test", 30000.0)

    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}})

    def mock_fetch_raises(ticker, start_date, end_date, allow_fallback, allow_stale_cache):
        raise RuntimeError("Provider outage")

    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", mock_fetch_raises)

    import pytest
    with pytest.raises(ValueError, match="market_data_unavailable"):
        p3_decision_engine.propose_order(conn, port['id'], 'AAPL', 'BUY')
