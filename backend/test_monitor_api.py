"""HTTP tests for the monitor router, against a throwaway SQLite file.

The screen is swapped at agents.shariah_agent.evaluate_shariah, which is what
run_monitor calls, so nothing reaches SEC.
"""

import sqlite3

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import monitor_api
from agents import shariah_agent


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / "monitor.db"

    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    def owner():
        return "owner"

    def verdict(symbol, **kwargs):
        status = "REJECT" if symbol == "BAD" else "PASS"
        details = {
            "report_date": "2025-12-31",
            "ratios": {
                "debt_ratio_pct": 10.0,
                "cash_ratio_pct": 5.0,
                "limit_pct": 33.0,
                "debt_concepts_used": ["LongTermDebt"],
            },
        }
        return {"market": "US", "status": status, "symbol": symbol, "details": details}

    monkeypatch.setattr(shariah_agent, "evaluate_shariah", verdict)
    app = FastAPI()
    app.include_router(monitor_api.build_router(connect, owner, lambda symbol: 12.0))
    return TestClient(app)


def test_add_import_run_and_sell(client):
    assert (
        client.post(
            "/monitor/holdings", json={"symbol": "GOOD", "quantity": 5, "average_cost": 10}
        ).status_code
        == 200
    )
    imported = client.post(
        "/monitor/holdings/import",
        json={"broker": "moomoo", "csv": "Code,Qty,Avg Price\nUS.BAD,10,10\n"},
    )
    assert imported.status_code == 200 and imported.json()["imported"] == 1

    run = client.get("/monitor").json()
    lights = {h["symbol"]: h["traffic_light"]["light"] for h in run["holdings"]}
    assert lights == {"GOOD": "GREEN", "BAD": "RED"}
    assert run["purification"]["total_purification_due"] == 20.0  # 10 * (12 - 10)
    assert [t["symbol"] for t in run["transitions"]] == ["BAD"]
    assert client.get("/monitor/alerts").json()["alerts"][0]["to_light"] == "RED"

    sale = client.post(
        "/monitor/sales", json={"symbol": "BAD", "quantity": 10, "price": 13, "broker": "moomoo"}
    ).json()
    assert sale["purification_due"] == 30.0


def test_bad_input_is_a_400_not_a_500(client):
    response = client.post(
        "/monitor/holdings", json={"symbol": "HK.00700", "quantity": 1, "average_cost": 1}
    )
    assert response.status_code == 400
    assert "not supported" in response.json()["detail"]
    bad_csv = client.post("/monitor/holdings/import", json={"broker": "x", "csv": "a,b\n1,2\n"})
    assert bad_csv.status_code == 400


def test_escalate_exists_and_no_endpoint_marks_green(client):
    client.post("/monitor/holdings", json={"symbol": "GOOD", "quantity": 1, "average_cost": 1})
    client.post(
        "/monitor/escalate",
        json={"symbol": "GOOD", "detail": "segment note", "report_date": "2025-12-31"},
    )
    run = client.get("/monitor").json()
    assert run["holdings"][0]["traffic_light"]["light"] == "RED"
    paths = set(client.app.openapi()["paths"])
    assert "/monitor/escalate" in paths
    assert not any("green" in path or "clear" in path for path in paths)


def test_settings_round_trip(client):
    assert client.get("/monitor/settings").json() == {"methodology": "SC_MY_SAC"}
    assert client.post("/monitor/settings", json={"methodology": "AAOIFI"}).status_code == 200
    assert client.get("/monitor/settings").json() == {"methodology": "AAOIFI"}
    assert client.post("/monitor/settings", json={"methodology": "x"}).status_code == 400


def test_owner_dependency_guards_every_route(tmp_path):
    def deny():
        raise HTTPException(status_code=403, detail="Owner access required")

    app = FastAPI()
    app.include_router(
        monitor_api.build_router(lambda: sqlite3.connect(tmp_path / "x.db"), deny, lambda s: None)
    )
    denied = TestClient(app)
    assert denied.get("/monitor").status_code == 403
    assert denied.post("/monitor/holdings", json={}).status_code == 403
