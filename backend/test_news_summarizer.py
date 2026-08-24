"""Verify the AI news summarizer without contacting OpenRouter, SEC, or a real DB.

The safety-critical assertions here are not the happy path -- they are the two
that keep a language model out of the decision path, per CLAUDE.md: the
forbidden-language filter that drops any summary drifting into advice, and the
fact that every single failure mode returns None rather than raising, so a
broken summarizer can never take the News panel down with it.

Both are mutation-checked in check_mutation_guards() below: the test breaks the
module deliberately and asserts the guard notices.
"""

import dataclasses
import os
import sqlite3
import time

import news_summarizer
from config import load_settings


ARTICLE = {
    "id": 12345,
    "headline": "Chevron reports higher quarterly production",
    "summary": "Chevron Corporation said output rose in the quarter.",
    "source": "benzinga",
    "url": "https://example.com/cvx",
    "created_at": "2026-08-23T14:00:00Z",
    "symbols": ["CVX"],
}

VERDICT = {
    # `symbol` is always set by _lookup_verdict, and the verdict-integrity check
    # requires it -- the fixture has to match what production really returns.
    "symbol": "CVX",
    "status": "COMPLIANT",
    "tradeable": True,
    "reason": "debt 12.4% and cash 3.1% are both under the 33% limit",
}

CLEAN_SUMMARY = (
    "Chevron told shareholders that oil and gas output increased compared with the "
    "previous quarter. The company attributed the rise to new wells coming online. "
    "This app's existing Shariah screen has already recorded CVX as COMPLIANT."
)


def settings_with(**overrides):
    """A real Settings object with fields overridden -- exercises the true shape."""
    return dataclasses.replace(load_settings(), **overrides)


# Distinct from None, which is itself one of the malformed payloads under test.
_UNSET = object()


class FakeOpenRouter:
    """Records every request and replays one canned OpenRouter response."""

    def __init__(self, content=CLEAN_SUMMARY, *, ok=True, data=_UNSET):
        self.calls = []
        self.content = content
        self.ok = ok
        self.data = data

    def __call__(self, payload, *, api_key):
        self.calls.append({"payload": payload, "api_key": api_key})
        if not self.ok:
            return {"ok": False, "status_code": 500, "data": {}, "reason": "http_500"}
        if self.data is not _UNSET:
            return {"ok": True, "status_code": 200, "data": self.data}
        return {
            "ok": True,
            "status_code": 200,
            "data": {"choices": [{"message": {"content": self.content}}]},
        }


# Captured before any test stubs it, so a test that genuinely exercises verdict
# lookup can put the real one back rather than inheriting a previous stub.
_REAL_LOOKUP_VERDICT = news_summarizer._lookup_verdict

STUB_CVX_VERDICT = {
    "symbol": "CVX",
    "status": "COMPLIANT",
    "tradeable": True,
    "reason": "under limits",
    "source": "shariah_screens",
}


def stub_verdict_lookup():
    """Pin _lookup_verdict for tests that are not about verdict lookup.

    Without this they depend on a live SEC screen resolving CVX -- which both
    reaches the network and, once the pass deadline stops allowing live screens,
    silently changes what those tests are measuring.
    """
    news_summarizer._lookup_verdict = lambda connection, symbol, **kwargs: STUB_CVX_VERDICT


