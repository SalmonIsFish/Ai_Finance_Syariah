"""Paper execution adapter boundary for Moomoo submissions.

Malaysia (Bursa) was enabled here on 2026-09-22 at the owner's direction. Until
then this module carried a "superseded by Alpaca, do not extend" note, because
Alpaca is the US path -- but Alpaca has no Bursa access of any kind, so Moomoo
is the only route to a Malaysian order. That makes this module the Malaysian
execution path rather than legacy, and it is held to the same test discipline
as the Alpaca adapter.

WHAT IS AND IS NOT PROVEN FOR MY
--------------------------------
The mapping below is verified against the installed SDK's own enums
(`TrdMarket.MY`, `Market.MY` exist in moomoo 10.09.6908) and against the tests
in `test_moomoo_paper_adapter.py`, which assert the request that gets built.

**No Malaysian order has ever reached a broker.** OpenD was not running when
this was written, so the `MY.` code format and the MY account lookup are
unverified against a live gateway. Do not describe Bursa execution as working
until a real order has filled and reconciled, the way the US path was proven
twice. See CLAUDE.md "Known limitations".

WHAT ACTUALLY PREVENTS A LIVE SUBMISSION
----------------------------------------
Stated precisely, because an earlier version of this docstring overclaimed and a
confident wrong entry costs more than a gap:

1. `TrdEnv.SIMULATE` is hardcoded at all three call sites (`place_order`,
   `order_list_query`, `history_order_list_query`). `trd_env` is not held in a
   variable, is not a parameter, and no env var or settings field reaches it. There is
   no reference to `TrdEnv.REAL` in this module. Changing that requires editing source.
2. `find_active_simulate_account` selects only rows with `trd_env == "SIMULATE"`, so a
   REAL account is never chosen even if the login has one.
3. `market_to_trd_market` falls through to `TrdMarket.NONE`, which is not tradeable.
4. `approval_workflow` refuses the whole approval unless `MOOMOO_MODE` is `paper`, and
   `config.load_settings` raises at startup if it is anything else.

What this is **not**: `paper_execution.py` does not verify the environment of the order
that was actually sent. Its check runs *before* submission, against the status probe's
reported account -- and that probe has already filtered to SIMULATE itself. It is a
second read of the same filter, not a second look at the submitted order.

Note also how this differs in kind from the Alpaca adapter. There, paper and live are
different hosts and `ALPACA_PAPER_BASE_URL` is the only one in the module, so live
submission is impossible rather than merely blocked. Here paper and live share one
OpenD socket and one logged-in account, separated by a hardcoded enum. The guards above
are real and layered, but this is a well-guarded flag, not a wall.
"""

import json
from datetime import datetime, timezone

from config import load_settings


SUPPORTED_REAL_MARKETS = {"US", "MY"}

# OpenD addresses instruments as "<market>.<code>", e.g. US.AAPL, MY.5225.
# Kept as data next to SUPPORTED_REAL_MARKETS so the two cannot drift: a market
# listed as supported but missing a prefix yields no code, and the order is
# refused rather than sent somewhere unintended.
MARKET_CODE_PREFIXES = {"US": "US", "MY": "MY"}


def submit_paper_order(approval: dict, moomoo: dict, adapter: str | None = None) -> dict:
    """``adapter`` is the per-market choice from broker_routing; None keeps the global."""
    settings = load_settings()
    adapter = adapter or settings.paper_execution_adapter
    if adapter == "fake":
        return fake_submit_paper_order(approval=approval, moomoo=moomoo)
    if adapter == "moomoo":
        return submit_moomoo_paper_order(approval=approval)
    return {
        "status": "ADAPTER_NOT_CONFIGURED",
        "adapter": adapter,
        "broker_submission": False,
        "reason": "PAPER_EXECUTION_ADAPTER must be fake or moomoo to submit paper orders",
    }


