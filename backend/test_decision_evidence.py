"""Every gate decision must actually reach the append-only evidence trail.

The project's claim is that the gate chain enforces *and proves*: an order that
fails any gate cannot be submitted, and every decision is recorded with its
evidence. The proving half was not true until 2026-09-22.

`evidence.py` implemented `build_decision_record()` and `append_decision()` in
full, and **nothing ever called `append_decision`**. Only the read path was
wired, through `screening_api.evidence_for_ticker`. `data/evidence/` did not
exist locally or on the droplet, and `GET /api/evidence/{ticker}` returned
`{"count": 0, "decisions": []}` for every symbol. The trail was a read endpoint
over a file nobody wrote, which is a worse failure than having no trail at all:
it looks like evidence.

These tests exist so it cannot silently come loose again.
"""

import json
import tempfile
from pathlib import Path

import agent_coordinator
import evidence

SHARIAH_PASS = {
    "agent": "shariah",
    "status": "PASS",
    "reason": "authoritative_compliant",
    "provider": "SC_MY_APPROVED_PUBLICATION",
    "publication_id": "sc-sac-my-2026-05-29",
}
SHARIAH_REJECT = {
    "agent": "shariah",
    "status": "REJECT",
    "reason": "authoritative_non_compliant",
    "provider": "SC_MY_APPROVED_PUBLICATION",
}
QUANT_LIVE_BUY = {
    "agent": "quant",
    "signal": "BUY",
    "price": 100.0,
    "data_freshness": "live",
    "price_source": "alpaca_iex",
    "as_of_date": "2026-09-22",
}


def _isolated_trail():
    """Point the trail at a throwaway dir. Never write to the real one in a test."""
    tmp = Path(tempfile.mkdtemp(prefix="amanah-evidence-test-"))
    evidence.EVIDENCE_DIR = tmp
    evidence.DECISIONS_FILE = tmp / "decisions.jsonl"
    return evidence.DECISIONS_FILE


def _evaluate(symbol="AAPL", shariah=None, quant=None):
    return agent_coordinator.evaluate_candidate(
        symbol=symbol,
        side="BUY",
        quantity=1,
        price=100.0,
        position_pct=1.0,
        total_exposure_pct=1.0,
        loss_per_trade_pct=0.1,
        daily_loss_pct=0.1,
        orders_today=0,
        shariah_override=shariah or SHARIAH_PASS,
        quant_override=quant or QUANT_LIVE_BUY,
    )


def test_an_approved_decision_is_recorded_with_provenance():
    trail = _isolated_trail()
    result = _evaluate()
    assert result["decision"] == "READY_FOR_APPROVAL", result

    assert trail.exists(), "no evidence was written for a decision that was made"
    record = json.loads(trail.read_text(encoding="utf-8").strip())

    assert record["ticker"] == "AAPL"
    assert record["decision"] == "READY_FOR_APPROVAL"
    assert record["decision_reason"] == "no_blockers"
    # Provenance is the point: which authority said what, and on what data.
    assert record["shariah"]["status"] == "PASS"
    assert record["shariah"]["provider"] == "SC_MY_APPROVED_PUBLICATION"
    assert record["shariah"]["publication_id"] == "sc-sac-my-2026-05-29"
    assert record["market_data"]["data_freshness"] == "live"
    assert record["market_data"]["price_source"] == "alpaca_iex"
    assert record["decision_id"] and record["timestamp"]
    print("PASS: an approved decision is recorded with provenance")


def test_a_refusal_is_recorded_too():
    """The refusals are the records most worth having.

    "your system let this through" and "your system stopped this" are both
    claims that need evidence, so BLOCKED is recorded exactly like READY.
    """
    trail = _isolated_trail()
    result = _evaluate(shariah=SHARIAH_REJECT)
    assert result["decision"] == "BLOCKED", result

    record = json.loads(trail.read_text(encoding="utf-8").strip())
    assert record["decision"] == "BLOCKED"
    assert "shariah_rejected" in record["decision_reason"]
    assert record["shariah"]["status"] == "REJECT"
    print("PASS: a refusal is recorded too, with the blocker as its reason")


