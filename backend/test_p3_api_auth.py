import pytest
from fastapi.testclient import TestClient
import json
import base64
from backend.local_api import app, BACKEND_DIR
from backend import auth

client = TestClient(app)

def setup_users():
    users_json = json.dumps([
        {"username": "admin_user", "password_hash": auth.hash_password("admin_pass", iterations=1000), "role": "admin"},
        {"username": "review_user", "password_hash": auth.hash_password("review_pass", iterations=1000), "role": "reviewer"}
    ])
    import os
    os.environ["SC_ADMIN_AUTH_USERS"] = users_json

def get_auth_headers(username, password):
    encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}

def test_p3_api_auth_requirements(monkeypatch):
    setup_users()
    
    # Mock portfolio creation natively since test doesn't test portfolio creation logic deeply
    from backend import p3_portfolio_engine, local_api
    
    # Setup test DB
    import sqlite3
    _conn = sqlite3.connect(":memory:", check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    class ConnWrapper:
        def __init__(self, c):
            self.c = c
        def __getattr__(self, name):
            return getattr(self.c, name)
        def close(self):
            pass
    conn = ConnWrapper(_conn)
    p3_portfolio_engine.create_portfolio(conn, "Test Port", 100000.0)
    monkeypatch.setattr(local_api, "db", lambda: conn)
    
    # Unauthenticated
    res = client.post("/api/p3/portfolios/1/proposals", json={"ticker": "AAPL", "side": "BUY"})
    assert res.status_code == 401
    assert "Not authenticated" in res.json().get("detail", "") or "Incorrect username or password" in res.json().get("detail", "")

    # Forged actor without auth
    res = client.post("/api/p3/portfolios/1/proposals", json={"ticker": "AAPL", "side": "BUY", "actor": "admin_user"})
    assert res.status_code == 401

    # Authenticated but bad password
    res = client.post("/api/p3/portfolios/1/proposals", json={"ticker": "AAPL", "side": "BUY"}, headers=get_auth_headers("admin_user", "wrong"))
    assert res.status_code == 401

    # Mock screen and risk so proposal can pass business logic
    import p3_decision_engine
    monkeypatch.setattr(p3_decision_engine, "screen_ticker", lambda t: {"shariah": {"status": "PASS", "sector": "Tech"}})
    monkeypatch.setattr(p3_decision_engine.yahoo_finance, "fetch_eod_prices", lambda t, start_date, end_date, allow_fallback, allow_stale_cache: ([{"close": 150.0}], "test"))
    
    # Authenticated reviewer -> propose
    res = client.post("/api/p3/portfolios/1/proposals", json={"ticker": "AAPL", "side": "BUY"}, headers=get_auth_headers("review_user", "review_pass"))
    assert res.status_code == 200, res.json()
    order_id = res.json()["id"]
    assert res.json()["actor"] == "review_user" # Verify actor provenance!

    # Authenticated reviewer -> approve
    res = client.post(f"/api/p3/portfolios/1/orders/{order_id}/approve", json={}, headers=get_auth_headers("review_user", "review_pass"))
    assert res.status_code == 200
    assert res.json()["actor"] == "review_user"

    # Authenticated reviewer -> execute (Should fail RBAC, role is reviewer which lacks execute)
    res = client.post(f"/api/p3/portfolios/1/orders/{order_id}/execute", json={}, headers=get_auth_headers("review_user", "review_pass"))
    assert res.status_code == 403

    # Authenticated admin -> execute
    res = client.post(f"/api/p3/portfolios/1/orders/{order_id}/execute", json={}, headers=get_auth_headers("admin_user", "admin_pass"))
    assert res.status_code == 200
    
    print("PASS: API Auth tests passed")