def memory_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def check_request_shape() -> None:
    """Assert the request that was built, not just the answer that came back."""
    fake = FakeOpenRouter()
    news_summarizer.openrouter_request = fake
    settings = settings_with(openrouter_api_key="test-key", openrouter_model="test/model-1")

    result = news_summarizer.summarize_article(ARTICLE, verdict=VERDICT, settings=settings)
    assert result is not None, result
    assert len(fake.calls) == 1, fake.calls

    call = fake.calls[0]
    assert call["api_key"] == "test-key", call
    payload = call["payload"]
    assert payload["model"] == "test/model-1", payload
    assert payload["max_tokens"] == 200, payload
    assert payload["temperature"] == 0.3, payload

    messages = payload["messages"]
    assert messages[0]["role"] == "system", messages
    assert messages[0]["content"] == news_summarizer.SYSTEM_PROMPT, messages
    assert messages[1]["role"] == "user", messages

    user = messages[1]["content"]
    assert ARTICLE["headline"] in user, user
    assert ARTICLE["summary"] in user, user
    assert "CVX" in user, user
    # The verdict must reach the model as already-decided background.
    assert "COMPLIANT" in user, user
    assert VERDICT["reason"] in user, user

    # And the returned envelope carries the verdict through for the UI badge.
    assert result["text"] == CLEAN_SUMMARY, result
    assert result["model"] == "test/model-1", result
    assert result["shariah_status"] == "COMPLIANT", result
    assert result["shariah_tradeable"] is True, result
    assert result["generated_at"].endswith("+00:00") or result["generated_at"].endswith("Z"), result


def check_no_verdict_still_summarizes() -> None:
    """No screen on record is not an error -- it just means no verdict to restate."""
    fake = FakeOpenRouter(content="Chevron said quarterly output rose on new wells.")
    news_summarizer.openrouter_request = fake
    settings = settings_with(openrouter_api_key="test-key")

    result = news_summarizer.summarize_article(ARTICLE, verdict=None, settings=settings)
    assert result is not None, result
    assert result["shariah_status"] is None, result
    assert result["shariah_tradeable"] is None, result
    user = fake.calls[0]["payload"]["messages"][1]["content"]
    assert "COMPLIANT" not in user, user


def check_forbidden_language_is_filtered() -> None:
    """The prompt asks the model not to advise. This is what happens when it does anyway.

    Every one of these is a real drift mode for a summarization model handed a
    finance headline, and each must be dropped rather than displayed.
    """
    # Each carries a faithful CVX/COMPLIANT restatement so it passes the
    # verdict-integrity check -- leaving the forbidden-word filter as the only
    # thing that can drop it. Without this the two guards mask each other and
    # neither can be mutation-checked on its own.
    advisory_outputs = [
        "Chevron looks like a strong buy after this production beat. This app's existing screen records CVX as COMPLIANT.",
        "Investors should sell into the rally. This app's existing screen records CVX as COMPLIANT.",
        "This is a good investment for a Shariah-compliant portfolio. This app's existing screen records CVX as COMPLIANT.",
        "Analysts recommend the stock at these levels. This app's existing screen records CVX as COMPLIANT.",
        "Shareholders should consider their position. This app's existing screen records CVX as COMPLIANT.",
        "The stock now presents an opportunity at current prices. This app's existing screen records CVX as COMPLIANT.",
        "CVX appears undervalued relative to peers. This app's existing screen records CVX as COMPLIANT.",
        "The shares look overvalued after the run-up. This app's existing screen records CVX as COMPLIANT.",
        "The average target price is now $230. This app's existing screen records CVX as COMPLIANT.",
        "This is bullish for the share price. This app's existing screen records CVX as COMPLIANT.",
        "The news is bearish for the stock. This app's existing screen records CVX as COMPLIANT.",
        "Now is a good time to act on this name. This app's existing screen records CVX as COMPLIANT.",
        "The stock should outperform the sector. This app's existing screen records CVX as COMPLIANT.",
        "Buy the dip on Chevron after this report. This app's existing screen records CVX as COMPLIANT.",
        "SELL signal confirmed for CVX. This app's existing screen records CVX as COMPLIANT.",
        "Investors Should Consider Their Position Here. This app's existing screen records CVX as COMPLIANT.",
        "A Strong Buy rating was reiterated. This app's existing screen records CVX as COMPLIANT.",
        "This remains an attractive INVESTMENT for the portfolio. This app's existing screen records CVX as COMPLIANT.",
    ]
    settings = settings_with(openrouter_api_key="test-key")
    for output in advisory_outputs:
        news_summarizer.openrouter_request = FakeOpenRouter(content=output)
        result = news_summarizer.summarize_article(ARTICLE, verdict=VERDICT, settings=settings)
        assert result is None, f"advisory language slipped through: {output!r} -> {result!r}"

    # ...and a genuinely neutral summary is not over-filtered into uselessness.
    neutral = [
        CLEAN_SUMMARY,
        "Chevron said production rose. Investors reacted to the announcement. "
        "This app's existing screen already records CVX as COMPLIANT.",
        "The company reported quarterly results above its own prior guidance. "
        "This app's existing screen records CVX as COMPLIANT.",
    ]
    for output in neutral:
        news_summarizer.openrouter_request = FakeOpenRouter(content=output)
        result = news_summarizer.summarize_article(ARTICLE, verdict=VERDICT, settings=settings)
        assert result is not None, f"neutral summary was wrongly filtered: {output!r}"


