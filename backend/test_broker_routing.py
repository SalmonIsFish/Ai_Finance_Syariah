"""Which broker submits an order is decided per market, and fails closed.

`market_data.provider_for` already routes pricing per market, because Alpaca and Tiingo
carry no Bursa data at all. Execution has the same shape of problem for the same reason,
and until now had one process-wide setting.

**What was actually wrong, stated accurately.** A Malaysian order was never silently sent
to Alpaca: `alpaca_paper_adapter.SUPPORTED_REAL_MARKETS` is `{"US"}` and a non-US approval
was refused with `UNSUPPORTED_MARKET` before anything was built. The first test below
pins that, so the record stays honest. What was wrong is that the *status probe* was
picked by the same global flag -- so a Bursa order was gated on whether the Alpaca account
was ready -- and that the refusal's stated reason described Alpaca's limits rather than
the system's decision.
"""

import pytest
import alpaca_paper_adapter
import broker_routing
from broker_routing import (
    ADAPTER_MARKETS,
    DEFAULT_MY_ADAPTER,
    CODE_MARKET_NOT_CONFIGURED,
    CODE_MARKET_UNSUPPORTED,
    CODE_NO_ADAPTER,
    adapter_for,
    market_for,
)


class Settings:
    def __init__(self, adapter="disabled", adapter_my="disabled"):
        self.paper_execution_adapter = adapter
        self.paper_execution_adapter_my = adapter_my


def approval(symbol="AAPL", market="US"):
    return {"id": 1, "symbol": symbol, "shariah_market": market, "side": "BUY", "quantity": 1}


# --- the record: what the old behaviour actually was ---------------------------------


def test_the_alpaca_adapter_already_refused_a_malaysian_order():
    """Kept as a fact, not an assumption. The old outcome was safe; the reason was not."""
    assert alpaca_paper_adapter.SUPPORTED_REAL_MARKETS == {"US"}
    result = alpaca_paper_adapter.submit_paper_order(
        approval(symbol="4197", market="MY"), {}, "alpaca_mcp"
    )
    assert result["status"] == "UNSUPPORTED_MARKET"
    assert result["broker_submission"] is False


# --- market resolution ---------------------------------------------------------------


def test_the_recorded_verdict_market_wins():
    """shariah_market was written by the gate that screened it, so it is authoritative."""
    assert market_for(approval(symbol="4197", market="MY")) == "MY"
    assert market_for(approval(symbol="AAPL", market="US")) == "US"


def test_a_row_without_a_recorded_market_falls_back_to_the_shared_detector():
    assert market_for({"symbol": "4197"}) == "MY"
    assert market_for({"symbol": "AAPL"}) == "US"


def test_the_fallback_is_the_same_function_the_shariah_gate_uses():
    """Three routings on one symbol must not disagree."""
    from agents.shariah_agent import detect_market

    for symbol in ("4197", "5225", "AAPL", "CVX", "BRK.B", ""):
        assert market_for({"symbol": symbol}) == detect_market(symbol)


# --- routing -------------------------------------------------------------------------


def test_a_us_order_uses_the_primary_adapter():
    routing = adapter_for(approval(), Settings(adapter="alpaca_mcp"))
    assert routing["status"] == "PASS"
    assert routing["adapter"] == "alpaca_mcp"
    assert routing["market"] == "US"


def test_a_malaysian_order_does_not_inherit_the_us_adapter():
    """The bug this module exists for: one setting answering for both markets."""
    routing = adapter_for(approval(symbol="4197", market="MY"), Settings(adapter="alpaca_mcp"))
    assert routing["status"] == "REJECT"
    assert routing["adapter"] != "alpaca_mcp"


def test_malaysian_execution_defaults_off_rather_than_to_moomoo():
    """No Moomoo order has ever reached a broker; defaulting to it would enable an
    unproven path by implication rather than by decision."""
    assert broker_routing.DEFAULT_MY_ADAPTER == "disabled"
    routing = adapter_for(approval(symbol="4197", market="MY"), Settings(adapter="alpaca_mcp"))
    assert routing["code"] == CODE_MARKET_NOT_CONFIGURED
    assert "PAPER_EXECUTION_ADAPTER_MY" in routing["reason"]


def test_malaysian_execution_is_enabled_by_typing_it():
    routing = adapter_for(
        approval(symbol="4197", market="MY"), Settings(adapter="alpaca_mcp", adapter_my="moomoo")
    )
    assert routing["status"] == "PASS"
    assert routing["adapter"] == "moomoo"


def test_enabling_malaysia_does_not_move_us_orders_off_alpaca():
    """The two markets must stay independent, which is the whole point."""
    settings = Settings(adapter="alpaca_mcp", adapter_my="moomoo")
    assert adapter_for(approval(), settings)["adapter"] == "alpaca_mcp"
    assert adapter_for(approval("4197", "MY"), settings)["adapter"] == "moomoo"


