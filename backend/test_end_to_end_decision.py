"""End-to-end decision pipeline test.

Four scenarios with zero LLM, zero network, zero broker dependency:
  1. SC-compliant ticker + BUY signal + within risk -> APPROVED
  2. Ticker not in SC universe -> UNKNOWN -> BLOCKED
  3. Ticker compliant + NO_SIGNAL -> BLOCKED (quant)
  4. Ticker compliant + BUY + over risk -> BLOCKED (risk)

Each scenario verifies the full evidence record is produced with correct provenance.
"""

import json
import sqlite3
import tempfile
from datetime import date, timedelta
from pathlib import Path

import sc_malaysia_store
import sc_malaysia_import
import confidence
import evidence
import risk_checks
from agents.quant_agent import evaluate_strategies


def _setup_sc_store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    sc_malaysia_store.ensure_sc_tables(conn)

    with tempfile.TemporaryDirectory() as tmp:
        data = {
            "dataset_id": "sc-sac-my-2026-05-29",
            "publication_date": "2026-05-29",
            "source": {
                "authority": "Securities Commission Malaysia Shariah Advisory Council",
                "url": "https://www.sc.com.my/test",
                "local_evidence_path": "test",
            },
            "expected_record_count": 3,
            "records": [
                {
                    "ticker": "1155",
                    "issuer_name": "Malayan Banking Bhd",
                    "shariah_status": "COMPLIANT",
                },
                {"ticker": "1295", "issuer_name": "Public Bank Bhd", "shariah_status": "COMPLIANT"},
                {
                    "ticker": "5225",
                    "issuer_name": "IHH Healthcare Bhd",
                    "shariah_status": "COMPLIANT",
                },
            ],
            "validation": {"status": "staging", "actual_record_count": 3, "validated_at": None},
        }
        path = Path(tmp) / "test.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        sc_malaysia_import.ingest_universe_json(
            conn, path, official_record_count=3, extractable_record_count=3
        )

    sc_malaysia_store.approve_publication(conn, "sc-sac-my-2026-05-29")
    sc_malaysia_store.activate_publication(conn, "sc-sac-my-2026-05-29")
    return conn


def _make_bars(n: int = 250, trend_up: bool = True, breakout: bool = True) -> list[dict]:
    bars = []
    base = 10.0
    today = date.today()
    for i in range(n):
        d = today - timedelta(days=n - i)
        if trend_up:
            close = base + (i * 0.02)
        else:
            close = base - (i * 0.005)

        if breakout and i == n - 1:
            close = max(base + (i * 0.02), close + 0.5)

        bars.append(
            {
                "symbol": "1155",
                "date": d.isoformat(),
                "open": round(close - 0.05, 4),
                "high": round(close + 0.10, 4),
                "low": round(close - 0.10, 4),
                "close": round(close, 4),
                "volume": 5000000 + (i * 1000),
            }
        )
    return bars


def run_pipeline(
    conn: sqlite3.Connection,
    ticker: str,
    bars: list[dict],
    risk_overrides: dict | None = None,
) -> dict:
    shariah = sc_malaysia_store.check_eligibility(conn, ticker)

    if shariah["status"] != "PASS":
        record = evidence.build_decision_record(
            ticker=ticker,
            shariah_result=shariah,
            final_decision="BLOCKED",
            decision_reason=f"shariah_{shariah['status'].lower()}",
        )
        return record

    results = evaluate_strategies(bars, ["S001", "S002"])
    from agents.quant_agent import select_signal

    strategy = select_signal(results)

    if strategy.get("signal") != "BUY":
        record = evidence.build_decision_record(
            ticker=ticker,
            shariah_result=shariah,
            quant_result={
                "signal": strategy.get("signal"),
                "reason": strategy.get("reason"),
                "signal_source": strategy.get("strategy_id"),
            },
            final_decision="BLOCKED",
            decision_reason="quant_no_buy_signal",
        )
        return record

    risk_input = {
        "position_pct": 3.0,
        "total_exposure_pct": 15.0,
        "loss_per_trade_pct": 0.3,
        "daily_loss_pct": 0.5,
        "orders_today": 2,
    }
    if risk_overrides:
        risk_input.update(risk_overrides)

    risk = risk_checks.check_order(**risk_input)

    attract = confidence.score_attractiveness(
        strategy_result=strategy,
        bars=bars,
        risk_headroom=0.7,
    )

    if risk["status"] != "PASS":
        record = evidence.build_decision_record(
            ticker=ticker,
            shariah_result=shariah,
            quant_result={
                "signal": "BUY",
                "reason": strategy.get("reason"),
                "signal_source": strategy.get("strategy_id"),
            },
            risk_result=risk,
            attractiveness=attract,
            final_decision="BLOCKED",
            decision_reason="risk_limit_breached",
        )
        return record

    record = evidence.build_decision_record(
        ticker=ticker,
        shariah_result=shariah,
        quant_result={
            "signal": "BUY",
            "reason": strategy.get("reason"),
            "signal_source": strategy.get("strategy_id"),
            "price": bars[-1]["close"],
            "bars": len(bars),
            "price_source": "test",
        },
        risk_result=risk,
        attractiveness=attract,
        final_decision="APPROVED",
        decision_reason="all_gates_passed",
    )
    return record