def reconcile_paper_order(approval: dict) -> dict:
    settings = load_settings()
    broker_submission = broker_submission_from_approval(approval)
    if not broker_submission:
        return {
            "status": "BROKER_NOT_SUBMITTED",
            "adapter": settings.paper_execution_adapter,
            "broker_submission": False,
            "reason": "approval row has no broker submission",
        }

    adapter = broker_submission.get("adapter") or settings.paper_execution_adapter
    if adapter == "fake":
        return fake_reconcile_paper_order(approval=approval, broker_submission=broker_submission)
    if adapter == "moomoo":
        return reconcile_moomoo_paper_order(approval=approval, broker_submission=broker_submission)
    return {
        "status": "ADAPTER_NOT_CONFIGURED",
        "adapter": adapter,
        "broker_submission": bool(approval.get("broker_submission")),
        "reason": "submitted order adapter is not supported for reconciliation",
    }


def load_moomoo_sdk():
    from moomoo import (
        RET_OK,
        OpenSecTradeContext,
        OrderType,
        SecurityFirm,
        Session,
        TimeInForce,
        TrdEnv,
        TrdMarket,
        TrdSide,
    )

    return {
        "RET_OK": RET_OK,
        "OpenSecTradeContext": OpenSecTradeContext,
        "OrderType": OrderType,
        "SecurityFirm": SecurityFirm,
        "Session": Session,
        "TimeInForce": TimeInForce,
        "TrdEnv": TrdEnv,
        "TrdMarket": TrdMarket,
        "TrdSide": TrdSide,
    }