@pytest.mark.parametrize("switch", ["fake", "disabled"])
def test_the_global_switches_apply_to_every_market(switch):
    """fake and disabled are a test mode and an off switch, not brokers.

    If MY could opt out of `fake`, a test configuring the fake adapter would still reach a
    real one for Bursa orders.
    """
    settings = Settings(adapter=switch, adapter_my="moomoo")
    for order in (approval(), approval("4197", "MY")):
        routing = adapter_for(order, settings)
        if switch == "fake":
            assert routing["adapter"] == "fake"
        else:
            assert routing["status"] == "REJECT"
            assert routing["code"] == CODE_NO_ADAPTER


def test_globally_disabled_keeps_its_original_reason_code():
    """The common 'execution is off' case must read exactly as it always did."""
    routing = adapter_for(approval(), Settings(adapter="disabled"))
    assert routing["code"] == CODE_NO_ADAPTER


def test_an_adapter_that_cannot_serve_the_market_is_refused_by_name():
    routing = adapter_for(
        approval("4197", "MY"), Settings(adapter="alpaca_mcp", adapter_my="alpaca")
    )
    assert routing["status"] == "REJECT"
    assert routing["code"] == CODE_MARKET_UNSUPPORTED
    assert "alpaca" in routing["reason"]
    assert "US" in routing["reason"]


def test_an_unknown_adapter_fails_closed():
    routing = adapter_for(approval(), Settings(adapter="etrade"))
    assert routing["status"] == "REJECT"
    assert routing["adapter"] == "etrade"


def test_an_unknown_market_fails_closed():
    """A market nothing claims to support must not fall through to a default."""
    routing = adapter_for(approval("0700", "HK"), Settings(adapter="alpaca_mcp"))
    assert routing["status"] == "REJECT"


# --- the support table must not overclaim --------------------------------------------


def test_the_table_agrees_with_the_alpaca_adapter_itself():
    """A table claiming more than the adapter does would route orders into a refusal."""
    for name in ("alpaca", "alpaca_mcp"):
        assert ADAPTER_MARKETS[name] == alpaca_paper_adapter.SUPPORTED_REAL_MARKETS


def test_the_table_agrees_with_the_moomoo_adapter_itself():
    import moomoo_paper_adapter

    assert ADAPTER_MARKETS["moomoo"] == moomoo_paper_adapter.SUPPORTED_REAL_MARKETS


def test_every_adapter_the_submit_paths_accept_appears_in_the_table():
    """A new adapter added to a dispatch but not here would be refused as unknown --
    which is the safe direction, and this test says so out loud."""
    assert set(alpaca_paper_adapter.ALPACA_ADAPTERS) <= set(ADAPTER_MARKETS)
    assert {"fake", "disabled", "moomoo"} <= set(ADAPTER_MARKETS)


# --- the resolved adapter reaches the submit call ------------------------------------


def test_the_resolved_adapter_overrides_the_global_setting(monkeypatch):
    """Both adapters self-dispatch on the global setting; the per-market choice must win."""
    seen = {}

    def fake_submit(*, approval):
        seen["called"] = "moomoo"
        return {"status": "PASS", "broker_submission": True, "adapter": "moomoo"}

    import moomoo_paper_adapter

    monkeypatch.setattr(moomoo_paper_adapter, "submit_moomoo_paper_order", fake_submit)
    monkeypatch.setattr(
        moomoo_paper_adapter, "load_settings", lambda: Settings(adapter="alpaca_mcp")
    )

    result = moomoo_paper_adapter.submit_paper_order(approval("4197", "MY"), {}, "moomoo")

    assert seen["called"] == "moomoo"
    assert result["broker_submission"] is True


def test_omitting_the_adapter_keeps_the_previous_global_behaviour(monkeypatch):
    """The parameter is additive: existing callers must be unaffected."""
    import moomoo_paper_adapter

    monkeypatch.setattr(moomoo_paper_adapter, "load_settings", lambda: Settings(adapter="disabled"))
    result = moomoo_paper_adapter.submit_paper_order(approval("4197", "MY"), {})
    assert result["status"] == "ADAPTER_NOT_CONFIGURED"


def test_the_default_has_exactly_one_source_of_truth():
    """A deliberate-break check caught this: two defaults, and changing one did nothing.

    config.py used to spell "disabled" itself, so `broker_routing.DEFAULT_MY_ADAPTER` was
    a decoration -- the real default lived elsewhere and the end-to-end suite stayed green
    when this constant was changed to "moomoo". config.py now reads this constant.
    """
    import os

    import config

    original = os.environ.pop("PAPER_EXECUTION_ADAPTER_MY", None)
    try:
        assert config.load_settings().paper_execution_adapter_my == DEFAULT_MY_ADAPTER
    finally:
        if original is not None:
            os.environ["PAPER_EXECUTION_ADAPTER_MY"] = original

    source = __import__("pathlib").Path(config.__file__).read_text(encoding="utf-8")
    assert "DEFAULT_MY_ADAPTER" in source, "config.py must read the constant, not re-spell it"