def check_fabricated_verdicts_are_dropped() -> None:
    """A model that attributes OUR verdict to the wrong company must be dropped.

    The first case is verbatim from a live run on 2026-08-24: the prompt said
    "verdict ALREADY DECIDED for JPM: NON_COMPLIANT" on a multi-symbol article
    whose subject was Tesla, and deepseek-chat returned a sentence claiming this
    app's screen found *Tesla* compliant. Nothing advisory appears in it, so the
    word filter is blind to it -- and a fabricated compliance claim wearing this
    app's name is the worst output this feature can produce.
    """
    settings = settings_with(openrouter_api_key="test-key")
    jpm = {"symbol": "JPM", "status": "NON_COMPLIANT", "tradeable": False, "reason": "banking"}
    cvx = {"symbol": "CVX", "status": "COMPLIANT", "tradeable": True, "reason": "under limits"}

    fabrications = [
        # The real one: wrong company, inverted polarity, our name on it.
        (
            jpm,
            "Elon Musk's net worth rose as analysts watched Tesla and SpaceX. This app's "
            "existing screen found Tesla compliant with Shariah principles due to its debt "
            "and cash levels being below thresholds.",
        ),
        # Right company, flipped polarity.
        (jpm, "JPM was found compliant by this app's existing screen."),
        (cvx, "This app's existing screen records CVX as non-compliant."),
        # Correct polarity, but attributed to a company we never screened.
        (jpm, "This app's screen marks Goldman Sachs as non-compliant. JPMorgan had a busy week."),
        # A verdict was supplied and simply never restated -- the badge would then
        # assert a status the text never supports.
        (jpm, "JPM reported a strong quarter and announced a new data centre."),
        # No verdict supplied, but the model invented one anyway.
        (None, "Chevron said output rose. CVX is Shariah compliant."),
        (None, "Chevron said output rose. The company is not permissible under Shariah."),
    ]
    for verdict, output in fabrications:
        news_summarizer.openrouter_request = FakeOpenRouter(content=output)
        result = news_summarizer.summarize_article(ARTICLE, verdict=verdict, settings=settings)
        assert result is None, f"fabricated verdict slipped through: {output!r} -> {result!r}"

    # Faithful restatements of each polarity still pass.
    faithful = [
        (
            jpm,
            "JPMorgan warned on food prices. This app's existing screen records JPM as "
            "non-compliant because of conventional banking activity.",
        ),
        # Verbatim shapes from live deepseek-chat runs. The underscore spelling is
        # the status token the prompt itself supplies, so it is the one the model
        # echoes most -- and the one the first draft of the regex could not see.
        (
            jpm,
            "This app's existing screen has already determined JPM is NON_COMPLIANT "
            "due to conventional banking activities. JPMorgan warned on food prices.",
        ),
        (jpm, "JPM is non compliant per this app's existing screen."),
        (jpm, "This app's existing screen marks JPM as noncompliant."),
        (cvx, "Chevron said output rose. This app's existing screen records CVX as compliant."),
        (None, "Chevron said quarterly output rose on new wells coming online."),
        # A ticker written in lower case is still our ticker. Dropping this would
        # only over-filter rather than leak, but the intent should be pinned.
        (cvx, "Chevron said output rose. This app's existing screen records cvx as compliant."),
    ]
    for verdict, output in faithful:
        news_summarizer.openrouter_request = FakeOpenRouter(content=output)
        result = news_summarizer.summarize_article(ARTICLE, verdict=verdict, settings=settings)
        assert result is not None, f"faithful restatement was wrongly dropped: {output!r}"