def test_scenario_1_all_pass():
    conn = _setup_sc_store()
    bars = _make_bars(250, trend_up=True, breakout=True)
    record = run_pipeline(conn, "1155", bars)

    assert record["decision"] == "APPROVED", f"Expected APPROVED: {record}"
    assert record["decision_reason"] == "all_gates_passed"
    assert record["shariah"]["status"] == "PASS"
    assert record["shariah"]["publication_id"] == "sc-sac-my-2026-05-29"
    assert record["quant"]["signal"] == "BUY"
    assert record["risk"]["status"] == "PASS"
    assert record["attractiveness"]["attractiveness"] > 0
    assert "decision_id" in record
    assert "timestamp" in record

    # Evidence completeness: minimum fields required for the record to be
    # independently reproducible/auditable later (Phase 0 audit, Part 10).
    assert record["shariah"]["publication_date"], "missing publication_date"
    assert record["shariah"]["source_document_hash"], "missing source_document_hash"
    assert record["shariah"]["reason"], "missing shariah reason code"
    assert record["quant"]["strategy_id"], "missing quant strategy id"
    assert record["quant"]["price_source"], "missing market-data source"
    print("PASS: scenario_1 — compliant + BUY + within risk -> APPROVED with full evidence")


def test_scenario_2_unknown_ticker():
    conn = _setup_sc_store()
    bars = _make_bars(250)
    record = run_pipeline(conn, "9999", bars)

    assert record["decision"] == "BLOCKED"
    assert record["decision_reason"] == "shariah_unknown"
    assert record["shariah"]["status"] == "UNKNOWN"
    assert "quant" not in record
    assert "risk" not in record
    print("PASS: scenario_2 — absent ticker -> UNKNOWN -> BLOCKED, pipeline short-circuits")


def test_scenario_3_no_signal():
    conn = _setup_sc_store()
    bars = _make_bars(250, trend_up=False, breakout=False)
    record = run_pipeline(conn, "1155", bars)

    assert record["decision"] == "BLOCKED"
    assert record["decision_reason"] == "quant_no_buy_signal"
    assert record["shariah"]["status"] == "PASS"
    assert record["quant"]["signal"] != "BUY"
    print("PASS: scenario_3 — compliant but no BUY signal -> BLOCKED")


def test_scenario_4_risk_breach():
    conn = _setup_sc_store()
    bars = _make_bars(250, trend_up=True, breakout=True)
    record = run_pipeline(conn, "1155", bars, risk_overrides={"position_pct": 20.0})

    assert record["decision"] == "BLOCKED"
    assert record["decision_reason"] == "risk_limit_breached"
    assert record["shariah"]["status"] == "PASS"
    assert record["quant"]["signal"] == "BUY"
    assert record["risk"]["status"] == "REJECT"
    assert record["risk"]["checks"]["position_ceiling"] is False
    print("PASS: scenario_4 — compliant + BUY but over risk -> BLOCKED")


def test_evidence_append():
    original_file = evidence.DECISIONS_FILE
    with tempfile.TemporaryDirectory() as tmp:
        evidence.EVIDENCE_DIR = Path(tmp)
        evidence.DECISIONS_FILE = Path(tmp) / "decisions.jsonl"
        try:
            record = evidence.build_decision_record(
                ticker="1155",
                shariah_result={
                    "status": "PASS",
                    "reason": "authoritative_compliant",
                    "publication_id": "test",
                },
                final_decision="APPROVED",
                decision_reason="test",
            )
            evidence.append_decision(record)
            evidence.append_decision(record)

            decisions = evidence.read_decisions()
            assert len(decisions) == 2
            assert all(d["ticker"] == "1155" for d in decisions)

            filtered = evidence.read_decisions(ticker="9999")
            assert len(filtered) == 0
        finally:
            evidence.EVIDENCE_DIR = original_file.parent
            evidence.DECISIONS_FILE = original_file
    print("PASS: evidence_append — JSONL append and read works correctly")


def test_attractiveness_separate_from_compliance():
    """Attractiveness score must not include compliance as a component."""
    strategy = {"signal": "BUY", "trend_gap_pct": 5.0, "breakout_gap_pct": 2.0}
    bars = _make_bars(30)
    result = confidence.score_attractiveness(
        strategy_result=strategy,
        bars=bars,
        risk_headroom=0.8,
    )

    components = result["components"]
    assert "compliance" not in components, "Compliance must NOT be a component of attractiveness"
    assert "shariah" not in components, "Shariah must NOT be a component of attractiveness"
    assert set(components.keys()) == {"technical", "volume", "risk_headroom"}
    print("PASS: attractiveness_separate — no compliance component in attractiveness score")


def main():
    test_scenario_1_all_pass()
    test_scenario_2_unknown_ticker()
    test_scenario_3_no_signal()
    test_scenario_4_risk_breach()
    test_evidence_append()
    test_attractiveness_separate_from_compliance()
    print("\nAll end-to-end decision pipeline tests passed.")


if __name__ == "__main__":
    main()