def submit_moomoo_paper_order(*, approval: dict) -> dict:
    settings = load_settings()
    market = (approval.get("shariah_market") or "").upper()
    if market not in SUPPORTED_REAL_MARKETS:
        return {
            "status": "UNSUPPORTED_MARKET",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": f"paper adapter currently supports {', '.join(sorted(SUPPORTED_REAL_MARKETS))} only",
        }

    code = normalize_order_code(approval)
    side = (approval.get("side") or "BUY").upper()
    quantity = approval.get("quantity")
    price = approval.get("price")
    if code is None:
        return {
            "status": "INVALID_SYMBOL",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": "symbol_required",
        }
    if side not in {"BUY", "SELL"}:
        return {
            "status": "INVALID_SIDE",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": "side_must_be_BUY_or_SELL",
        }
    if not isinstance(quantity, int) or quantity <= 0:
        return {
            "status": "INVALID_QUANTITY",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": "positive_integer_quantity_required",
        }
    if not isinstance(price, (int, float)) or price <= 0:
        return {
            "status": "INVALID_PRICE",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": "positive_price_required",
        }

    try:
        sdk = load_moomoo_sdk()
    except ModuleNotFoundError:
        return {
            "status": "SDK_NOT_INSTALLED",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": "moomoo_sdk_missing",
        }
    except Exception as exc:
        return {
            "status": "SDK_UNAVAILABLE",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": type(exc).__name__,
        }

    trd_market = market_to_trd_market(sdk, market)
    security_firm = security_firm_for(sdk, market)
    account_context = sdk["OpenSecTradeContext"](
        filter_trdmarket=trd_market,
        host=settings.moomoo_host,
        port=settings.moomoo_port,
        security_firm=security_firm,
    )
    try:
        ret, accounts = account_context.get_acc_list()
        if ret != sdk["RET_OK"]:
            return {
                "status": "MOOMOO_ACCOUNT_QUERY_FAILED",
                "adapter": "moomoo",
                "broker_submission": False,
                "reason": str(accounts),
            }
        account = find_active_simulate_account(accounts, market)
        if account is None:
            return {
                "status": "MOOMOO_PAPER_ACCOUNT_MISSING",
                "adapter": "moomoo",
                "broker_submission": False,
                "reason": (
                    f"no SIMULATE account authorised for {market} "
                    f"(found: {describe_simulate_accounts(accounts)})"
                ),
            }

        account_id = int(account["acc_id"])
        account_type = str(account.get("acc_type", "UNKNOWN"))
    except Exception as exc:
        return {
            "status": "BROKER_ERROR",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": type(exc).__name__,
        }
    finally:
        account_context.close()

    context = sdk["OpenSecTradeContext"](
        filter_trdmarket=trd_market, host=settings.moomoo_host, port=settings.moomoo_port
    )
    try:
        # `fill_outside_rth` and `session` are omitted rather than sent at their defaults,
        # matching moomoo's own reference implementation, whose place_order.py builds
        # kwargs conditionally:
        #
        #     if fill_outside_rth: order_kwargs["fill_outside_rth"] = True
        #     if session != Session.NONE: order_kwargs["session"] = session
        #
        # Both are US-market concepts. CLAUDE.md flagged sending them unconditionally as
        # an untested risk for Bursa; the vendor's own code says not to, which is better
        # evidence than a guess and costs nothing, since every order this system places is
        # a regular-hours day order and both would always be at their defaults anyway.
        order_kwargs = {
            "price": float(price),
            "qty": float(quantity),
            "code": code,
            "trd_side": side_to_trd_side(sdk, side),
            "order_type": sdk["OrderType"].NORMAL,
            "trd_env": sdk["TrdEnv"].SIMULATE,
            "acc_id": account_id,
            "remark": f"Amanah queue {approval['id']}",
            "time_in_force": sdk["TimeInForce"].DAY,
        }
        ret, order_data = context.place_order(**order_kwargs)
        if ret != sdk["RET_OK"]:
            return {
                "status": "BROKER_REJECTED",
                "adapter": "moomoo",
                "broker_submission": False,
                "reason": str(order_data),
                "environment": "SIMULATE",
                "account_type": account_type,
                "account_suffix": str(account_id)[-4:],
            }

        submitted_at = datetime.now(timezone.utc).isoformat()
        order_id = extract_first_value(order_data, "order_id", "orderID")
        return {
            "status": "BROKER_SUBMITTED",
            "adapter": "moomoo",
            "broker_submission": True,
            "broker_order_id": order_id,
            "submitted_at": submitted_at,
            "symbol": approval.get("symbol"),
            "broker_code": code,
            "side": side,
            "quantity": quantity,
            "price": price,
            "environment": "SIMULATE",
            "account_type": account_type,
            "account_suffix": str(account_id)[-4:],
            "order_status": extract_first_value(order_data, "order_status"),
        }
    except Exception as exc:
        return {
            "status": "BROKER_ERROR",
            "adapter": "moomoo",
            "broker_submission": False,
            "reason": type(exc).__name__,
        }
    finally:
        context.close()