def check_every_failure_mode_returns_none() -> None:
    """No API key, a failed request, and every malformed-response shape."""
    good = settings_with(openrouter_api_key="test-key")

    news_summarizer.openrouter_request = FakeOpenRouter()
    no_key = settings_with(openrouter_api_key=None)
    assert news_summarizer.summarize_article(ARTICLE, verdict=None, settings=no_key) is None

    news_summarizer.openrouter_request = FakeOpenRouter(ok=False)
    assert news_summarizer.summarize_article(ARTICLE, verdict=None, settings=good) is None

    malformed = [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
        {"choices": "not-a-list"},
        {"choices": [{"message": "not-a-dict"}]},
        None,
        "a bare string",
    ]
    for data in malformed:
        news_summarizer.openrouter_request = FakeOpenRouter(data=data)
        result = news_summarizer.summarize_article(ARTICLE, verdict=None, settings=good)
        assert result is None, f"malformed response produced a summary: {data!r} -> {result!r}"

    # A seam that raises outright must still not escape.
    def exploding(payload, *, api_key):
        raise RuntimeError("network on fire")

    news_summarizer.openrouter_request = exploding
    assert news_summarizer.summarize_article(ARTICLE, verdict=None, settings=good) is None


def check_cache_prevents_a_second_call() -> None:
    stub_verdict_lookup()
    fake = FakeOpenRouter()
    news_summarizer.openrouter_request = fake
    news_summarizer._SUMMARY_CACHE.clear()
    settings = settings_with(openrouter_api_key="test-key", news_ai_summary_cache_ttl_minutes=60)
    connection = memory_connection()
    try:
        first = news_summarizer.attach_ai_summaries(
            [dict(ARTICLE)], connection=connection, settings=settings
        )
        assert "ai_summary" in first[0], first[0]
        assert len(fake.calls) == 1, fake.calls

        second = news_summarizer.attach_ai_summaries(
            [dict(ARTICLE)], connection=connection, settings=settings
        )
        assert "ai_summary" in second[0], second[0]
        # The assertion the cache mutation check flips:
        assert len(fake.calls) == 1, f"cache did not prevent a second call: {fake.calls}"
        assert second[0]["ai_summary"]["text"] == CLEAN_SUMMARY, second[0]
    finally:
        connection.close()


