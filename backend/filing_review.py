"""A model reads a company's annual report for non-compliant business activity.

This exists because the US screen judges business activity by SEC industry
code (SIC), all or nothing. The SC SAC benchmark is a percentage: income from
non-compliant activities must be under 5% of group total income (single
benchmark, SAC resolutions of 24 Feb 2025 and 13 Nov 2025, for financial years
ending on or after 31 Dec 2025). A code cannot see that 6% of a company's
revenue is casino income; a reading of the report can.

What the model is allowed to do, and what it is not:

- It can only produce a YELLOW flag (or a note with no effect). It can never
  produce GREEN, never escalate to RED, and never change a verdict's status --
  holdings_monitor.add_review refuses a model escalation, and traffic_light
  ignores anything in a flag that looks like a colour.
- Every finding must quote the filing. A quote that does not appear in the
  document, word for word, is discarded before anything is stored. A model that
  invents evidence produces nothing.
- It runs only when the owner asks (it spends OpenRouter credit), never on a
  schedule.

A flag is keyed to the annual report it read, so it lapses when a newer report
arrives and the screen runs fresh.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser

import holdings_monitor
import sec_edgar_screen
from config import load_settings
from news_summarizer import openrouter_request

# 10% of the 5% benchmark: the same near-limit band traffic_light uses for ratios.
ACTIVITY_LIMIT_PCT = 5.0
ACTIVITY_YELLOW_FROM_PCT = 4.5

SECTION_CHARS = 40_000
SEGMENT_CHARS = 8_000
MIN_QUOTE_CHARS = 20

CATEGORIES = [
    "conventional banking / interest income",
    "conventional insurance",
    "gambling",
    "alcohol",
    "pork / non-halal food",
    "tobacco / e-cigarettes",
    "non-compliant entertainment / cinema",
    "share trading / stockbroking",
    "rental from non-compliant activities",
    "other non-compliant activity",
]

SYSTEM_PROMPT = (
    "You read US annual reports for a Shariah screen. Identify business activities "
    "that may be non-compliant under the SC Malaysia SAC methodology, in these "
    "categories: "
    + "; ".join(CATEGORIES)
    + ". For each, estimate its share of total revenue in percent if the text "
    "supports a number, else null. Every finding MUST include a quote copied "
    "exactly, word for word, from the text you were given. Do not decide whether "
    "the company is compliant. Reply with JSON only: "
    '{"findings": [{"category": str, "activity": str, '
    '"share_of_revenue_pct": number|null, "quote": str}], "summary": str}. '
    "If you find nothing, return an empty findings list."
)


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(document: str) -> str:
    extractor = _TextExtractor()
    try:
        extractor.feed(document or "")
    except Exception:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", document or "")))
    text = "".join(extractor.parts).replace("\xa0", " ")
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def _normalize(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def business_section(text: str) -> str:
    """The Item 1 Business section plus segment information, size-capped.

    The table of contents also says "Item 1. Business", so the occurrence with
    the most text before the next "Item 1A" is taken as the real section.
    """
    starts = [m.start() for m in re.finditer(r"item\s*1\.?\s*business", text, re.IGNORECASE)]
    best = ""
    for start in starts:
        end_match = re.search(r"item\s*1a\.?", text[start + 20 :], re.IGNORECASE)
        end = start + 20 + end_match.start() if end_match else start + SECTION_CHARS
        if end - start > len(best):
            best = text[start:end]
    if not best:
        best = text[:SECTION_CHARS]
    best = best[:SECTION_CHARS]

    segment = ""
    hits = [m.start() for m in re.finditer(r"segment information", text, re.IGNORECASE)]
    if hits:
        segment = text[hits[-1] : hits[-1] + SEGMENT_CHARS]
    return best + ("\n\n[Segment information]\n" + segment if segment else "")


def _parse_llm(response: dict) -> dict:
    if not isinstance(response, dict) or not response.get("ok"):
        raise ValueError(f"model request failed: {(response or {}).get('reason', 'unknown')}")
    try:
        content = response["data"]["choices"][0]["message"]["content"]
        parsed = json.loads(str(content).strip())
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ValueError("model reply was not the JSON asked for") from None
    findings = parsed.get("findings") if isinstance(parsed, dict) else None
    if not isinstance(findings, list):
        raise ValueError("model reply had no findings list")
    return {"findings": findings, "summary": str(parsed.get("summary") or "")}


def verify_findings(findings: list, document_text: str) -> tuple[list, list]:
    """Keep only findings whose quote appears verbatim in the filing."""
    haystack = _normalize(document_text)
    verified, rejected = [], []
    for item in findings:
        if not isinstance(item, dict):
            continue
        quote = str(item.get("quote") or "").strip()
        pct = item.get("share_of_revenue_pct")
        try:
            pct = None if pct is None else float(pct)
        except (TypeError, ValueError):
            pct = None
        if pct is not None and not 0 <= pct <= 100:
            pct = None
        clean = {
            "category": str(item.get("category") or "other non-compliant activity"),
            "activity": str(item.get("activity") or ""),
            "share_of_revenue_pct": pct,
            "quote": quote,
        }
        if len(quote) >= MIN_QUOTE_CHARS and _normalize(quote) in haystack:
            verified.append(clean)
        else:
            rejected.append({**clean, "rejected_because": "quote not found in the filing"})
    return verified, rejected


def decide_review(verified: list) -> dict | None:
    """What a verified reading adds: a flag, a note, or nothing.

    The benchmark is on the *total* of all non-compliant activity, so known
    shares are summed. A finding with no number cannot be shown to be under
    the benchmark, so it is a flag.
    """
    if not verified:
        return None
    unknown = [f for f in verified if f["share_of_revenue_pct"] is None]
    total = round(sum(f["share_of_revenue_pct"] or 0 for f in verified), 2)
    flagged = bool(unknown) or total >= ACTIVITY_YELLOW_FROM_PCT
    listing = "; ".join(
        f"{f['category']}: {f['activity'] or 'unspecified'}"
        + (f" (~{f['share_of_revenue_pct']:g}%)" if f["share_of_revenue_pct"] is not None else "")
        for f in verified
    )
    if flagged:
        basis = (
            "share not stated in the report"
            if unknown
            else f"~{total:g}% of revenue, yellow from {ACTIVITY_YELLOW_FROM_PCT:g}% "
            f"(benchmark {ACTIVITY_LIMIT_PCT:g}%)"
        )
        detail = f"AI reading of the annual report found possible non-compliant activity ({basis}): {listing}"
        kind = "flag"
    else:
        detail = (
            f"AI reading found minor activity totalling ~{total:g}% of revenue, under "
            f"{ACTIVITY_YELLOW_FROM_PCT:g}%: {listing}"
        )
        kind = "note"
    return {
        "kind": kind,
        "code": "mixed_business_activity" if kind == "flag" else "activity_note",
        "detail": detail,
        "quote": verified[0]["quote"],
        "activity_pct": None if unknown else total,
    }


def review_symbol(
    connection,
    symbol: str,
    *,
    locate=None,
    fetch_text=None,
    llm=None,
    settings=None,
) -> dict:
    """Read the latest annual report of one US company and record what it adds.

    Seams (`locate`, `fetch_text`, `llm`) default to the real SEC and OpenRouter
    calls; tests swap all three. A failure anywhere records nothing -- which is
    safe, because a review can only ever add caution.
    """
    settings = settings or load_settings()
    clean_symbol = holdings_monitor.normalize_symbol(symbol)
    if clean_symbol.isdigit():
        return {
            "status": "NOT_APPLICABLE",
            "symbol": clean_symbol,
            "reason": "Bursa securities are settled by the SC list; there is no report to read",
        }
    if llm is None:
        if not settings.openrouter_api_key:
            return {
                "status": "NOT_CONFIGURED",
                "symbol": clean_symbol,
                "reason": "OPENROUTER_API_KEY is not set",
            }

        def llm(payload):
            return openrouter_request(payload, api_key=settings.openrouter_api_key)

    filing = (locate or sec_edgar_screen.latest_annual_filing)(clean_symbol)
    if filing.get("status") != "OK":
        return {"status": filing.get("status", "ERROR"), "symbol": clean_symbol, **filing}

    fetched = (fetch_text or sec_edgar_screen.sec_text_request)(filing["url"])
    if not fetched.get("ok"):
        return {
            "status": "ERROR",
            "symbol": clean_symbol,
            "reason": f"filing_unavailable:{fetched.get('reason')}",
        }
    document_text = html_to_text(fetched.get("text") or "")
    section = business_section(document_text)

    payload = {
        "model": settings.openrouter_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Company: {clean_symbol}\n\n{section}"},
        ],
        "max_tokens": 1200,
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
    }
    try:
        reply = _parse_llm(llm(payload))
    except ValueError as exc:
        return {"status": "ERROR", "symbol": clean_symbol, "reason": str(exc)}

    verified, rejected = verify_findings(reply["findings"], document_text)
    decision = decide_review(verified)
    stored = None
    if decision is not None:
        stored = holdings_monitor.add_review(
            connection,
            symbol=clean_symbol,
            report_date=filing.get("report_date"),
            source="model",
            **decision,
        )
    return {
        "status": "OK",
        "symbol": clean_symbol,
        "filing": {k: filing.get(k) for k in ("form", "report_date", "url", "accession")},
        "summary": reply["summary"],
        "verified_findings": verified,
        "rejected_findings": rejected,
        "recorded": None if decision is None else {**decision, "id": stored["id"]},
        "effect": (
            "YELLOW until a newer annual report"
            if decision and decision["kind"] == "flag"
            else "no change to the colour"
        ),
    }
