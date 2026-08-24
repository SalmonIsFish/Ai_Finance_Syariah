"""Plain-language AI summaries for the News panel, kept out of the decision path.

CLAUDE.md: "No LLM in the decision path -- a language model may explain a decision
but must never make, approve, or bypass one." This module is the News-panel
sibling of `shariah_explain.py`: that one explains a verdict `sec_edgar_screen`
already made deterministically, and this one explains a news article and, when a
verdict exists, *restates* that same already-recorded verdict as background.

Concretely, the model here:

  - never screens anything. The Shariah status it repeats is read out of the
    `shariah_screens` table (or, for a symbol never screened, from one
    `shariah_explain.explain_symbol` call) and passed *into* the prompt. The
    model is told it, it does not decide it.
  - never advises. `SYSTEM_PROMPT` forbids the advisory vocabulary, and
    `_FORBIDDEN_PATTERN` independently drops any output that used it anyway. A
    prompt is a request; the filter is the enforcement, so the filter is what the
    tests mutation-check.
  - never gates. Nothing here feeds an order, an approval, or a risk check. Its
    entire output is one display string on a news card.

Every failure -- no API key, a dead endpoint, a malformed response, a filtered
output, a locked database -- returns None and leaves the article exactly as
Alpaca returned it. The News panel degrades to plain headlines rather than
breaking, which is the correct direction for a cosmetic layer sitting on top of
a compliance product.
"""

import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import load_settings
from shariah_explain import explain_symbol
from shariah_screen_store import latest_shariah_screen

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Well under the dashboard's own patience. A slow model must not hold the News
# panel open; the article is already renderable without it.
REQUEST_TIMEOUT_SECONDS = 20

SYSTEM_PROMPT = (
    "You summarize financial news articles for a Shariah-compliant paper-trading "
    "dashboard. You are a NEUTRAL EXPLAINER, not an analyst and not an adviser.\n"
    "\n"
    "Write 2-3 plain sentences saying what the article reports and what it means in "
    "ordinary language. Plain text only -- no markdown, no bullet points, no headings.\n"
    "\n"
    "If the user message includes a Shariah verdict, that verdict has ALREADY been "
    "decided by this application's own deterministic screen. Restate it in one short "
    "sentence as settled background, attributed to 'this app's existing screen'. Never "
    "recompute it, never question it, never contradict it, never add conditions to it, "
    "and never offer a compliance opinion of your own. If no verdict is included, simply "
    "summarize the news and say nothing at all about Shariah status.\n"
    "\n"
    "The verdict belongs to ONE ticker, named in the user message. When you restate it "
    "you MUST name that exact ticker symbol, and you must repeat its status exactly as "
    "given. An article often mentions several companies; the verdict applies only to the "
    "ticker named, never to whichever company the article is mostly about. Never state or "
    "imply a Shariah status for any other company, and never flip a NON_COMPLIANT verdict "
    "to compliant or a COMPLIANT verdict to non-compliant.\n"
    "\n"
    "You must NEVER use these words or any form of them: buy, sell, invest, investing, "
    "investment, recommend, recommendation, should, opportunity, undervalued, overvalued, "
    "target price.\n"
    "\n"
    "You must NEVER imply that the news is good or bad for the share price, that the "
    "stock will rise or fall, that a company is worth more or less than its price, or "
    "that now is a good or bad time to act. Do not describe anything as bullish, bearish, "
    "positive or negative for the stock. Report what happened; do not evaluate what it is "
    "worth. If you cannot describe the article without breaking these rules, describe only "
    "the plain facts it states."
)

# The enforcement half. The prompt above asks; this drops the output when the model
# asks anyway -- models drift, especially on finance headlines, and a request is not
# a control. Deliberately biased toward over-filtering: a false positive costs one
# AI block on one card (Alpaca's own summary still renders), while a false negative
# puts investment advice on the screen of a compliance product. That trade is not close.
_FORBIDDEN_TERMS = [
    # Explicitly forbidden by the framing constraint.
    r"buy(s|ing)?",
    r"bought",
    r"sell(s|ing)?",
    r"sold",
    r"invest(s|ing|ment|ments)?",
    r"recommend(s|ed|ing|ation|ations)?",
    r"should",
    r"opportunit(y|ies)",
    r"undervalued",
    r"overvalued",
    r"target price",
    r"price target",
    # "good/bad for the price, good/bad time to act" -- the same rule, said in the
    # vocabulary a model actually reaches for.
    r"bullish",
    r"bearish",
    r"outperform(s|ed|ing)?",
    r"underperform(s|ed|ing)?",
    r"upside",
    r"downside",
    r"good time",
    r"bad time",
    r"upgrade(s|d)?",
    r"downgrade(s|d)?",
    r"overweight",
    r"underweight",
]
_FORBIDDEN_PATTERN = re.compile(r"\b(" + "|".join(_FORBIDDEN_TERMS) + r")\b", re.IGNORECASE)