def check_cache_does_not_leak_a_verdict_across_symbols() -> None:
    """The badge shown must belong to the symbol the viewer asked about.

    Alpaca tags an article with every ticker it mentions -- six is normal. Keying
    the cache on the article alone meant the first caller's verdict was replayed
    to every later caller: a viewer looking at GOOGL news would be shown JPM's
    NON_COMPLIANT badge, from cache, on an article about Tesla. That is a wrong
    compliance claim reaching the screen, the same class of failure as a
    fabricated one, just arriving by a different route.
    """
    multi = {
        "id": 999,
        "headline": "This Week in Tesla",
        "summary": "Tesla news.",
        "created_at": "2026-08-24T00:00:00Z",
        "symbols": ["AMZN", "GOOGL", "JPM", "TSLA"],
    }

    def by_symbol(payload, *, api_key):
        user = payload["messages"][1]["content"]
        symbol = "JPM" if "for JPM" in user else "GOOGL"
        word = "non-compliant" if symbol == "JPM" else "compliant"
        return {
            "ok": True,
            "status_code": 200,
            "data": {
                "choices": [
                    {
                        "message": {
                            "content": f"This app's existing screen records {symbol} as {word}."
                        }
                    }
                ]
            },
        }

    original_latest = news_summarizer.latest_shariah_screen
    # This test is about the real lookup keyed by symbol, so undo any stub an
    # earlier check installed.
    news_summarizer._lookup_verdict = _REAL_LOOKUP_VERDICT
    try:
        news_summarizer.openrouter_request = by_symbol
        news_summarizer.latest_shariah_screen = lambda connection, symbol: {
            "status": "NON_COMPLIANT" if symbol == "JPM" else "COMPLIANT",
            "reason": "r",
            "screened_at": "2026-08-24",
        }
        news_summarizer._SUMMARY_CACHE.clear()
        settings = settings_with(
            openrouter_api_key="test-key", news_ai_summary_cache_ttl_minutes=60
        )
        connection = memory_connection()
        try:
            viewing_jpm = news_summarizer.attach_ai_summaries(
                [dict(multi)], connection=connection, requested_symbols=["JPM"], settings=settings
            )
            assert viewing_jpm[0]["ai_summary"]["shariah_status"] == "NON_COMPLIANT", viewing_jpm[0]

            viewing_googl = news_summarizer.attach_ai_summaries(
                [dict(multi)], connection=connection, requested_symbols=["GOOGL"], settings=settings
            )
            summary = viewing_googl[0]["ai_summary"]
            assert summary["shariah_status"] == "COMPLIANT", (
                f"cached verdict leaked across symbols: {summary}"
            )
            assert "JPM" not in summary["text"], summary

            # Same symbol again really is a cache hit -- the fix must not disable caching.
            calls = []
            news_summarizer.openrouter_request = lambda payload, *, api_key: (
                calls.append(1) or by_symbol(payload, api_key=api_key)
            )
            news_summarizer.attach_ai_summaries(
                [dict(multi)], connection=connection, requested_symbols=["GOOGL"], settings=settings
            )
            assert calls == [], f"symbol-keyed cache stopped caching: {calls}"
        finally:
            connection.close()
    finally:
        news_summarizer.latest_shariah_screen = original_latest


def check_cache_is_bounded() -> None:
    """A long-running server must not grow the cache without limit."""
    stub_verdict_lookup()
    news_summarizer._SUMMARY_CACHE.clear()
    news_summarizer.openrouter_request = FakeOpenRouter()
    settings = settings_with(openrouter_api_key="test-key", news_ai_summary_max_articles=1)
    connection = memory_connection()
    try:
        for index in range(news_summarizer._SUMMARY_CACHE_MAX_ENTRIES + 40):
            news_summarizer.attach_ai_summaries(
                [dict(ARTICLE, id=index)],
                connection=connection,
                settings=settings,
            )
        assert len(news_summarizer._SUMMARY_CACHE) <= news_summarizer._SUMMARY_CACHE_MAX_ENTRIES, (
            f"cache grew past its cap: {len(news_summarizer._SUMMARY_CACHE)}"
        )
        assert len(news_summarizer._SUMMARY_CACHE) > 0, "eviction emptied the cache entirely"
    finally:
        connection.close()
        news_summarizer._SUMMARY_CACHE.clear()


def check_deadline_stops_a_slow_pass() -> None:
    """Latency is bounded even when the provider is slow."""
    stub_verdict_lookup()
    news_summarizer._SUMMARY_CACHE.clear()
    calls = []
    original_deadline = news_summarizer._SUMMARY_DEADLINE_SECONDS
    try:
        # Every call "takes" longer than the whole budget, so exactly one gets made.
        news_summarizer._SUMMARY_DEADLINE_SECONDS = 0.05

        def slow(payload, *, api_key):
            calls.append(1)
            time.sleep(0.12)
            return {
                "ok": True,
                "status_code": 200,
                "data": {"choices": [{"message": {"content": CLEAN_SUMMARY}}]},
            }

        news_summarizer.openrouter_request = slow
        settings = settings_with(openrouter_api_key="test-key", news_ai_summary_max_articles=5)
        articles = [dict(ARTICLE, id=index) for index in range(5)]
        connection = memory_connection()
        try:
            result = news_summarizer.attach_ai_summaries(
                articles, connection=connection, settings=settings
            )
            assert len(calls) == 1, f"the deadline did not stop the pass: {len(calls)} calls"
            # And the un-summarized articles still carry the publisher's summary.
            assert all(article["summary"] for article in result), result
            assert sum("ai_summary" in article for article in result) == 1, result
        finally:
            connection.close()
    finally:
        news_summarizer._SUMMARY_DEADLINE_SECONDS = original_deadline
        news_summarizer._SUMMARY_CACHE.clear()