def reconcile_moomoo_paper_order(*, approval: dict, broker_submission: dict) -> dict:
    settings = load_settings()
    market = (approval.get("shariah_market") or "").upper()
    if market not in SUPPORTED_REAL_MARKETS:
        return {
            "status": "UNSUPPORTED_MARKET",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": f"paper reconciliation currently supports {', '.join(sorted(SUPPORTED_REAL_MARKETS))} only",
        }

    broker_order_id = broker_submission.get("broker_order_id")
    code = broker_submission.get("broker_code") or normalize_order_code(approval)
    if not broker_order_id:
        return {
            "status": "BROKER_ORDER_ID_MISSING",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": "broker_order_id_missing",
        }
    if not code:
        return {
            "status": "INVALID_SYMBOL",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": "symbol_required",
        }

    try:
        sdk = load_moomoo_sdk()
    except ModuleNotFoundError:
        return {
            "status": "SDK_NOT_INSTALLED",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": "moomoo_sdk_missing",
        }
    except Exception as exc:
        return {
            "status": "SDK_UNAVAILABLE",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": type(exc).__name__,
        }

    trd_market = market_to_trd_market(sdk, market)
    account_context = sdk["OpenSecTradeContext"](
        filter_trdmarket=trd_market, host=settings.moomoo_host, port=settings.moomoo_port
    )
    try:
        ret, accounts = account_context.get_acc_list()
        if ret != sdk["RET_OK"]:
            return {
                "status": "MOOMOO_ACCOUNT_QUERY_FAILED",
                "adapter": "moomoo",
                "broker_submission": True,
                "reason": str(accounts),
            }
        account = find_active_simulate_account(accounts)
        if account is None:
            return {
                "status": "MOOMOO_PAPER_ACCOUNT_MISSING",
                "adapter": "moomoo",
                "broker_submission": True,
                "reason": "active_simulate_account_not_found",
            }
        account_id = int(account["acc_id"])
        account_type = str(account.get("acc_type", "UNKNOWN"))
    except Exception as exc:
        return {
            "status": "BROKER_RECONCILE_ERROR",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": type(exc).__name__,
        }
    finally:
        account_context.close()

    context = sdk["OpenSecTradeContext"](
        filter_trdmarket=trd_market, host=settings.moomoo_host, port=settings.moomoo_port
    )
    try:
        ret, order_data = context.order_list_query(
            order_id=str(broker_order_id),
            code=code,
            trd_env=sdk["TrdEnv"].SIMULATE,
            acc_id=account_id,
            refresh_cache=True,
            order_market=trd_market,
        )
        if ret != sdk["RET_OK"]:
            return {
                "status": "BROKER_RECONCILE_ERROR",
                "adapter": "moomoo",
                "broker_submission": True,
                "reason": str(order_data),
            }

        row = find_order_row(order_data, broker_order_id)
        source = "open_orders"
        if row is None:
            ret, history_data = context.history_order_list_query(
                code=code,
                trd_env=sdk["TrdEnv"].SIMULATE,
                acc_id=account_id,
                order_market=trd_market,
            )
            if ret != sdk["RET_OK"]:
                return {
                    "status": "BROKER_RECONCILE_ERROR",
                    "adapter": "moomoo",
                    "broker_submission": True,
                    "reason": str(history_data),
                }
            row = find_order_row(history_data, broker_order_id)
            source = "history_orders"

        reconciled_at = datetime.now(timezone.utc).isoformat()
        if row is None:
            return {
                "status": "BROKER_ORDER_NOT_FOUND",
                "adapter": "moomoo",
                "broker_submission": True,
                "broker_order_id": str(broker_order_id),
                "broker_code": code,
                "environment": "SIMULATE",
                "account_type": account_type,
                "account_suffix": str(account_id)[-4:],
                "reconciled_at": reconciled_at,
                "reason": "order not found in open or history order lists",
            }

        order_status = str(row.get("order_status") or "UNKNOWN")
        return {
            "status": lifecycle_status(order_status),
            "adapter": "moomoo",
            "broker_submission": True,
            "broker_order_id": str(broker_order_id),
            "broker_code": row.get("code") or code,
            "source": source,
            "order_status": order_status,
            "side": row.get("trd_side") or approval.get("side"),
            "quantity": numeric_or_original(row.get("qty")),
            "price": numeric_or_original(row.get("price")),
            "dealt_qty": numeric_or_original(row.get("dealt_qty")),
            "dealt_avg_price": numeric_or_original(row.get("dealt_avg_price")),
            "last_err_msg": row.get("last_err_msg"),
            "created_at_broker": row.get("create_time"),
            "updated_at_broker": row.get("updated_time"),
            "environment": "SIMULATE",
            "account_type": account_type,
            "account_suffix": str(account_id)[-4:],
            "reconciled_at": reconciled_at,
            "raw_order": row,
        }
    except Exception as exc:
        return {
            "status": "BROKER_RECONCILE_ERROR",
            "adapter": "moomoo",
            "broker_submission": True,
            "reason": type(exc).__name__,
        }
    finally:
        context.close()