# The second enforcement half, and the one a word filter cannot do. A live run on
# 2026-08-24 handed the model "verdict ALREADY DECIDED for JPM: NON_COMPLIANT" on a
# multi-symbol article whose subject was Tesla, and got back "this app's existing
# screen found Tesla compliant" -- a fabricated verdict, for a company never
# screened, with the polarity inverted, attributed to this app. No advisory word
# appears in that sentence, so _FORBIDDEN_PATTERN cannot see it.
#
# So the restatement is checked structurally instead: if a verdict was supplied, the
# summary must name that exact ticker and must carry the matching compliance
# polarity. Anything else is dropped.
# The underscore form matters more than it looks: NON_COMPLIANT is the exact
# status token _build_user_message hands the model, so it is the spelling the
# model echoes most often. `_` is a word character, so \bnon[-\s]?compliant\b
# does not match it and \bcompliant\b does not match inside it -- a first draft
# of this regex silently dropped every correct NON_COMPLIANT restatement.
_NON_COMPLIANT_CLAIM = re.compile(
    r"\bnon[-_\s]?compliant\b|\bnoncompliant\b|\bnot\s+(?:shariah[-\s]?)?compliant\b"
    r"|\bimpermissible\b|\bnot\s+permissible\b",
    re.IGNORECASE,
)
_COMPLIANT_CLAIM = re.compile(r"\bcompliant\b|\bpermissible\b", re.IGNORECASE)


def _verdict_restatement_is_faithful(text: str, verdict: dict | None) -> bool:
    """Did the model restate OUR verdict, for OUR ticker, with OUR polarity?"""
    if not verdict:
        # Nothing was supplied, so nothing may be claimed. A summary that invents a
        # Shariah status out of nowhere is the same fabrication by another route.
        stripped = _NON_COMPLIANT_CLAIM.sub(" ", text)
        return not _NON_COMPLIANT_CLAIM.search(text) and not _COMPLIANT_CLAIM.search(stripped)

    symbol = str(verdict.get("symbol") or "").strip()
    if not symbol or not re.search(rf"\b{re.escape(symbol)}\b", text, re.IGNORECASE):
        return False

    says_non_compliant = bool(_NON_COMPLIANT_CLAIM.search(text))
    # Remove the "non-compliant" phrases before looking for a bare "compliant",
    # since the negative phrase contains the positive word.
    says_compliant = bool(_COMPLIANT_CLAIM.search(_NON_COMPLIANT_CLAIM.sub(" ", text)))

    status = str(verdict.get("status") or "").upper()
    if status == "COMPLIANT":
        return says_compliant and not says_non_compliant
    if status == "NON_COMPLIANT":
        return says_non_compliant and not says_compliant
    # UNKNOWN / ERROR are not tradeable and have no polarity to restate, so the
    # summary must not claim either one.
    return not says_non_compliant and not says_compliant


# Latency ceiling for one /news request's whole summarization pass. The News
# panel is waiting on this; five sequential model calls measured ~25s on a live
# run, and a slow provider has no upper bound at all. Articles past the deadline
# keep the publisher's summary, exactly like any other skipped article.
_SUMMARY_DEADLINE_SECONDS = 25

# Bound on the in-memory cache, so a long-running server cannot grow it forever.
_SUMMARY_CACHE_MAX_ENTRIES = 512

# {cache_key: (unix_seconds, summary_dict)}. A plain dict on purpose: this is a
# local paper-trading deployment, losing it on restart costs one extra call per
# article, and sec_edgar_cache.py is scoped by its own docstring to SEC raw
# responses only -- reusing it for model output would break that scoping.
_SUMMARY_CACHE: dict = {}