def check_live_screen_is_bounded() -> None:
    """A live SEC screen must not run outside the pass deadline, or more than once.

    Production, 2026-08-24: /news for a never-before-screened symbol took 86s and
    the client got a dead connection while the app itself logged 200 -- the
    deadline only gated the model call, and the SEC screen behind an unseen
    symbol (25s per request, sometimes several) ran outside it entirely.
    """
    news_summarizer._lookup_verdict = _REAL_LOOKUP_VERDICT
    original_latest = news_summarizer.latest_shariah_screen
    original_explain = news_summarizer.explain_symbol
    original_deadline = news_summarizer._SUMMARY_DEADLINE_SECONDS
    screened = []
    try:
        news_summarizer.openrouter_request = FakeOpenRouter(
            content="Nothing on record for this one, so no status is claimed."
        )
        news_summarizer.latest_shariah_screen = lambda connection, symbol: None
        news_summarizer.explain_symbol = lambda symbol: (
            screened.append(symbol)
            or {"verdict": {"status": "COMPLIANT", "tradeable": True, "statement": "ok"}}
        )
        settings = settings_with(openrouter_api_key="test-key", news_ai_summary_max_articles=5)
        articles = [dict(ARTICLE, id=index, symbols=[f"SYM{index}"]) for index in range(5)]
        connection = memory_connection()
        try:
            # Five unseen symbols, but only one live screen is paid for.
            news_summarizer._SUMMARY_CACHE.clear()
            news_summarizer.attach_ai_summaries(articles, connection=connection, settings=settings)
            assert len(screened) <= news_summarizer._MAX_LIVE_SCREENS_PER_REQUEST, (
                f"paid for {len(screened)} live SEC screens in one request: {screened}"
            )

            # And with no time left in the pass, none at all.
            screened.clear()
            news_summarizer._SUMMARY_CACHE.clear()
            news_summarizer._SUMMARY_DEADLINE_SECONDS = 1  # below the live-screen floor
            news_summarizer.attach_ai_summaries(
                [dict(ARTICLE, id=99, symbols=["NEWSYM"])],
                connection=connection,
                settings=settings,
            )
            assert screened == [], f"ran a live SEC screen with no time budget: {screened}"
        finally:
            connection.close()
    finally:
        news_summarizer.latest_shariah_screen = original_latest
        news_summarizer.explain_symbol = original_explain
        news_summarizer._SUMMARY_DEADLINE_SECONDS = original_deadline
        news_summarizer._SUMMARY_CACHE.clear()


def check_cache_expires() -> None:
    stub_verdict_lookup()
    fake = FakeOpenRouter()
    news_summarizer.openrouter_request = fake
    news_summarizer._SUMMARY_CACHE.clear()
    settings = settings_with(openrouter_api_key="test-key", news_ai_summary_cache_ttl_minutes=0)
    connection = memory_connection()
    try:
        news_summarizer.attach_ai_summaries(
            [dict(ARTICLE)], connection=connection, settings=settings
        )
        news_summarizer.attach_ai_summaries(
            [dict(ARTICLE)], connection=connection, settings=settings
        )
        assert len(fake.calls) == 2, f"a zero TTL should not serve from cache: {fake.calls}"
    finally:
        connection.close()