def fake_submit_paper_order(*, approval: dict, moomoo: dict) -> dict:
    submitted_at = datetime.now(timezone.utc).isoformat()
    return {
        "status": "BROKER_SUBMITTED",
        "adapter": "fake",
        "broker_submission": True,
        "broker_order_id": f"FAKE-PAPER-{approval['id']}",
        "submitted_at": submitted_at,
        "symbol": approval.get("symbol"),
        "side": approval.get("side"),
        "quantity": approval.get("quantity"),
        "price": approval.get("price"),
        "environment": moomoo.get("environment", "SIMULATE"),
        "account_type": moomoo.get("account_type", "CASH"),
        "account_suffix": moomoo.get("account_suffix"),
    }


def fake_reconcile_paper_order(*, approval: dict, broker_submission: dict) -> dict:
    reconciled_at = datetime.now(timezone.utc).isoformat()
    return {
        "status": "BROKER_SUBMITTED",
        "adapter": "fake",
        "broker_submission": True,
        "broker_order_id": broker_submission.get("broker_order_id")
        or f"FAKE-PAPER-{approval['id']}",
        "broker_code": broker_submission.get("broker_code") or approval.get("symbol"),
        "order_status": broker_submission.get("order_status", "SUBMITTED"),
        "environment": broker_submission.get("environment", "SIMULATE"),
        "account_type": broker_submission.get("account_type"),
        "account_suffix": broker_submission.get("account_suffix"),
        "reconciled_at": reconciled_at,
        "raw_order": broker_submission,
    }


def normalize_order_code(approval: dict) -> str | None:
    """Build the OpenD instrument code, e.g. 'US.AAPL' or 'MY.5225'.

    Returns None for a market this adapter has no code format for, which the
    callers treat as UNSUPPORTED_MARKET. Guessing a prefix would produce an
    order for whatever instrument that code happened to name on some other
    exchange, so an unknown market must fail rather than improvise.
    """
    symbol = (approval.get("symbol") or "").strip().upper()
    if not symbol:
        return None
    if "." in symbol:
        return symbol
    market = (approval.get("shariah_market") or "").upper()
    if market in MARKET_CODE_PREFIXES:
        return f"{MARKET_CODE_PREFIXES[market]}.{symbol}"
    return None


def market_to_trd_market(sdk: dict, market: str):
    """Map our market label to the SDK's TrdMarket enum.

    Falls through to NONE rather than raising: NONE is not a tradeable market,
    so a market that slips past the SUPPORTED_REAL_MARKETS check still cannot
    place an order by accident.
    """
    if market == "US":
        return sdk["TrdMarket"].US
    if market == "MY":
        return sdk["TrdMarket"].MY
    return sdk["TrdMarket"].NONE


def security_firm_for(sdk: dict, market: str):
    """Which moomoo legal entity holds this market's accounts.

    Moomoo Securities Malaysia is a separate entity from the Hong Kong one, and the SDK
    has a `SecurityFirm` value for each. Neither call site used to pass it, so both took
    the default.

    Account *discovery* happens to work either way -- enumerating 8 firms against 6
    markets on 2026-09-23 showed the same two simulate accounts under every firm. Order
    placement against a Malaysian account is a different call, and querying the wrong
    legal entity for it is not something to leave to luck.

    Unknown markets get the default rather than an invented entity, the same fail-safe
    habit as market_to_trd_market falling through to TrdMarket.NONE.
    """
    firms = sdk["SecurityFirm"]
    if market == "MY":
        return firms.FUTUMY
    if market == "US":
        return firms.FUTUINC
    return firms.FUTUSECURITIES


def side_to_trd_side(sdk: dict, side: str):
    if side == "SELL":
        return sdk["TrdSide"].SELL
    return sdk["TrdSide"].BUY


def rows_from_table(table) -> list[dict]:
    if hasattr(table, "to_dict"):
        return table.to_dict("records")
    if isinstance(table, list):
        return table
    return []


