"""Shariah screening for US equities from free SEC EDGAR filings.

Drop-in replacement for ``zoya_compliance.check_us_symbol``: same call shape, same
return shape, so ``agents/shariah_agent.py`` can route to either.

Methodology implemented is the **Securities Commission Malaysia / Shariah Advisory
Council** two-tier screen documented in ``docs/shariah-policy/screening-criteria-breakdown.md``:

1. Business-activity exclusions are automatic disqualifiers regardless of financials.
2. Otherwise interest-bearing debt / total assets and conventional cash / total assets
   must each be **strictly under 33%**.

Ratios are computed from the latest **annual** filing (10-K / 20-F / 40-F), because the
SC review cycle is explicitly "based on the company's latest audited annual financial
statements". Every component of a ratio is read at that one balance-sheet date, so a
ratio is never assembled from figures belonging to different periods.

Scope and honesty limits -- read before relying on a verdict:

- **This is not a certified screening service.** It is a labelled, scoped implementation
  of a published methodology, in the same spirit as ``docs/shariah-policy/README.md``.
  A qualified scholar should review the thresholds and the exclusion table.
- **Business activity is approximated by SIC code.** SEC's SIC classification is a
  filer-selected primary-industry label, not a revenue breakdown. A conglomerate whose
  primary SIC is benign can still earn non-compliant revenue, and this screen will not
  see it. SIC is a reasonable first pass, not a substitute for a revenue-source review.
  Where a SIC block mixes compliant and non-compliant operators, the block is excluded
  rather than admitted, so the error lands on the rejecting side.
- **XBRL cannot distinguish Islamic from conventional instruments.** The SC rule counts
  only conventional cash and interest-bearing debt, excluding Islamic accounts and sukuk.
  Filings do not tag that distinction, so *all* cash and *all* interest-bearing debt are
  counted. That overstates both ratios, which errs toward rejection.
- **The SC qualitative limb is not implemented.** Public perception / image of the
  company's activities is a human judgement, and is left to the human approver.
- Missing data, an unmapped ticker, an untaggable balance sheet, or a fetch error returns
  UNKNOWN/ERROR. A guessed COMPLIANT is never returned.

SEC requires a descriptive User-Agent on every request and blocks bare ones. Set
``SEC_EDGAR_USER_AGENT`` in backend/.env; there is a working default, but SEC asks that
it identify the requester.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from numeric_guards import first_non_finite, is_finite_number
from sec_edgar_cache import cached_fetch
from shariah_screen_store import record_screen_to_default_db


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

DEFAULT_USER_AGENT = "AmanahTrader/1.0 (Shariah screening research; contact via repository)"
REQUEST_TIMEOUT_SECONDS = 25

# SC methodology: each ratio must be *less than* 33%.
MAX_RATIO_PCT = 33.0

# Methodologies. SC SAC is the default and the only one the order gate uses.
# AAOIFI (Shariah Standard 21) is offered to the monitor as a comparison view:
# the same Tier-1 exclusions, but debt and conventional cash are measured
# against *market capitalisation* and must each be under 30%. AAOIFI practice
# often averages market cap over 24-36 months; this uses the current price
# times the shares outstanding on the latest cover page, which is labelled on
# every verdict as an approximation.
METHODOLOGY_SC = "SC_MY_SAC"
METHODOLOGY_AAOIFI = "AAOIFI"
METHODOLOGIES = (METHODOLOGY_SC, METHODOLOGY_AAOIFI)
AAOIFI_MAX_RATIO_PCT = 30.0

# Forms whose facts we read at all. Amendments are excluded: an "/A" restates a period
# we already have from the original and would compete with it on filing date.
ANNUAL_FORMS = {"10-K", "20-F", "40-F"}
REPORTING_FORMS = ANNUAL_FORMS | {"10-Q"}

# --- Business-activity exclusions, keyed by SEC SIC code -------------------------------
# Each entry maps an inclusive SIC range to the exclusion category from
# screening-criteria-breakdown.md section 1. Ranges are used because SEC assigns
# four-digit codes within well-defined industry blocks.
EXCLUDED_SIC_RANGES = [
    ((6020, 6036), "conventional_banking"),
    ((6099, 6099), "conventional_banking"),
    ((6111, 6111), "conventional_banking"),
    ((6712, 6712), "conventional_banking"),
    ((6140, 6163), "conventional_credit_or_leasing"),
    ((6172, 6199), "conventional_credit_or_leasing"),
    ((6311, 6411), "conventional_insurance"),
    ((6200, 6211), "stockbroking_or_share_trading"),
    ((6221, 6221), "stockbroking_or_share_trading"),
    ((6282, 6282), "stockbroking_or_share_trading"),
    ((6726, 6726), "stockbroking_or_share_trading"),
    ((6770, 6770), "stockbroking_or_share_trading"),
    ((213, 213), "pork_related"),
    ((2011, 2015), "non_halal_food"),
    ((2082, 2085), "alcohol"),
    ((5182, 5182), "alcohol"),
    ((5813, 5813), "alcohol"),
    ((5921, 5921), "alcohol"),
    ((2100, 2199), "tobacco"),
    ((7800, 7841), "cinema_or_entertainment"),
    ((7990, 7999), "gambling_or_entertainment"),
    ((7011, 7011), "hospitality_mixed_activity"),
    ((7377, 7377), "conventional_leasing"),
]

EXCLUSION_LABELS = {
    "conventional_banking": "conventional banking / interest-based lending",
    "conventional_credit_or_leasing": "conventional credit, mortgage or leasing finance",
    "conventional_insurance": "conventional insurance",
    "stockbroking_or_share_trading": "stockbroking / share trading",
    "pork_related": "pork and pork-related products",
    "non_halal_food": "meat processing without halal certification",
    "alcohol": "alcohol / liquor-related activities",
    "tobacco": "tobacco and cigarette products",
    "cinema_or_entertainment": "cinema / Shariah non-compliant entertainment",
    "gambling_or_entertainment": "gambling / Shariah non-compliant entertainment",
    # Hotels are not disqualified as such. SEC files casino resorts, and hotels whose
    # revenue is materially alcohol and non-halal F&B, under the same code, and nothing
    # in the filing separates them -- so the block is rejected pending a revenue review.
    "hospitality_mixed_activity": (
        "hotels/resorts -- SIC block mixes casino, alcohol and non-halal F&B revenue; "
        "needs a revenue-source review"
    ),
    "conventional_leasing": "conventional leasing (non-ijarah)",
}

# XBRL concept fallbacks. Filers tag the same economic item under different names, so
# each inner list holds alternatives for the *same* item and contributes at most one
# value; the outer list is what gets summed.
TOTAL_ASSETS_CONCEPTS = ["Assets"]

INTEREST_BEARING_DEBT_COMPONENTS = [
    # Long-term debt, non-current portion. "LongTermDebt" is the whole balance including
    # current maturities, so it is the last resort: when it fires it can overlap the
    # current-maturities group below, overstating debt, which is the safe direction.
    ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt"],
    ["LongTermDebtCurrent", "LongTermDebtCurrentMaturities"],
    ["ShortTermBorrowings", "OtherShortTermBorrowings", "CommercialPaper"],
    ["NotesPayableCurrent"],
]

CONVENTIONAL_CASH_COMPONENTS = [
    [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashAndDueFromBanks",
    ],
    [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    ],
]

_TICKER_MAP_CACHE = None


class SecEdgarError(RuntimeError):
    """A SEC EDGAR request failed in a way the caller must fail closed on."""


def sec_request(url: str) -> dict:
    """Single seam for every SEC call, so tests never touch the network.

    Repeat calls for the same URL are served from a temporary on-disk cache
    (``sec_edgar_cache``, a shim -- see that module's docstring), which also
    keeps live fetches under SEC's ~10 requests/second guidance. Tests that swap
    this whole function out, as every existing suite does, bypass both.
    """
    return cached_fetch(url, lambda: _sec_fetch(url))


def _sec_fetch(url: str) -> dict:
    """The actual HTTP call, separated so the cache can decide whether to make it."""
    headers = {
        "User-Agent": os.getenv("SEC_EDGAR_USER_AGENT") or DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    try:
        with urlopen(Request(url, headers=headers), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return {"ok": True, "status_code": response.status, "data": _decode(response.read())}
    except HTTPError as exc:
        return {"ok": False, "status_code": exc.code, "data": {}, "reason": f"http_{exc.code}"}
    except URLError as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}


def _decode(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def reset_ticker_cache() -> None:
    """Drop the in-process ticker->CIK map (tests and long-running processes)."""
    global _TICKER_MAP_CACHE
    _TICKER_MAP_CACHE = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _screen_us_symbol(symbol: str, *, methodology: str = METHODOLOGY_SC, price_lookup=None) -> dict:
    """Screen one US-listed symbol. Mirrors zoya_compliance.check_us_symbol.

    The verdict-producing half, split out from check_us_symbol so that every
    one of its six exits is logged by one call site instead of six.
    """
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return {"status": "UNKNOWN", "symbol": normalized, "reason": "symbol_required"}
    if methodology not in METHODOLOGIES:
        return {"status": "UNKNOWN", "symbol": normalized, "reason": "unknown_methodology"}

    try:
        cik = resolve_cik(normalized)
    except SecEdgarError as exc:
        return {"status": "ERROR", "symbol": normalized, "reason": str(exc)}
    if cik is None:
        return {
            "status": "UNKNOWN",
            "symbol": normalized,
            "reason": "ticker_not_found_in_sec_registry",
        }

    try:
        profile = fetch_company_profile(cik)
    except SecEdgarError as exc:
        return {"status": "ERROR", "symbol": normalized, "reason": str(exc)}

    base = {
        "symbol": normalized,
        "exchange": profile.get("exchange"),
        "provider": "SEC_EDGAR",
        "methodology": methodology,
        "company": profile.get("name"),
        # Carried on every verdict, and therefore persisted into shariah_screens,
        # purely so the risk overlay can group held positions by industry without
        # a second data source or a second SEC fetch. The screen itself already
        # reads this to decide business-activity exclusion; it was simply being
        # thrown away afterwards. Reporting only -- nothing here changes a verdict.
        "sic": profile.get("sic"),
        "sic_description": profile.get("sic_description"),
    }

    # Tier 1: business activity. An excluded activity disqualifies outright, and no
    # ratio is computed -- a conventional bank passes the ratio tests on its own
    # balance sheet, so running them first would give the wrong answer.
    exclusion = business_activity_exclusion(profile.get("sic"))
    if exclusion is not None:
        return {
            **base,
            "status": "NON_COMPLIANT",
            "report_date": None,
            "screen": "business_activity",
            # Machine-readable twin of the label in `reason`, so traffic_light can tell
            # a placeholder rejection (a mixed SIC block) from an established one
            # without parsing prose. Reporting only -- the status above is unchanged.
            "exclusion_category": exclusion,
            "reason": (
                f"excluded business activity: {EXCLUSION_LABELS[exclusion]} "
                f"(SIC {profile.get('sic')} {profile.get('sic_description') or ''})".strip()
            ),
        }

    # Tier 2: financial ratios.
    try:
        facts = fetch_company_facts(cik)
    except SecEdgarError as exc:
        return {**base, "status": "ERROR", "reason": str(exc)}

    ratios = compute_ratios(facts)
    if isinstance(ratios, str):
        return {**base, "status": "UNKNOWN", "report_date": None, "reason": ratios}
    if methodology == METHODOLOGY_AAOIFI:
        try:
            dei = fetch_company_dei(cik)
        except SecEdgarError as exc:
            return {**base, "status": "ERROR", "reason": str(exc)}
        price = price_lookup(normalized) if price_lookup else None
        ratios = aaoifi_ratios(ratios, dei, price)
        if isinstance(ratios, str):
            return {**base, "status": "UNKNOWN", "report_date": None, "reason": ratios}
    limit_pct = ratios["limit_pct"]
    denominator = "market capitalisation" if methodology == METHODOLOGY_AAOIFI else "total assets"

    failures = []
    if ratios["debt_ratio_pct"] >= limit_pct:
        failures.append(
            f"interest-bearing debt / {denominator} {ratios['debt_ratio_pct']:.1f}% >= {limit_pct:.0f}%"
        )
    if ratios["cash_ratio_pct"] >= limit_pct:
        failures.append(
            f"conventional cash / {denominator} {ratios['cash_ratio_pct']:.1f}% >= {limit_pct:.0f}%"
        )

    return {
        **base,
        "status": "NON_COMPLIANT" if failures else "COMPLIANT",
        "report_date": ratios["report_date"],
        "screen": "financial_ratios",
        "ratios": ratios,
        "reason": "; ".join(failures)
        if failures
        else (
            f"debt {ratios['debt_ratio_pct']:.1f}% and cash {ratios['cash_ratio_pct']:.1f}% "
            f"of {denominator}, both under {limit_pct:.0f}%"
        ),
    }


def _record_screen(verdict: dict) -> None:
    """Append the verdict to the audit log. Replaceable seam -- tests swap this
    so the suite never writes to the real paper_trading.db."""
    record_screen_to_default_db(verdict)


def check_us_symbol(symbol: str, *, methodology: str = METHODOLOGY_SC, price_lookup=None) -> dict:
    """Screen one US-listed symbol, and record the verdict in the audit log.

    The log is observability, not a gate: a failed write must never change a
    verdict. Fail-closed is the right instinct everywhere a decision is made in
    this repo, and exactly the wrong one here -- letting a locked SQLite file
    turn a COMPLIANT company into an ERROR would be a screening bug caused by
    bookkeeping. So the write is best-effort and the verdict is returned either
    way.
    """
    verdict = _screen_us_symbol(symbol, methodology=methodology, price_lookup=price_lookup)
    try:
        _record_screen(verdict)
    except Exception:
        pass
    return verdict


# ---------------------------------------------------------------------------
# EDGAR lookups
# ---------------------------------------------------------------------------


def resolve_cik(symbol: str):
    global _TICKER_MAP_CACHE
    if _TICKER_MAP_CACHE is None:
        response = sec_request(SEC_TICKERS_URL)
        if not response.get("ok"):
            raise SecEdgarError(f"sec_ticker_map_unavailable:{response.get('reason')}")
        data = response.get("data") or {}
        mapping = {}
        for row in data.values() if isinstance(data, dict) else []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            cik = row.get("cik_str")
            if ticker and isinstance(cik, int):
                mapping[ticker] = cik
        if not mapping:
            raise SecEdgarError("sec_ticker_map_empty")
        _TICKER_MAP_CACHE = mapping
    return _TICKER_MAP_CACHE.get(symbol)


def fetch_company_profile(cik: int) -> dict:
    response = sec_request(SEC_SUBMISSIONS_URL.format(cik=cik))
    if not response.get("ok"):
        raise SecEdgarError(f"sec_submissions_unavailable:{response.get('reason')}")
    data = response.get("data") or {}
    exchanges = data.get("exchanges")
    exchange = exchanges[0] if isinstance(exchanges, list) and exchanges else None
    return {
        "cik": cik,
        "name": data.get("name"),
        "sic": _int_or_none(data.get("sic")),
        "sic_description": data.get("sicDescription"),
        "exchange": exchange,
    }


def fetch_company_dei(cik: int) -> dict:
    """The cover-page (dei) facts from the same companyfacts document."""
    response = sec_request(SEC_FACTS_URL.format(cik=cik))
    if not response.get("ok"):
        raise SecEdgarError(f"sec_company_facts_unavailable:{response.get('reason')}")
    data = response.get("data") or {}
    return ((data.get("facts") or {}).get("dei")) or {}


def latest_shares_outstanding(dei: dict):
    """Latest EntityCommonStockSharesOutstanding, summed across rows of one filing.

    companyfacts omits dimensional facts, so a multi-class issuer may report no
    undimensioned figure at all; that returns None and the screen is UNKNOWN.
    """
    node = dei.get("EntityCommonStockSharesOutstanding")
    rows = [
        row
        for row in ((node or {}).get("units") or {}).get("shares") or []
        if isinstance(row, dict) and row.get("end") and is_finite_number(row.get("val"))
    ]
    if not rows:
        return None
    latest_end = max(str(row["end"]) for row in rows)
    same_end = [row for row in rows if str(row["end"]) == latest_end]
    latest_filed = max(str(row.get("filed") or "") for row in same_end)
    total = sum(
        float(row["val"]) for row in same_end if str(row.get("filed") or "") == latest_filed
    )
    return {"shares": total, "as_of": latest_end} if total > 0 else None


def aaoifi_ratios(sc_ratios: dict, dei: dict, price):
    """Re-base the SC ratio inputs on market capitalisation (AAOIFI Standard 21)."""
    shares = latest_shares_outstanding(dei)
    if shares is None:
        return "shares_outstanding_not_reported"
    if not is_finite_number(price) or price <= 0:
        return "price_unavailable_for_market_cap"
    market_cap = shares["shares"] * float(price)
    if not is_finite_number(market_cap) or market_cap <= 0:
        return "market_cap_not_a_finite_number"
    debt_pct = round(sc_ratios["interest_bearing_debt"] / market_cap * 100, 4)
    cash_pct = round(sc_ratios["conventional_cash"] / market_cap * 100, 4)
    unusable = first_non_finite(debt_ratio_pct=debt_pct, cash_ratio_pct=cash_pct)
    if unusable:
        return f"{unusable}_not_a_finite_number"
    return {
        **sc_ratios,
        "debt_ratio_pct": debt_pct,
        "cash_ratio_pct": cash_pct,
        "limit_pct": AAOIFI_MAX_RATIO_PCT,
        "denominator": "market_cap",
        "market_cap": round(market_cap, 2),
        "shares_outstanding": shares["shares"],
        "shares_as_of": shares["as_of"],
        "price": float(price),
        "approximation": "spot market cap (price x latest cover-page shares), not a 24-36 month average",
    }


def fetch_company_facts(cik: int) -> dict:
    response = sec_request(SEC_FACTS_URL.format(cik=cik))
    if not response.get("ok"):
        raise SecEdgarError(f"sec_company_facts_unavailable:{response.get('reason')}")
    data = response.get("data") or {}
    return ((data.get("facts") or {}).get("us-gaap")) or {}


SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
# An annual report's HTML runs to several MB; nothing past this is read.
MAX_FILING_BYTES = 12_000_000


def sec_text_request(url: str) -> dict:
    """Seam for fetching a filing document as text (HTML), not JSON.

    Used only by filing_review, on the owner's request, never by a screen. Same
    contract as sec_request: never raises; {"ok", "status_code", "text", "reason"}.
    Tests swap this whole function.
    """
    headers = {"User-Agent": os.getenv("SEC_EDGAR_USER_AGENT") or DEFAULT_USER_AGENT}
    try:
        with urlopen(Request(url, headers=headers), timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_FILING_BYTES)
            return {
                "ok": True,
                "status_code": response.status,
                "text": raw.decode("utf-8", "replace"),
            }
    except HTTPError as exc:
        return {"ok": False, "status_code": exc.code, "text": "", "reason": f"http_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "text": "", "reason": type(exc).__name__}


def latest_annual_filing(symbol: str) -> dict:
    """Locate the newest 10-K / 20-F / 40-F for a symbol: its URL and report date.

    Reads the same submissions document the screen reads (so a warm cache serves
    it). Returns {"status": "OK", ...} or {"status": "UNKNOWN"/"ERROR", "reason"}.
    """
    normalized = str(symbol or "").strip().upper()
    try:
        cik = resolve_cik(normalized)
    except SecEdgarError as exc:
        return {"status": "ERROR", "symbol": normalized, "reason": str(exc)}
    if cik is None:
        return {
            "status": "UNKNOWN",
            "symbol": normalized,
            "reason": "ticker_not_found_in_sec_registry",
        }
    response = sec_request(SEC_SUBMISSIONS_URL.format(cik=cik))
    if not response.get("ok"):
        return {
            "status": "ERROR",
            "symbol": normalized,
            "reason": f"sec_submissions_unavailable:{response.get('reason')}",
        }
    recent = ((response.get("data") or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    for index, form in enumerate(forms):
        if form not in ANNUAL_FORMS:
            continue
        try:
            accession = recent["accessionNumber"][index]
            document = recent["primaryDocument"][index]
        except (KeyError, IndexError, TypeError):
            continue
        if not accession or not document:
            continue
        report_dates = recent.get("reportDate") or []
        return {
            "status": "OK",
            "symbol": normalized,
            "cik": cik,
            "form": form,
            "accession": accession,
            "report_date": report_dates[index] if index < len(report_dates) else None,
            "url": SEC_ARCHIVE_URL.format(
                cik=cik, accession=str(accession).replace("-", ""), document=document
            ),
        }
    return {
        "status": "UNKNOWN",
        "symbol": normalized,
        "reason": "no_annual_report_in_recent_filings",
    }


# ---------------------------------------------------------------------------
# Screening logic
# ---------------------------------------------------------------------------


def business_activity_exclusion(sic):
    """Return the exclusion category for a SIC code, or None if not excluded."""
    code = _int_or_none(sic)
    if code is None:
        return None
    for (low, high), category in EXCLUDED_SIC_RANGES:
        if low <= code <= high:
            return category
    return None


def compute_ratios(gaap: dict):
    """Debt and cash as a percentage of total assets, at one annual balance-sheet date.

    Returns the ratio dict, or a string reason the caller turns into UNKNOWN. Nothing
    here guesses: an anchor date that cannot be established, or a balance sheet whose
    cash we cannot find a tag for, fails closed rather than defaulting to zero.
    """
    anchor = latest_annual_fact(gaap, TOTAL_ASSETS_CONCEPTS)
    if anchor is None:
        # A filer that has only ever submitted 10-Qs -- typically a newly listed or
        # newly reorganised entity -- is a different situation from one we simply
        # cannot read, and a human triaging an UNKNOWN needs to be able to tell them
        # apart. Neither is screened: the SC review runs off audited annual
        # statements, so quarterly figures are not a substitute for them.
        if any(usd_rows(gaap, concept) for concept in TOTAL_ASSETS_CONCEPTS):
            return "no_annual_report_filed_yet_only_quarterly_data_available"
        return "total_assets_not_reported_in_any_filing"
    total_assets = float(anchor["value"])
    # `nan <= 0` and `inf <= 0` are both False, so this guard alone let a non-finite
    # balance sheet through -- and both ratios then compared False against the limit,
    # producing a silent COMPLIANT. Checked explicitly, and first.
    if not is_finite_number(total_assets):
        return "total_assets_not_a_finite_number"
    if total_assets <= 0:
        return "total_assets_not_positive"
    period_end = anchor["end"]

    debt, debt_concepts = sum_components(gaap, INTEREST_BEARING_DEBT_COMPONENTS, period_end)
    cash, cash_concepts = sum_components(gaap, CONVENTIONAL_CASH_COMPONENTS, period_end)

    # A filed balance sheet always carries a cash line. Finding none means this filer
    # tags cash under a concept we do not recognise, and treating that as zero cash
    # would manufacture a passing ratio out of missing data.
    if not cash_concepts:
        return "no_recognised_cash_concept_in_filing"

    debt_ratio_pct = round(debt / total_assets * 100, 4)
    cash_ratio_pct = round(cash / total_assets * 100, 4)
    # Belt to the braces above. A ratio that is not a finite number cannot be compared to
    # the limit -- every comparison would be False, which reads as "under the limit".
    unusable = first_non_finite(debt_ratio_pct=debt_ratio_pct, cash_ratio_pct=cash_ratio_pct)
    if unusable:
        return f"{unusable}_not_a_finite_number"

    return {
        "report_date": period_end,
        "form": anchor.get("form"),
        "total_assets": total_assets,
        "interest_bearing_debt": debt,
        "conventional_cash": cash,
        "debt_ratio_pct": debt_ratio_pct,
        "cash_ratio_pct": cash_ratio_pct,
        "limit_pct": MAX_RATIO_PCT,
        "debt_concepts_used": debt_concepts,
        "cash_concepts_used": cash_concepts,
    }


def sum_components(gaap: dict, components, period_end: str):
    """Sum one value per component group, taking the first alternative that reports.

    A group is a set of alternative tags for the *same* economic item, so only one is
    counted per group. A group with no tag at this date contributes 0 and is simply
    absent from the returned concept list -- a filer with no such balance does not tag
    it. That list is returned so a human can see exactly what a ratio was built from.
    """
    total = 0.0
    used = []
    for group in components:
        for concept in group:
            fact = fact_at_period(gaap, concept, period_end)
            if fact is not None:
                total += float(fact["value"])
                used.append(concept)
                break
    return total, used


def latest_annual_fact(gaap: dict, concepts):
    """Most recent value reported in an annual filing, across the given concepts."""
    best = None
    for concept in concepts:
        for row in usd_rows(gaap, concept):
            if row["form"] not in ANNUAL_FORMS:
                continue
            if best is None or (row["end"], row["filed"]) > (best["end"], best["filed"]):
                best = row
    return best


def fact_at_period(gaap: dict, concept: str, period_end: str):
    """Value tagged for exactly this balance-sheet date, latest filing wins.

    Only an exact date match counts. Falling back to a nearby period would mix one
    balance sheet across reporting dates, which is what compute_ratios promises not to
    do; a later 10-Q restating the same date is fine, and is preferred.
    """
    best = None
    for row in usd_rows(gaap, concept):
        if row["end"] != period_end:
            continue
        if best is None or row["filed"] > best["filed"]:
            best = row
    return best


def usd_rows(gaap: dict, concept: str):
    node = gaap.get(concept)
    if not isinstance(node, dict):
        return []
    rows = []
    for row in (node.get("units") or {}).get("USD") or []:
        if not isinstance(row, dict):
            continue
        if row.get("form") not in REPORTING_FORMS:
            continue
        end = row.get("end")
        value = row.get("val")
        # `value is None` misses NaN, and json.loads parses a bare NaN literal natively.
        # A non-finite fact must never enter the set a ratio is computed from.
        if not end or not is_finite_number(value):
            continue
        rows.append(
            {
                "end": str(end),
                "value": value,
                "filed": str(row.get("filed") or ""),
                "form": row.get("form"),
            }
        )
    return rows


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