def openrouter_request(payload: dict, *, api_key: str) -> dict:
    """Single seam for every OpenRouter call, so tests never touch the network.

    Same contract as alpaca_paper_adapter.alpaca_request: never raises, always
    returns {"ok", "status_code", "data", and "reason" on failure}. stdlib only --
    this repo has no `requests` dependency (see backend/requirements.txt).
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    request = Request(OPENROUTER_URL, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return {
                "ok": True,
                "status_code": response.status,
                "data": _decode_json(response.read()),
            }
    except HTTPError as exc:
        return {
            "ok": False,
            "status_code": exc.code,
            "data": _decode_json(exc.read()),
            "reason": f"http_{exc.code}",
        }
    except URLError as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "data": {}, "reason": type(exc).__name__}


def _decode_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lookup_verdict(connection, symbol: str) -> dict | None:
    """The verdict this app has ALREADY recorded for `symbol`, or None.

    Reads the append-only `shariah_screens` log first. Only a symbol that has
    never been screened falls through to a live `explain_symbol`, which costs an
    SEC fetch -- so the common path is a single indexed SELECT.

    Never raises. A locked database or an SEC outage means "no verdict to
    restate", which drops the badge; it must never turn into a broken News panel.
    """
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return None

    try:
        row = latest_shariah_screen(connection, normalized)
    except Exception:
        # The log could not be read. That is "we do not know", which is NOT the
        # same as "never screened" -- falling through to a live SEC fetch here
        # would spend 0.7-2s per article to work around a locked database. Drop
        # the badge instead; the summary still renders without one.
        return None

    if row:
        try:
            status = str(row["status"] or "UNKNOWN").upper()
            return {
                "symbol": normalized,
                "status": status,
                # The screen's own rule: only COMPLIANT is tradeable. Everything
                # else -- NON_COMPLIANT, UNKNOWN, ERROR -- stays rejected.
                "tradeable": status == "COMPLIANT",
                "reason": row["reason"] if "reason" in row.keys() else None,
                "screened_at": row["screened_at"] if "screened_at" in row.keys() else None,
                "source": "shariah_screens",
            }
        except Exception:
            return None

    try:
        verdict = explain_symbol(normalized).get("verdict") or {}
        status = str(verdict.get("status") or "UNKNOWN").upper()
        return {
            "symbol": normalized,
            "status": status,
            "tradeable": bool(verdict.get("tradeable")),
            "reason": verdict.get("statement"),
            "screened_at": None,
            "source": "shariah_explain",
        }
    except Exception:
        return None


def _build_user_message(article: dict, verdict: dict | None) -> str:
    headline = str(article.get("headline") or "").strip()
    source_summary = str(article.get("summary") or "").strip()
    symbols = [str(s) for s in (article.get("symbols") or []) if s]

    lines = [f"Headline: {headline}"]
    if source_summary:
        lines.append(f"Publisher summary: {source_summary}")
    if symbols:
        lines.append(f"Symbols mentioned: {', '.join(symbols)}")

    if verdict:
        reason = str(verdict.get("reason") or "").strip()
        lines.append(
            "Shariah verdict ALREADY DECIDED by this app's deterministic screen for "
            f"{verdict.get('symbol') or (symbols[0] if symbols else 'this symbol')}: "
            f"{verdict.get('status')}"
        )
        if reason:
            lines.append(f"Reason recorded by that screen: {reason}")
        lines.append(
            "Restate that verdict as settled background. Do not re-evaluate it and do "
            "not add a compliance opinion of your own."
        )
    else:
        lines.append(
            "No Shariah verdict is on record for this article's symbols. Say nothing "
            "about Shariah status."
        )

    lines.append("Now write the 2-3 sentence plain-text summary.")
    return "\n".join(lines)


def summarize_article(article: dict, *, verdict: dict | None, settings=None) -> dict | None:
    """One article -> one neutral summary, or None if anything at all goes wrong.

    None is returned for: no API key, a failed request, a response that does not
    carry the expected choices/message/content shape, an empty completion, and --
    most importantly -- a completion that used advisory language. The caller
    treats all of these identically: omit the block, keep the article.
    """
    try:
        settings = settings or load_settings()
        api_key = settings.openrouter_api_key
        if not api_key:
            return None

        payload = {
            "model": settings.openrouter_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_message(article, verdict)},
            ],
            "max_tokens": 200,
            "temperature": 0.3,
        }

        response = openrouter_request(payload, api_key=api_key)
        if not isinstance(response, dict) or not response.get("ok"):
            return None

        data = response.get("data")
        if not isinstance(data, dict):
            return None
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            return None
        text = message.get("content")
        if not isinstance(text, str):
            return None
        text = text.strip()
        if not text:
            return None

        if _FORBIDDEN_PATTERN.search(text):
            # The model advised despite being told not to. Drop it silently: the
            # article still renders with the publisher's own summary, and nothing
            # advisory reaches the screen.
            return None

        if not _verdict_restatement_is_faithful(text, verdict):
            # The model attributed a Shariah status to the wrong company, flipped
            # the polarity, or invented one where none was supplied. A fabricated
            # compliance claim carrying this app's name is worse than no summary.
            return None

        return {
            "text": text,
            "model": settings.openrouter_model,
            "shariah_status": verdict.get("status") if verdict else None,
            "shariah_tradeable": verdict.get("tradeable") if verdict else None,
            "generated_at": _utc_now(),
        }
    except Exception:
        return None


def _cache_key(article: dict, symbol: str | None) -> str:
    """Article identity AND the symbol its verdict belongs to.

    Keying on the article alone is wrong, and dangerously so: Alpaca tags an
    article with every ticker it mentions -- six is normal -- and the summary
    carries the verdict for whichever of those the caller asked about. Cache it
    under the article only, and a later request for a different symbol gets the
    first symbol's compliance badge. A viewer looking at GOOGL news would be
    shown JPM's NON_COMPLIANT verdict, cached, on an article about Tesla.
    """
    suffix = f"|{symbol or '-'}"
    identifier = article.get("id")
    if identifier not in (None, ""):
        return f"id:{identifier}{suffix}"
    return f"hc:{article.get('headline') or ''}|{article.get('created_at') or ''}{suffix}"


def _cached(key: str, ttl_minutes: float):
    if ttl_minutes <= 0:
        return None
    entry = _SUMMARY_CACHE.get(key)
    if not entry:
        return None
    stored_at, summary = entry
    if (datetime.now(timezone.utc).timestamp() - stored_at) > ttl_minutes * 60:
        return None
    return summary


def _store(key: str, summary: dict) -> None:
    """Cache one summary, dropping the oldest entries once the cache is full.

    Unbounded growth is not acceptable in a long-running process: entries are
    only ever added, never expired in place, so a server left up for a week
    accumulates one entry per article per symbol seen. The cap is generous
    relative to a 60-minute TTL and a 20-article page.
    """
    if len(_SUMMARY_CACHE) >= _SUMMARY_CACHE_MAX_ENTRIES:
        for stale in sorted(_SUMMARY_CACHE, key=lambda k: _SUMMARY_CACHE[k][0])[
            : max(1, _SUMMARY_CACHE_MAX_ENTRIES // 4)
        ]:
            _SUMMARY_CACHE.pop(stale, None)
    _SUMMARY_CACHE[key] = (datetime.now(timezone.utc).timestamp(), summary)


def _pick_symbol(article: dict, requested_symbols) -> str | None:
    """Prefer a symbol the caller actually asked about, so a multi-symbol article
    gets the verdict for the name the user is looking at rather than whichever
    ticker the publisher happened to list first."""
    symbols = [str(s).strip().upper() for s in (article.get("symbols") or []) if s]
    if not symbols:
        return None
    if requested_symbols:
        wanted = {str(s).strip().upper() for s in requested_symbols if s}
        for symbol in symbols:
            if symbol in wanted:
                return symbol
    return symbols[0]


def attach_ai_summaries(articles, *, connection, requested_symbols=None, settings=None) -> list:
    """Add `ai_summary` to the first N articles, in place, and return the list.

    N is NEWS_AI_SUMMARY_MAX_ARTICLES, applied to the response as a whole rather
    than per symbol, so latency and spend stay bounded however large the watchlist
    grows. One busy symbol can therefore use up the whole budget and leave a
    quieter symbol with no AI summaries in that response -- an accepted trade,
    since every article keeps Alpaca's own headline and summary regardless.

    `ai_summary` is set only when a summary was actually produced. When it was
    not, the key is absent entirely rather than present-and-null, matching the
    dashboard's existing `if (article.summary)` truthy-check idiom.

    Never raises.
    """
    try:
        if not isinstance(articles, list) or not articles:
            return articles

        settings = settings or load_settings()
        if not settings.news_ai_summary_enabled or not settings.openrouter_api_key:
            return articles

        budget = max(0, int(settings.news_ai_summary_max_articles))
        ttl_minutes = float(settings.news_ai_summary_cache_ttl_minutes)
        verdict_cache: dict = {}
        # Wall-clock stop. The model calls run one after another, so on a slow
        # day the budget alone bounds spend but not latency -- and /news is a
        # foreground request the News panel is waiting on. Past the deadline the
        # remaining articles simply keep the publisher's own summary, which is
        # the same outcome as any other skipped article.
        deadline = datetime.now(timezone.utc).timestamp() + _SUMMARY_DEADLINE_SECONDS

        for article in articles[:budget]:
            if not isinstance(article, dict):
                continue
            try:
                symbol = _pick_symbol(article, requested_symbols)
                key = _cache_key(article, symbol)
                summary = _cached(key, ttl_minutes)
                if summary is None:
                    if datetime.now(timezone.utc).timestamp() >= deadline:
                        break
                    if symbol and symbol not in verdict_cache:
                        verdict_cache[symbol] = _lookup_verdict(connection, symbol)
                    verdict = verdict_cache.get(symbol) if symbol else None
                    summary = summarize_article(article, verdict=verdict, settings=settings)
                    if summary is not None:
                        _store(key, summary)
                if summary is not None:
                    article["ai_summary"] = summary
            except Exception:
                # One bad article must not cost the other nineteen their summaries.
                continue

        return articles
    except Exception:
        return articles
