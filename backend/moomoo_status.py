"""Read-only Moomoo OpenD status checks.

The probe is **per market**. It used to be hardcoded to ``TrdMarket.US`` while
paper_execution.py gated *every* order on it, including Malaysian ones -- so a Bursa
order could be refused MOOMOO_NOT_READY because no *US* simulate account was found, and
a passing US probe said nothing at all about MY readiness. Both halves of that are
wrong, and the reason string said ``active_us_simulate_account_not_found`` regardless.

Markets come from moomoo_paper_adapter.SUPPORTED_REAL_MARKETS so the two cannot drift:
a market the adapter will not submit for is not a market worth probing.
"""

import socket

from config import load_settings
from moomoo_paper_adapter import (
    SUPPORTED_REAL_MARKETS,
    describe_simulate_accounts,
    find_active_simulate_account,
    market_to_trd_market,
    security_firm_for,
)


def _port_reachable(host: str, port: int, *, timeout: float = 1.5) -> bool:
    """Cheap pre-check so a closed OpenD port fails in ~1.5s instead of the
    minutes-long retry/backoff the moomoo SDK runs internally when a real
    connection attempt is refused."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _all_accounts(context_factory, settings, trd_market_none):
    """Every account regardless of market, for the diagnosis on a failed lookup.

    Best-effort: a failure here must never change the verdict, only the wording of it.
    """
    try:
        # TrdMarket.NONE means "do not filter". Omitting it defaults to the Hong Kong
        # market, which is why an earlier version of this message listed only the HK
        # account and missed the US one entirely.
        context = context_factory(
            filter_trdmarket=trd_market_none,
            host=settings.moomoo_host,
            port=settings.moomoo_port,
        )
    except Exception:
        return []
    try:
        ret, accounts = context.get_acc_list()
        return accounts if ret == 0 else []
    except Exception:
        return []
    finally:
        try:
            context.close()
        except Exception:
            pass


def check_moomoo_status(market: str = "US") -> dict:
    """Is there an ACTIVE SIMULATE account for ``market`` behind a listening OpenD?

    A reachable port proves only that *something* is listening -- not that it is OpenD,
    that OpenD is logged in, or that the login has an account for this market. The real
    check is get_acc_list() below.
    """
    settings = load_settings()
    normalized_market = str(market or "US").strip().upper()
    if not _port_reachable(settings.moomoo_host, settings.moomoo_port):
        return {
            "status": "unreachable",
            "host": settings.moomoo_host,
            "port": settings.moomoo_port,
            "mode": settings.moomoo_mode,
            "paper_account_ready": False,
            "paper_execution_enabled": settings.paper_execution_enabled,
            "broker_submission": False,
            "reason": "moomoo_opend_not_listening",
        }
    try:
        from moomoo import OpenSecTradeContext, SecurityFirm, TrdMarket
    except ModuleNotFoundError:
        return {
            "status": "not_installed",
            "host": settings.moomoo_host,
            "port": settings.moomoo_port,
            "mode": settings.moomoo_mode,
            "paper_account_ready": False,
            "paper_execution_enabled": settings.paper_execution_enabled,
            "broker_submission": False,
            "reason": "moomoo_sdk_missing",
        }
    except Exception as exc:
        return {
            "status": "sdk_unavailable",
            "host": settings.moomoo_host,
            "port": settings.moomoo_port,
            "mode": settings.moomoo_mode,
            "paper_account_ready": False,
            "paper_execution_enabled": settings.paper_execution_enabled,
            "broker_submission": False,
            "reason": type(exc).__name__,
        }

    if normalized_market not in SUPPORTED_REAL_MARKETS:
        return {
            "status": "unsupported_market",
            "host": settings.moomoo_host,
            "port": settings.moomoo_port,
            "mode": settings.moomoo_mode,
            "market": normalized_market,
            "paper_account_ready": False,
            "paper_execution_enabled": settings.paper_execution_enabled,
            "broker_submission": False,
            "reason": f"market_{normalized_market.lower()}_not_supported",
        }

    sdk = {"TrdMarket": TrdMarket, "SecurityFirm": SecurityFirm}
    trd_market = market_to_trd_market(sdk, normalized_market)
    context = OpenSecTradeContext(
        filter_trdmarket=trd_market,
        host=settings.moomoo_host,
        port=settings.moomoo_port,
        security_firm=security_firm_for(sdk, normalized_market),
    )
    try:
        ret, accounts = context.get_acc_list()
        if ret != 0:
            return {
                "status": "unreachable",
                "host": settings.moomoo_host,
                "port": settings.moomoo_port,
                "mode": settings.moomoo_mode,
                "paper_account_ready": False,
                "paper_execution_enabled": settings.paper_execution_enabled,
                "broker_submission": False,
                "reason": f"get_acc_list_failed:{ret}",
            }

        # Market-aware: an account is usable only if it is authorised for this market.
        # The 2026-09-23 enumeration found an HK-only and a US-only simulate account on
        # one login, so "the first SIMULATE row" is not a safe answer once more than one
        # exists -- and they differed in acc_type, which decides the Riba gate's verdict.
        selected = find_active_simulate_account(accounts, normalized_market)
        if selected is None:
            # `accounts` is filtered to this market, so it lists no simulate account by
            # definition -- saying "none present" would be true and useless. One extra
            # unfiltered query, only on the failure path, turns the refusal into a
            # diagnosis: it names the paper accounts that DO exist, which is what tells
            # the reader whether to ask moomoo for provisioning or fix a config.
            present = describe_simulate_accounts(
                _all_accounts(OpenSecTradeContext, settings, TrdMarket.NONE)
            )
            return {
                "status": "paper_account_missing",
                "host": settings.moomoo_host,
                "port": settings.moomoo_port,
                "mode": settings.moomoo_mode,
                "paper_account_ready": False,
                "paper_execution_enabled": settings.paper_execution_enabled,
                "broker_submission": False,
                "market": normalized_market,
                "reason": (
                    f"active_{normalized_market.lower()}_simulate_account_not_found "
                    f"(simulate accounts on this login: {present})"
                ),
            }

        account_id = str(selected["acc_id"])
        return {
            "status": "paper_account_ready",
            "host": settings.moomoo_host,
            "port": settings.moomoo_port,
            "mode": settings.moomoo_mode,
            "paper_account_ready": True,
            "paper_execution_enabled": settings.paper_execution_enabled,
            "broker_submission": False,
            "environment": str(selected["trd_env"]),
            "account_type": str(selected["acc_type"]),
            "account_status": str(selected["acc_status"]),
            "account_suffix": account_id[-4:],
            "market": normalized_market,
        }
    except Exception as exc:
        return {
            "status": "unreachable",
            "host": settings.moomoo_host,
            "port": settings.moomoo_port,
            "mode": settings.moomoo_mode,
            "paper_account_ready": False,
            "paper_execution_enabled": settings.paper_execution_enabled,
            "broker_submission": False,
            "reason": type(exc).__name__,
        }
    finally:
        context.close()