def check_max_articles_cap() -> None:
    stub_verdict_lookup()
    fake = FakeOpenRouter()
    news_summarizer.openrouter_request = fake
    news_summarizer._SUMMARY_CACHE.clear()
    settings = settings_with(openrouter_api_key="test-key", news_ai_summary_max_articles=2)
    articles = [dict(ARTICLE, id=index, headline=f"Story {index}") for index in range(6)]
    connection = memory_connection()
    try:
        result = news_summarizer.attach_ai_summaries(
            articles, connection=connection, settings=settings
        )
        assert len(fake.calls) == 2, fake.calls
        assert sum("ai_summary" in article for article in result) == 2, result
        # Beyond the cap the key is absent entirely, not present-and-null: the
        # dashboard tests `article.ai_summary && ...`, so a null would be a
        # different-shaped falsy that still says "we tried and got nothing".
        assert "ai_summary" not in result[5], result[5]
        # Alpaca's own summary survives on every article regardless.
        assert all(article["summary"] for article in result), result
    finally:
        connection.close()


def check_disabled_and_keyless_paths_are_untouched() -> None:
    for settings in [
        settings_with(openrouter_api_key="test-key", news_ai_summary_enabled=False),
        settings_with(openrouter_api_key=None, news_ai_summary_enabled=True),
    ]:
        fake = FakeOpenRouter()
        news_summarizer.openrouter_request = fake
        news_summarizer._SUMMARY_CACHE.clear()
        connection = memory_connection()
        try:
            result = news_summarizer.attach_ai_summaries(
                [dict(ARTICLE)], connection=connection, settings=settings
            )
            assert fake.calls == [], fake.calls
            assert "ai_summary" not in result[0], result[0]
            assert result[0]["headline"] == ARTICLE["headline"], result[0]
        finally:
            connection.close()


def check_total_failure_returns_articles_unchanged() -> None:
    """Both halves blow up. The News panel still gets its articles."""
    original_lookup = news_summarizer._lookup_verdict
    original_summarize = news_summarizer.summarize_article

    def exploding_lookup(connection, symbol):
        raise RuntimeError("database on fire")

    def exploding_summarize(article, *, verdict, settings):
        raise RuntimeError("model on fire")

    news_summarizer._lookup_verdict = exploding_lookup
    news_summarizer.summarize_article = exploding_summarize
    news_summarizer._SUMMARY_CACHE.clear()
    settings = settings_with(openrouter_api_key="test-key")
    connection = memory_connection()
    try:
        articles = [dict(ARTICLE)]
        result = news_summarizer.attach_ai_summaries(
            articles, connection=connection, settings=settings
        )
        assert len(result) == 1, result
        assert "ai_summary" not in result[0], result[0]
        assert result[0]["headline"] == ARTICLE["headline"], result[0]
        assert result[0]["summary"] == ARTICLE["summary"], result[0]
    finally:
        news_summarizer._lookup_verdict = original_lookup
        news_summarizer.summarize_article = original_summarize
        connection.close()