def find_order_row(table, broker_order_id: str | int) -> dict | None:
    target = str(broker_order_id)
    for row in rows_from_table(table):
        if str(row.get("order_id") or row.get("orderID") or "") == target:
            return row
    return None


def find_active_simulate_account(accounts, market: str | None = None) -> dict | None:
    """The ACTIVE simulate account that actually serves `market`.

    Moomoo provisions a simulated account *per market* -- verified 2026-09-23, where one
    login carried an HK account (CASH, sim_acc_type STOCK) and a US one (MARGIN,
    STOCK_AND_OPTION) and no Malaysian one at all. So "the first SIMULATE row" is not a
    safe answer once more than one exists: the two on that login differ in `acc_type`,
    and picking the wrong one silently changes whether account_shariah_gate refuses the
    order for margin.

    The caller's `filter_trdmarket` already pre-filters the list, so this was not biting.
    That made it a single point of failure, which is exactly the kind of thing worth a
    second check rather than a comment.

    `market` is optional so existing callers keep their previous behaviour.
    """
    for row in rows_from_table(accounts):
        if row.get("trd_env") != "SIMULATE" or row.get("acc_status") != "ACTIVE":
            continue
        if market is None:
            return row
        auth = row.get("trdmarket_auth") or []
        if market in list(auth):
            return row
    return None


def describe_simulate_accounts(accounts) -> str:
    """What simulate accounts DO exist, for an error message worth acting on.

    "active_simulate_account_not_found" is true and tells the reader nothing. Naming the
    accounts that exist turns it into a diagnosis.
    """
    found = []
    for row in rows_from_table(accounts):
        if row.get("trd_env") != "SIMULATE":
            continue
        markets = ",".join(str(m) for m in (row.get("trdmarket_auth") or [])) or "no markets"
        found.append(f"{markets}/{row.get('sim_acc_type') or row.get('acc_type')}")
    return ", ".join(found) if found else "none"


def extract_first_value(data, *columns: str) -> str | None:
    rows = rows_from_table(data)
    if rows:
        for column in columns:
            value = rows[0].get(column)
            if value not in {None, ""}:
                return str(value)
    if isinstance(data, dict):
        for column in columns:
            value = data.get(column)
            if value not in {None, ""}:
                return str(value)
    return None


def broker_submission_from_approval(approval: dict) -> dict | None:
    payload = approval.get("payload")
    parsed = {}
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            parsed = {}
    elif isinstance(payload, dict):
        parsed = payload
    broker_submission = parsed.get("broker_submission")
    if isinstance(broker_submission, dict):
        return broker_submission
    return None


def lifecycle_status(order_status: str) -> str:
    normalized = order_status.strip().upper()
    if normalized in {"FILLED_ALL", "FILLED ALL", "FILLED"}:
        return "BROKER_FILLED"
    if normalized in {"FILLED_PART", "FILLED PART", "PARTIAL_FILLED", "PARTIAL FILLED"}:
        return "BROKER_PARTIAL_FILL"
    if normalized in {
        "CANCELLED_ALL",
        "CANCELLED ALL",
        "CANCELLED_PART",
        "CANCELLED PART",
        "FILL_CANCELLED",
        "FILL CANCELLED",
    }:
        return "BROKER_CANCELLED"
    if normalized in {"SUBMIT_FAILED", "SUBMIT FAILED", "FAILED", "DISABLED", "DELETED"}:
        return "BROKER_REJECTED"
    if normalized in {"TIMEOUT", "TIME_OUT", "TIME OUT", "EXPIRED"}:
        return "BROKER_EXPIRED"
    if normalized in {"SUBMITTING", "SUBMITTED", "WAITING_SUBMIT", "WAITING SUBMIT", "UNSUBMITTED"}:
        return "BROKER_SUBMITTED"
    return "BROKER_STATUS_UNKNOWN"


def numeric_or_original(value):
    if value in {None, ""}:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return value