def test_the_trail_is_append_only():
    trail = _isolated_trail()
    _evaluate(symbol="AAPL")
    _evaluate(symbol="MSFT", shariah=SHARIAH_REJECT)
    _evaluate(symbol="AAPL")

    lines = [x for x in trail.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 3, f"expected 3 appended records, got {len(lines)}"

    ids = {json.loads(x)["decision_id"] for x in lines}
    assert len(ids) == 3, "decision_id must be unique per record"

    aapl = evidence.read_decisions(ticker="AAPL")
    assert len(aapl) == 2, f"read_decisions should filter by ticker, got {len(aapl)}"
    print("PASS: the trail appends and filters by ticker")


def test_a_failed_write_never_changes_the_decision():
    """Bookkeeping must not be able to overturn a correct verdict.

    Same principle as sec_edgar_screen._record_screen swallowing its own audit
    write: a full disk or a locked file must never turn a working REJECT into an
    error, or worse, into an approval.
    """
    _isolated_trail()
    original = agent_coordinator.record_decision

    def _explode(_result):
        raise OSError("No space left on device")

    agent_coordinator.record_decision = _explode
    try:
        result = _evaluate(shariah=SHARIAH_REJECT)
    finally:
        agent_coordinator.record_decision = original

    assert result["decision"] == "BLOCKED", result
    assert "shariah_rejected" in result["blockers"], result
    print("PASS: a failed evidence write leaves the decision untouched")


def test_the_trail_records_the_decision_that_was_actually_reached():
    """A sell approved after the portfolio overlay must not be filed as BLOCKED.

    The bug, live until 2026-09-22: `record_decision` fired inside
    `evaluate_candidate`, but `local_api.apply_portfolio_risk_overlay` runs
    *afterwards* and strips `only_buy_side_supported` / `quant_no_buy_signal`,
    turning a BLOCKED sell into READY_FOR_APPROVAL. So an approved -- and
    executable -- SELL was preserved in the evidence trail as
    `BLOCKED / only_buy_side_supported`.

    An audit trail that contradicts the order it describes is worse than no
    trail, because it carries the authority of a record. This drives the real
    preview path rather than the coordinator directly, since the bug lives in
    the seam between the two and a coordinator-only test cannot see it.
    """
    trail = _isolated_trail()

    import local_api
    from portfolio_store import apply_fill_to_position, ensure_portfolio_tables

    # A held position is what lets the overlay clear the sell. Without one the
    # order stays BLOCKED on `portfolio_sell_without_position` and the flip this
    # test exists to catch never happens -- the test would still pass, for the
    # wrong reason, which is the failure mode CLAUDE.md warns about after a live
    # option order passed a check only because incidental account state satisfied
    # it.
    original_db_path = local_api.DB_PATH
    original_price_lookup = local_api.portfolio_price_lookup
    db_dir = Path(tempfile.mkdtemp(prefix="amanah-evidence-db-"))
    local_api.DB_PATH = db_dir / "paper_trading.db"
    # Positions are marked to market, so without pinning this the overlay would
    # fetch a live AAPL quote -- a network call, and a test whose verdict moves
    # with the market. 10.0 keeps the position far inside the 5% position limit.
    local_api.portfolio_price_lookup = lambda symbol: {"latest_close": 10.0, "source": "test"}
    connection = local_api.db()
    try:
        ensure_portfolio_tables(connection)
        apply_fill_to_position(
            connection,
            symbol="AAPL",
            account_suffix="TEST",
            account_type="CASH",
            side="BUY",
            quantity=5,
            avg_price=10.0,
        )
        connection.commit()
    finally:
        connection.close()

    recorded = []
    original = agent_coordinator.record_decision

    def _capture(result, *, source="scan"):
        recorded.append((dict(result), source))
        return original(result, source=source)

    agent_coordinator.record_decision = _capture
    try:
        evaluation = local_api.evaluate_preview_request(
            local_api.PaperPreviewRequest(
                symbol="AAPL",
                side="SELL",
                quantity=1,
                price=10.0,
                position_pct=1.0,
                total_exposure_pct=1.0,
                loss_per_trade_pct=0.1,
                daily_loss_pct=0.1,
                orders_today=0,
                test_fixture=True,
                shariah_status="PASS",
            )
        )
    finally:
        agent_coordinator.record_decision = original
        local_api.DB_PATH = original_db_path
        local_api.portfolio_price_lookup = original_price_lookup

    # The scenario must actually be the one described, or the assertions below
    # prove nothing: this sell has to have been approved.
    assert evaluation["decision"] == "READY_FOR_APPROVAL", (
        f"the fixture no longer produces an approved sell: {evaluation['decision']} "
        f"{evaluation['blockers']}"
    )

    assert len(recorded) == 1, f"a preview must be recorded exactly once, got {len(recorded)}"
    written, source = recorded[0]
    assert source == "preview", f"a real order must not be filed as scan noise: {source}"

    # The heart of it: what was written must be what the API returned.
    assert written["decision"] == evaluation["decision"], (
        f"trail says {written['decision']}, the system answered {evaluation['decision']}"
    )
    assert written["blockers"] == evaluation["blockers"], (
        f"trail blockers {written['blockers']} vs actual {evaluation['blockers']}"
    )

    # And it reached disk in that state, not just the seam.
    lines = [json.loads(line) for line in trail.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1, lines
    assert lines[0]["decision"] == evaluation["decision"], lines[0]
    assert lines[0]["source"] == "preview", lines[0]
    print("PASS: the trail records the decision the system actually reached")


def test_a_scan_is_distinguishable_from_an_order():
    """Background scans run evaluate_candidate for up to 30 symbols at a time.

    Without a source field the trail is mostly scan noise and there is no way to
    ask what happened to a particular order.
    """
    trail = _isolated_trail()
    _evaluate(symbol="MSFT")
    record = json.loads(trail.read_text(encoding="utf-8").splitlines()[0])
    assert record["source"] == "scan", record
    print("PASS: a scan is recorded as a scan, not as an order")


def main():
    test_an_approved_decision_is_recorded_with_provenance()
    test_a_refusal_is_recorded_too()
    test_the_trail_is_append_only()
    test_a_failed_write_never_changes_the_decision()
    test_the_trail_records_the_decision_that_was_actually_reached()
    test_a_scan_is_distinguishable_from_an_order()
    print()
    print("All decision-evidence tests passed.")


if __name__ == "__main__":
    main()