def check_verdict_lookup_never_raises() -> None:
    """A closed database and a never-screened symbol both resolve to None, not an error."""
    original_latest = news_summarizer.latest_shariah_screen
    original_explain = news_summarizer.explain_symbol
    # Exercises the real function, not whichever stub an earlier check left behind.
    news_summarizer._lookup_verdict = _REAL_LOOKUP_VERDICT
    try:
        closed = memory_connection()
        closed.close()
        assert news_summarizer._lookup_verdict(closed, "CVX") is None

        # Screened before: the stored verdict is reused, no live screen at all.
        news_summarizer.latest_shariah_screen = lambda connection, symbol: {
            "status": "NON_COMPLIANT",
            "reason": "debt ratio 41.2% exceeds the 33% limit",
            "screened_at": "2026-08-23T00:00:00+00:00",
        }
        news_summarizer.explain_symbol = lambda symbol: (_ for _ in ()).throw(
            AssertionError("must not screen live when a stored verdict exists")
        )
        stored = news_summarizer._lookup_verdict(memory_connection(), "CVX")
        assert stored["status"] == "NON_COMPLIANT", stored
        assert stored["tradeable"] is False, stored

        # Never screened: fall back to a live explain, and survive it failing.
        news_summarizer.latest_shariah_screen = lambda connection, symbol: None
        news_summarizer.explain_symbol = lambda symbol: {
            "verdict": {"status": "COMPLIANT", "tradeable": True, "statement": "within limits"}
        }
        live = news_summarizer._lookup_verdict(memory_connection(), "CVX")
        assert live["status"] == "COMPLIANT", live
        assert live["tradeable"] is True, live

        news_summarizer.explain_symbol = lambda symbol: (_ for _ in ()).throw(
            RuntimeError("SEC down")
        )
        assert news_summarizer._lookup_verdict(memory_connection(), "CVX") is None
    finally:
        news_summarizer.latest_shariah_screen = original_latest
        news_summarizer.explain_symbol = original_explain


def check_mutation_guards() -> None:
    """Deliberately break each safety guard and confirm this file notices.

    A green test that would stay green with the guard removed is not evidence.
    Both guards here are load-bearing for CLAUDE.md's no-LLM-in-the-decision-path
    rule, so both are checked rather than trusted.
    """
    import re

    # 1. The forbidden-language filter.
    original_pattern = news_summarizer._FORBIDDEN_PATTERN
    try:
        news_summarizer._FORBIDDEN_PATTERN = re.compile(r"(?!x)x")  # matches nothing
        leaked = False
        try:
            check_forbidden_language_is_filtered()
        except AssertionError:
            leaked = True
        assert leaked, (
            "MUTATION SURVIVED: neutering _FORBIDDEN_PATTERN did not fail the filter test"
        )
    finally:
        news_summarizer._FORBIDDEN_PATTERN = original_pattern

    # 2. The cache.
    original_cache = news_summarizer._SUMMARY_CACHE
    try:

        class NeverCaches(dict):
            """Accepts writes, serves nothing -- exactly a bypassed cache."""

            def __setitem__(self, key, value):
                return None

        news_summarizer._SUMMARY_CACHE = NeverCaches()
        noticed = False
        try:
            check_cache_prevents_a_second_call()
        except AssertionError:
            noticed = True
        assert noticed, "MUTATION SURVIVED: bypassing the cache did not fail the call-count test"
    finally:
        news_summarizer._SUMMARY_CACHE = original_cache

    # Both guards restored and still green.
    check_forbidden_language_is_filtered()
    check_cache_prevents_a_second_call()


def main() -> None:
    original_request = news_summarizer.openrouter_request
    original_lookup = news_summarizer._lookup_verdict
    saved_key = os.environ.get("OPENROUTER_API_KEY")
    try:
        check_request_shape()
        check_no_verdict_still_summarizes()
        check_forbidden_language_is_filtered()
        check_fabricated_verdicts_are_dropped()
        check_every_failure_mode_returns_none()
        check_cache_prevents_a_second_call()
        check_cache_does_not_leak_a_verdict_across_symbols()
        check_cache_is_bounded()
        check_deadline_stops_a_slow_pass()
        check_live_screen_is_bounded()
        check_cache_expires()
        check_max_articles_cap()
        check_disabled_and_keyless_paths_are_untouched()
        check_total_failure_returns_articles_unchanged()
        check_verdict_lookup_never_raises()
        check_mutation_guards()
    finally:
        news_summarizer.openrouter_request = original_request
        news_summarizer._lookup_verdict = original_lookup
        news_summarizer._SUMMARY_CACHE.clear()
        if saved_key is None:
            os.environ.pop("OPENROUTER_API_KEY", None)
        else:
            os.environ["OPENROUTER_API_KEY"] = saved_key

    print("PASS: news summaries explain, never advise, and fail closed to plain articles.")


if __name__ == "__main__":
    main()
