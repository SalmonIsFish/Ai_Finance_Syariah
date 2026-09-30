"""Stand SEC EDGAR in as answering: the named symbols screen COMPLIANT.

For tests that pass *through* a US Shariah screen on the way to something else --
approval, option structure, portfolio risk -- and need the underlying to clear it.
The real screen still runs. Only ``sec_edgar_screen.sec_request`` is swapped, with
SEC-shaped payloads for a benign manufacturer (SIC 3571) whose debt and cash ratios
both sit well inside the limits, so a COMPLIANT verdict is computed, not asserted.

Why this exists (2026-09-30): test_alpaca_shariah_wiring, test_local_api_smoke and
test_portfolio_risk_limits screened AAPL/MSFT against live SEC. The census passed only
while ``backend/sec_edgar_cache`` held a fresh entry, which answers without a socket,
and failed as NETWORK once it expired. They also appended every verdict to the real
``paper_trading.db`` screen log, so ``_record_screen`` is swapped here too.

A symbol not named is answered with a 404 from the ticker map, which the screen
treats as ERROR and fails closed -- never silently COMPLIANT.

    with sec_screens_compliant("AAPL", "MSFT"):
        main()
"""

from __future__ import annotations

from contextlib import contextmanager

import sec_edgar_screen

_BALANCE_SHEET_DATE = "2025-09-27"


def _facts() -> dict:
    def usd(value: float) -> dict:
        return {
            "units": {
                "USD": [
                    {
                        "end": _BALANCE_SHEET_DATE,
                        "val": value,
                        "form": "10-K",
                        "filed": "2026-02-01",
                    }
                ]
            }
        }

    # Debt 20% and cash 10% of assets: comfortably inside both SC limits.
    return {
        "facts": {
            "us-gaap": {
                "Assets": usd(1_000.0),
                "LongTermDebtNoncurrent": usd(150.0),
                "LongTermDebtCurrent": usd(50.0),
                "CashAndCashEquivalentsAtCarryingValue": usd(100.0),
            }
        }
    }


def compliant_responses(*symbols: str) -> dict:
    """The {url: payload} map a COMPLIANT screen of each symbol reads."""
    ticker_map = {}
    responses = {}
    for index, symbol in enumerate(symbols):
        cik = 900_000 + index
        ticker_map[str(index)] = {"cik_str": cik, "ticker": symbol.upper(), "title": symbol}
        responses[sec_edgar_screen.SEC_SUBMISSIONS_URL.format(cik=cik)] = {
            "name": f"{symbol.upper()} FIXTURE",
            "sic": 3571,
            "sicDescription": "Electronic Computers",
            "exchanges": ["Nasdaq"],
        }
        responses[sec_edgar_screen.SEC_FACTS_URL.format(cik=cik)] = _facts()
    responses[sec_edgar_screen.SEC_TICKERS_URL] = ticker_map
    return responses


@contextmanager
def sec_screens_compliant(*symbols: str):
    """Swap the SEC seam and the verdict log; yield the verdicts that were recorded."""
    responses = compliant_responses(*symbols)
    recorded: list[dict] = []

    def fake_request(url):
        if url in responses:
            return {"ok": True, "status_code": 200, "data": responses[url]}
        return {"ok": False, "status_code": 404, "data": {}, "reason": "http_404"}

    real_request = sec_edgar_screen.sec_request
    real_record = sec_edgar_screen._record_screen
    sec_edgar_screen.reset_ticker_cache()
    sec_edgar_screen.sec_request = fake_request
    sec_edgar_screen._record_screen = recorded.append
    try:
        yield recorded
    finally:
        sec_edgar_screen.sec_request = real_request
        sec_edgar_screen._record_screen = real_record
        sec_edgar_screen.reset_ticker_cache()
