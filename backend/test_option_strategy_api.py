"""Verify GET /stock/{symbol}/option-strategy proposes without approving.

The risk this guards is a category error, not a crash: a selected contract is a
proposal that has passed no gate, and if the endpoint ever presents one as though
it were cleared, the whole "enforces and proves" claim goes with it.

Nothing reaches a network: the quote and the two selectors are seams.
"""
import pytest
import auth
from option_permissibility import (
    OPTION_DETERMINATION,
    OPTION_POLICY_PERMITTED,
    REASON_NOT_PERMITTED,
)
@pytest.fixture(autouse=True)
def _owner_auth_fixture():
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    yield
    _my_app.dependency_overrides.pop(_get_owner_actor, None)


import local_api
import option_strategy_api
from fastapi.testclient import TestClient


CASH_ACCOUNT = {
    "account_type": "CASH",
    "shares_held": 0,
    "cash_collateral": 100000.0,
    "uses_margin": False,
}

HOLDER_ACCOUNT = {**CASH_ACCOUNT, "shares_held": 100}

QUOTE = {"latest_close": 203.92, "source": "alpaca_iex", "latest_date": "2026-08-18", "bars": 10}

# Test-only seam. The shipped determination refuses every option (see
# option_permissibility.py); these tests assert the *proposal* behaviour, which must stay
# exercised so a scholarly ruling either way is one constant away rather than a rebuild.
# test_the_endpoint_refuses_under_the_shipped_determination below covers the real default.
PERMITTED_DETERMINATION = dict(OPTION_DETERMINATION, status=OPTION_POLICY_PERMITTED)


def fake_quote(symbol, **kwargs):
    return QUOTE


def selection_for(strategy: str) -> dict:
    return {
        "status": "SELECTED",
        "strategy": strategy,
        "underlying": "CVX",
        "option_contract": {
            "strategy": strategy,
            "option_type": "call" if strategy == "COVERED_CALL" else "put",
            "underlying": "CVX",
            "strike": 212.5,
            "expiration": "2026-08-21",
            "occ_symbol": "CVX260821C00212500",
        },
        "side": "SELL",
        "contracts": 1,
        "price": 1.35,
        "rationale": "picked the 212.5 strike, 4.2% OTM, 2 DTE",
    }


def recording_selectors(strategy: str):
    calls = []

    def covered_call(symbol, **kwargs):
        calls.append(("COVERED_CALL", symbol, kwargs))
        return selection_for("COVERED_CALL")

    def cash_secured_put(symbol, **kwargs):
        calls.append(("CASH_SECURED_PUT", symbol, kwargs))
        return selection_for("CASH_SECURED_PUT")

    return (covered_call, cash_secured_put), calls


def test_a_proposal_is_never_presented_as_approved() -> None:
    selectors, _ = recording_selectors("COVERED_CALL")
    result = option_strategy_api.propose_option_strategy(
        "cvx", account=HOLDER_ACCOUNT, quote=fake_quote, selectors=selectors, determination=PERMITTED_DETERMINATION
    )

    next_step = result["next_step"]
    assert next_step["approved"] is False
    assert "not an approval" in next_step["note"]
    assert set(next_step["gates_not_yet_run"]) == {
        "shariah_gate",
        "option_structure_gate",
        "account_shariah_gate",
        "risk_checks",
    }
    # No key anywhere may claim the thing is approved or submittable.
    assert "broker_submission" not in result
    assert result["status"] == "SELECTED"


def test_the_preview_request_is_postable_as_an_option_order() -> None:
    selectors, _ = recording_selectors("COVERED_CALL")
    result = option_strategy_api.propose_option_strategy(
        "CVX", account=HOLDER_ACCOUNT, quote=fake_quote, selectors=selectors, determination=PERMITTED_DETERMINATION
    )
    body = result["next_step"]["preview_request"]

    assert body["symbol"] == "CVX"
    assert body["asset_class"] == "option"
    assert body["side"] == "SELL"
    assert body["quantity"] == 1
    assert body["option_contract"]["occ_symbol"] == "CVX260821C00212500"
    # It must satisfy the endpoint's own request model.
    local_api.PaperPreviewRequest(**body)


