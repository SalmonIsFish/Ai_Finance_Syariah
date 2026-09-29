"""Stand-ins for the four network seams, all reporting "the provider is down".

Tests run offline, and run_all_tests.py enforces it (backend/netguard). A test that needs
"the provider answered X" swaps the seam itself, the way test_alpaca_market_data.py does.
This module is for the far more common case: a test that exercises something else --
auth, screening, approval -- and merely passes *through* a code path that fetches data.

For those, "unavailable" is the honest stand-in. It is what the guard would produce
anyway, and every caller already handles it -- so each stand-in returns exactly what its
real seam returns on an outage, and the code under test takes its genuine outage path.
No branch exists only for tests.

What this replaces matters. Before 2026-09-28 these tests made real calls on every run --
authenticated reads of the Alpaca paper account, Alpaca market data, Yahoo, and OpenD on
127.0.0.1:11111 -- and some of their assertions held only because the real services did
not recognise the made-up symbols they used. Now that assumption is written down here.

Usage, the same in both of this repo's test shapes:

    with offline_seams("yahoo", "alpaca_data"):   # names from SEAMS; none = all four
        main()

    @pytest.fixture(autouse=True)
    def _offline():
        with offline_seams("yahoo"):
            yield
"""

from __future__ import annotations

import importlib
from contextlib import contextmanager


def alpaca_data_unavailable(path, params, *, credentials, base_url=None):
    """alpaca_market_data.alpaca_data_request on a transport failure (its URLError branch)."""
    return {"ok": False, "status_code": 0, "data": {}, "reason": "URLError"}


def alpaca_rest_unavailable(method, path, *, credentials, body=None):
    """alpaca_paper_adapter.alpaca_request on a transport failure (its URLError branch)."""
    return {"ok": False, "status_code": 0, "data": {}, "reason": "URLError"}


def yahoo_unavailable(yf_ticker, start_date, end_date):
    """yahoo_finance._fetch_yfinance when Yahoo cannot be reached: it raises, and
    fetch_eod_prices catches it and moves on to its cache / fixture / YahooDataError path."""
    raise ConnectionError(f"offline: no Yahoo data for {yf_ticker}")


def opend_not_listening(host, port, *, timeout=1.5):
    """moomoo_status._port_reachable with no OpenD running."""
    return False


SEAMS = {
    "alpaca_data": ("alpaca_market_data", "alpaca_data_request", alpaca_data_unavailable),
    "alpaca_rest": ("alpaca_paper_adapter", "alpaca_request", alpaca_rest_unavailable),
    "yahoo": ("yahoo_finance", "_fetch_yfinance", yahoo_unavailable),
    "opend": ("moomoo_status", "_port_reachable", opend_not_listening),
}


@contextmanager
def offline_seams(*names: str):
    """Swap the named seams for their outage stand-ins, and restore them on exit."""
    unknown = set(names) - set(SEAMS)
    if unknown:
        raise ValueError(f"unknown seam(s) {sorted(unknown)}; known: {sorted(SEAMS)}")
    saved = []
    try:
        for name in names or tuple(SEAMS):
            module_name, attribute, stand_in = SEAMS[name]
            module = importlib.import_module(module_name)
            saved.append((module, attribute, getattr(module, attribute)))
            setattr(module, attribute, stand_in)
        yield
    finally:
        for module, attribute, original in reversed(saved):
            setattr(module, attribute, original)
