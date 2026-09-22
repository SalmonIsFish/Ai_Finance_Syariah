"""Verify the real Moomoo adapter path without connecting to OpenD."""

import os
import json

import moomoo_paper_adapter


class FakeConstants:
    BUY = "BUY"
    SELL = "SELL"
    NORMAL = "NORMAL"
    NONE = "NONE"
    DAY = "DAY"
    SIMULATE = "SIMULATE"
    US = "US"
    MY = "MY"
    NONE_MARKET = "NONE"


class FakeTradeContext:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.place_order_calls = []
        FakeTradeContext.instances.append(self)

    def get_acc_list(self):
        return 0, [
            {"acc_id": 101, "trd_env": "REAL", "acc_type": "CASH", "acc_status": "ACTIVE"},
            {
                "acc_id": 987654321,
                "trd_env": "SIMULATE",
                "acc_type": "MARGIN",
                "acc_status": "ACTIVE",
            },
        ]

    def place_order(self, **kwargs):
        self.place_order_calls.append(kwargs)
        return 0, [{"order_id": "MOOMOO-ORDER-1", "order_status": "SUBMITTING"}]

    def order_list_query(self, **kwargs):
        return 0, [
            {
                "code": "US.AAPL",
                "trd_side": "BUY",
                "order_status": "FILLED_ALL",
                "order_id": "MOOMOO-ORDER-1",
                "qty": 3.0,
                "price": 195.5,
                "create_time": "2026-07-24 13:20:00",
                "updated_time": "2026-07-24 13:22:00",
                "dealt_qty": 3.0,
                "dealt_avg_price": 195.45,
                "last_err_msg": "",
            }
        ]

    def history_order_list_query(self, **kwargs):
        return 0, []

    def close(self):
        self.closed = True


def fake_sdk():
    return {
        "RET_OK": 0,
        "OpenSecTradeContext": FakeTradeContext,
        "OrderType": FakeConstants,
        "Session": FakeConstants,
        "TimeInForce": FakeConstants,
        "TrdEnv": FakeConstants,
        "TrdMarket": FakeConstants,
        "TrdSide": FakeConstants,
    }


def main() -> None:
    original_env = os.environ.get("PAPER_EXECUTION_ADAPTER")
    original_loader = moomoo_paper_adapter.load_moomoo_sdk
    os.environ["PAPER_EXECUTION_ADAPTER"] = "moomoo"
    moomoo_paper_adapter.load_moomoo_sdk = fake_sdk
    FakeTradeContext.instances.clear()
    try:
        approval = {
            "id": 42,
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 3,
            "price": 195.5,
            "shariah_market": "US",
        }
        result = moomoo_paper_adapter.submit_paper_order(approval, moomoo={})
        assert result["status"] == "BROKER_SUBMITTED"
        assert result["broker_submission"] is True
        assert result["adapter"] == "moomoo"
        assert result["broker_order_id"] == "MOOMOO-ORDER-1"
        assert result["broker_code"] == "US.AAPL"
        assert result["account_suffix"] == "4321"

        account_context = FakeTradeContext.instances[0]
        assert account_context.kwargs["filter_trdmarket"] == "US"
        assert account_context.closed is True

        order_context = FakeTradeContext.instances[1]
        assert order_context.kwargs["filter_trdmarket"] == "US"
        assert order_context.closed is True
        call = order_context.place_order_calls[0]
        assert call["code"] == "US.AAPL"
        assert call["trd_side"] == "BUY"
        assert call["order_type"] == "NORMAL"
        assert call["trd_env"] == "SIMULATE"
        assert call["acc_id"] == 987654321
        assert call["remark"] == "Amanah queue 42"
        assert call["time_in_force"] == "DAY"
        assert call["fill_outside_rth"] is False
        assert call["session"] == "NONE"

        sell_result = moomoo_paper_adapter.submit_paper_order(
            {
                "id": 44,
                "symbol": "AAPL",
                "side": "SELL",
                "quantity": 1,
                "price": 200.0,
                "shariah_market": "US",
            },
            moomoo={},
        )
        assert sell_result["status"] == "BROKER_SUBMITTED"
        sell_call = FakeTradeContext.instances[-1].place_order_calls[0]
        assert sell_call["code"] == "US.AAPL"
        assert sell_call["trd_side"] == "SELL"
        assert sell_call["remark"] == "Amanah queue 44"

        # Malaysia. This case previously asserted UNSUPPORTED_MARKET; Bursa was
        # enabled on 2026-09-22 because Alpaca has no Bursa access at all, so
        # Moomoo is the only possible Malaysian route. Assert the request that
        # gets built, not merely that it was accepted -- a MY order carrying a
        # US code would be an order for a different instrument entirely.
        my_result = moomoo_paper_adapter.submit_paper_order(
            {
                "id": 43,
                "symbol": "5225",
                "side": "BUY",
                "quantity": 100,
                "price": 6.5,
                "shariah_market": "MY",
            },
            moomoo={},
        )
        assert my_result["status"] == "BROKER_SUBMITTED"
        assert my_result["broker_code"] == "MY.5225"

        my_context = FakeTradeContext.instances[-1]
        assert my_context.kwargs["filter_trdmarket"] == "MY"
        my_call = my_context.place_order_calls[0]
        assert my_call["code"] == "MY.5225", "a Bursa order must not carry a US code"
        assert my_call["trd_side"] == "BUY"
        assert my_call["trd_env"] == "SIMULATE", "Bursa must never reach a REAL account"
        assert my_call["remark"] == "Amanah queue 43"

        # A market with no code prefix must still be refused. Enabling MY must
        # not turn the adapter into one that improvises a prefix for anything.
        unsupported = moomoo_paper_adapter.submit_paper_order(
            {
                "id": 45,
                "symbol": "0700",
                "side": "BUY",
                "quantity": 1,
                "price": 1.0,
                "shariah_market": "HK",
            },
            moomoo={},
        )
        assert unsupported["status"] == "UNSUPPORTED_MARKET"
        assert unsupported["broker_submission"] is False

        reconciliation = moomoo_paper_adapter.reconcile_paper_order(
            {
                "id": 42,
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 3,
                "price": 195.5,
                "shariah_market": "US",
                "broker_submission": True,
                "payload": json.dumps(
                    {
                        "broker_submission": {
                            "adapter": "moomoo",
                            "broker_order_id": "MOOMOO-ORDER-1",
                            "broker_code": "US.AAPL",
                            "environment": "SIMULATE",
                        }
                    }
                ),
            }
        )
        assert reconciliation["status"] == "BROKER_FILLED"
        assert reconciliation["broker_submission"] is True
        assert reconciliation["broker_order_id"] == "MOOMOO-ORDER-1"
        assert reconciliation["order_status"] == "FILLED_ALL"
        assert reconciliation["dealt_qty"] == 3.0
        assert reconciliation["dealt_avg_price"] == 195.45
    finally:
        moomoo_paper_adapter.load_moomoo_sdk = original_loader
        if original_env is None:
            os.environ.pop("PAPER_EXECUTION_ADAPTER", None)
        else:
            os.environ["PAPER_EXECUTION_ADAPTER"] = original_env

    print("PASS: Moomoo paper adapter maps safe orders to OpenD place_order.")


if __name__ == "__main__":
    main()