def test_cash_secured_put_is_sized_from_settled_cash_not_buying_power() -> None:
    selectors, calls = recording_selectors("CASH_SECURED_PUT")
    account = {**CASH_ACCOUNT, "cash_collateral": 25000.0, "buying_power": 100000.0}
    option_strategy_api.propose_option_strategy(
        "CVX", account=account, strategy="cash_secured_put", quote=fake_quote, selectors=selectors, determination=PERMITTED_DETERMINATION
    )
    strategy, symbol, kwargs = calls[0]
    assert strategy == "CASH_SECURED_PUT"
    assert kwargs["cash_available"] == 25000.0, kwargs
    assert 100000.0 not in kwargs.values(), "buying power must never reach the selector"


def test_strategy_defaults_to_the_conservative_one() -> None:
    """With shares on hand, write calls against stock already owned."""
    selectors, calls = recording_selectors("COVERED_CALL")
    option_strategy_api.propose_option_strategy(
        "CVX", account=HOLDER_ACCOUNT, quote=fake_quote, selectors=selectors, determination=PERMITTED_DETERMINATION
    )
    assert calls[0][0] == "COVERED_CALL", calls

    selectors, calls = recording_selectors("CASH_SECURED_PUT")
    option_strategy_api.propose_option_strategy(
        "CVX", account=CASH_ACCOUNT, quote=fake_quote, selectors=selectors, determination=PERMITTED_DETERMINATION
    )
    assert calls[0][0] == "CASH_SECURED_PUT", calls

    # 99 shares cannot cover a contract, so it must not pick a covered call.
    selectors, calls = recording_selectors("CASH_SECURED_PUT")
    option_strategy_api.propose_option_strategy(
        "CVX", account={**CASH_ACCOUNT, "shares_held": 99}, quote=fake_quote, selectors=selectors, determination=PERMITTED_DETERMINATION
    )
    assert calls[0][0] == "CASH_SECURED_PUT", calls


def test_multi_leg_and_unknown_strategies_are_refused() -> None:
    for requested in ["iron_condor", "straddle", "vertical_spread", "protective_put"]:
        result = option_strategy_api.propose_option_strategy(
            "CVX",
            account=HOLDER_ACCOUNT,
            strategy=requested,
            quote=fake_quote,
            determination=PERMITTED_DETERMINATION,
        )
        assert result["status"] == "REJECT", (requested, result)
        assert "Level 1 only" in result["reason"], result


def test_no_selection_yields_no_preview_request() -> None:
    def no_candidate(symbol, **kwargs):
        return {"status": "NO_CANDIDATE", "strategy": "COVERED_CALL", "reason": "no strike in band"}

    result = option_strategy_api.propose_option_strategy(
        "CVX",
        account=HOLDER_ACCOUNT,
        quote=fake_quote,
        selectors=(no_candidate, no_candidate),
        determination=PERMITTED_DETERMINATION,
    )
    assert result["status"] == "NO_CANDIDATE"
    assert result["next_step"]["preview_request"] is None
    assert result["next_step"]["approved"] is False


def test_route_resolves_account_facts_from_the_broker_context() -> None:
    original_context = local_api.broker_account_context
    original_propose = local_api.propose_option_strategy
    seen = {}

    local_api.broker_account_context = lambda connection, preview: (
        seen.update({"preview": preview}) or HOLDER_ACCOUNT
    )

    def spy(symbol, *, account, strategy=None):
        seen["account"] = account
        return option_strategy_api.propose_option_strategy(
            symbol,
            account=account,
            strategy=strategy,
            quote=fake_quote,
            selectors=recording_selectors("COVERED_CALL")[0],
            determination=PERMITTED_DETERMINATION,
        )

    local_api.propose_option_strategy = spy
    try:
        client = TestClient(local_api.app)
        response = client.get("/stock/CVX/option-strategy")
        assert response.status_code == 200, response.status_code
        body = response.json()
        assert body["next_step"]["approved"] is False
        assert body["account_context"]["shares_held"] == 100
        # The context must be asked for as an option, or it answers with the
        # equity fallback and an option order would be sized wrong.
        assert seen["preview"]["asset_class"] == "option"
        assert seen["preview"]["symbol"] == "CVX"
    finally:
        local_api.broker_account_context = original_context
        local_api.propose_option_strategy = original_propose


def test_an_unreachable_broker_is_not_reported_as_an_empty_account() -> None:
    """A failed account query must not be restated as a fact about the account."""
    import os
    original_adapter = os.environ.get("PAPER_EXECUTION_ADAPTER")
    os.environ["PAPER_EXECUTION_ADAPTER"] = "alpaca"
    
    original_status = local_api.check_alpaca_status
    unreachable = {
        "base_url": "https://paper-api.alpaca.markets",
        "mode": "paper",
        "adapter": "alpaca_mcp",
        "paper_account_ready": False,
        "status": "unreachable",
        "reason": "URLError",
    }
    local_api.check_alpaca_status = lambda: unreachable
    try:
        connection = local_api.db()
        try:
            context = local_api.broker_account_context(
                connection, {"symbol": "CVX", "asset_class": "option"}
            )
        finally:
            connection.close()

        # The caller must be able to tell "broker did not answer" from
        # "account answered, and it holds nothing".
        assert context["broker_status"] == "unreachable", context
        assert context["account_type"] == "UNKNOWN", context

        healthy = {
            **unreachable,
            "status": "paper_account_ready",
            "account_type": "CASH",
            "cash": 50.0,
        }
        local_api.check_alpaca_status = lambda: healthy
        connection = local_api.db()
        try:
            good = local_api.broker_account_context(
                connection, {"symbol": "CVX", "asset_class": "option"}
            )
        finally:
            connection.close()
        assert good["broker_status"] == "paper_account_ready", good
        assert good["cash_collateral"] == 50.0, good
    finally:
        local_api.check_alpaca_status = original_status


def main() -> None:
    test_an_unreachable_broker_is_not_reported_as_an_empty_account()
    test_a_proposal_is_never_presented_as_approved()
    test_the_preview_request_is_postable_as_an_option_order()
    test_cash_secured_put_is_sized_from_settled_cash_not_buying_power()
    test_strategy_defaults_to_the_conservative_one()
    test_multi_leg_and_unknown_strategies_are_refused()
    test_no_selection_yields_no_preview_request()
    test_route_resolves_account_facts_from_the_broker_context()
    print("PASS: the strategy endpoint proposes contracts and never claims approval.")


if __name__ == "__main__":
    import auth
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    try:
        main()
    finally:
        _my_app.dependency_overrides.pop(_get_owner_actor, None)


def test_the_endpoint_refuses_under_the_shipped_determination() -> None:
    """The default the system actually ships: no contract is proposed at all.

    The tests above pass a permissive determination through the test-only seam so the
    proposal machinery stays exercised. This one uses the determination in force, and it
    is the behaviour a caller will really see.
    """
    result = option_strategy_api.propose_option_strategy(
        "CVX", account=HOLDER_ACCOUNT, quote=fake_quote
    )
    assert result["status"] == "REJECT"
    assert result["reason"] == REASON_NOT_PERMITTED
    assert result["determination"]["review_status"] == "PENDING_SCHOLARLY_REVIEW"
    # No contract, and nothing that could be mistaken for one.
    assert "selection" not in result
    assert "next_step" not in result


def test_the_refusal_costs_no_option_chain_request() -> None:
    """Refusing before the quote seam matters: the chain fetch is a live paid call."""
    calls = []

    def counting_quote(symbol, **kwargs):
        calls.append(symbol)
        return QUOTE

    option_strategy_api.propose_option_strategy(
        "CVX", account=HOLDER_ACCOUNT, quote=counting_quote
    )
    assert calls == [], "the permissibility check must short-circuit before any market data"
