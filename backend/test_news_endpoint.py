"""Verify GET /news through the real FastAPI app, without contacting Alpaca.

The point of interest is not the happy path -- it is that the AI layer is
strictly additive. A summarizer that explodes, a missing OpenRouter key, and a
symbol nobody has ever screened all have to end in the same place: a 200 with
Alpaca's own articles in it. The News panel is cosmetic; the compliance product
underneath it is not, and the cosmetic layer is not allowed to take it down.
"""

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


fixture_dir = tempfile.TemporaryDirectory()
universe_path = Path(fixture_dir.name) / "shariah_universe.json"
universe_path.write_text(
    json.dumps({"validation": {"status": "active"}, "records": []}),
    encoding="utf-8",
)
import pytest

_ENV_ORIG = {k: os.environ.get(k) for k in ["SHARIAH_UNIVERSE_PATH", "TRADING_MODE", "PAPER_EXECUTION_ENABLED", "PAPER_EXECUTION_ADAPTER", "MOOMOO_MODE"]}

@pytest.fixture(autouse=True)
def _restore_env():
    os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
    os.environ["TRADING_MODE"] = "approval"
    os.environ["PAPER_EXECUTION_ENABLED"] = "false"
    os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
    os.environ["MOOMOO_MODE"] = "paper"
    yield
    for _k in _ENV_ORIG:
        if _ENV_ORIG[_k] is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _ENV_ORIG[_k]

os.environ["SHARIAH_UNIVERSE_PATH"] = str(universe_path)
os.environ["TRADING_MODE"] = "approval"
os.environ["PAPER_EXECUTION_ENABLED"] = "false"
os.environ["PAPER_EXECUTION_ADAPTER"] = "disabled"
os.environ["MOOMOO_MODE"] = "paper"
# No key: the default path through this file is the fail-closed one.
#
# Set to empty rather than popped. config._load_local_env() runs at import and
# uses os.environ.setdefault, so a popped variable is refilled from a developer's
# real backend/.env -- which made this file issue live OpenRouter calls the moment
# a key was configured. An empty string is present (so setdefault leaves it) and
# falsy (so `os.getenv(...) or None` still yields None).


# These import after the fixture above, not before it: local_api reads
# SHARIAH_UNIVERSE_PATH at import time and the file has to exist by then.
import local_api  # noqa: E402
import news_summarizer  # noqa: E402
from local_api import app  # noqa: E402
from shariah_screen_store import record_shariah_screen  # noqa: E402


ARTICLES = [
    {
        "id": 1,
        "headline": "Chevron reports higher quarterly production",
        "summary": "Chevron Corporation said output rose in the quarter.",
        "source": "benzinga",
        "url": "https://example.com/cvx",
        "created_at": "2026-08-23T14:00:00Z",
        "symbols": ["CVX"],
    },
    {
        "id": 2,
        "headline": "Apple opens a new campus",
        "summary": "Apple Inc. announced a new site.",
        "source": "benzinga",
        "url": "https://example.com/aapl",
        "created_at": "2026-08-23T13:00:00Z",
        "symbols": ["AAPL"],
    },
]


class FakeNews:
    """Records the symbols and limit fetch_news was actually called with."""

    def __init__(self, articles=None):
        self.calls = []
        self.articles = ARTICLES if articles is None else articles

    def __call__(self, symbols, *, limit=20):
        self.calls.append({"symbols": list(symbols), "limit": limit})
        return {
            "news": [dict(article) for article in self.articles],
            "next_page_token": None,
        }


def check_route_is_listed(client: TestClient) -> None:
    routes = client.get("/").json()["routes"]
    assert "/news" in routes, routes


def check_explicit_symbols_pass_through(client: TestClient) -> None:
    fake = FakeNews()
    local_api.fetch_news = fake
    response = client.get("/news?symbols=CVX,AAPL&limit=7")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert fake.calls == [{"symbols": ["CVX", "AAPL"], "limit": 7}], fake.calls
    assert len(payload["news"]) == 2, payload
    assert payload["news"][0]["headline"] == ARTICLES[0]["headline"], payload
    # No OpenRouter key configured, so the AI layer is absent by construction --
    # the key is omitted entirely, not present-and-null.
    assert all("ai_summary" not in article for article in payload["news"]), payload

    # Whitespace and empty segments are cleaned, not forwarded as symbols.
    fake.calls.clear()
    client.get("/news?symbols=%20CVX%20,,%20AAPL")
    assert fake.calls == [{"symbols": ["CVX", "AAPL"], "limit": 20}], fake.calls


def check_blank_symbols_resolve_to_watchlist_union_positions(client: TestClient) -> None:
    fake = FakeNews()
    local_api.fetch_news = fake
    original_watchlist = local_api.get_watchlist_settings
    original_portfolio = local_api.portfolio_snapshot_with_exposure
    try:
        local_api.get_watchlist_settings = lambda connection: {"symbols": ["MSFT", "CVX"]}
        local_api.portfolio_snapshot_with_exposure = lambda connection: {
            "positions": [{"symbol": "CVX"}, {"symbol": "AAPL"}]
        }
        response = client.get("/news")
        assert response.status_code == 200, response.text
        # Sorted union, CVX de-duplicated across both sources.
        assert fake.calls == [{"symbols": ["AAPL", "CVX", "MSFT"], "limit": 20}], fake.calls
    finally:
        local_api.get_watchlist_settings = original_watchlist
        local_api.portfolio_snapshot_with_exposure = original_portfolio


def check_summarizer_failure_never_500s(client: TestClient) -> None:
    """The endpoint does not trust attach_ai_summaries' no-raise promise."""
    fake = FakeNews()
    local_api.fetch_news = fake
    original = local_api.attach_ai_summaries
    try:

        def exploding(articles, **kwargs):
            raise RuntimeError("summarizer on fire")

        local_api.attach_ai_summaries = exploding
        response = client.get("/news?symbols=CVX")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["news"]) == 2, payload
        assert payload["news"][0]["summary"] == ARTICLES[0]["summary"], payload
        assert all("ai_summary" not in article for article in payload["news"]), payload
    finally:
        local_api.attach_ai_summaries = original


def check_summaries_are_attached_when_configured(client: TestClient) -> None:
    """With a key and a stubbed model, the block appears -- and only on the budget."""
    fake = FakeNews()
    local_api.fetch_news = fake
    original_request = news_summarizer.openrouter_request
    saved_key = os.environ.get("OPENROUTER_API_KEY")
    try:
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        os.environ["NEWS_AI_SUMMARY_MAX_ARTICLES"] = "1"
        news_summarizer._SUMMARY_CACHE.clear()
        news_summarizer.openrouter_request = lambda payload, *, api_key: {
            "ok": True,
            "status_code": 200,
            "data": {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "Chevron said quarterly output rose. This app's existing "
                                "screen already records CVX as COMPLIANT."
                            )
                        }
                    }
                ]
            },
        }
        response = client.get("/news?symbols=CVX,AAPL")
        assert response.status_code == 200, response.text
        articles = response.json()["news"]
        assert "ai_summary" in articles[0], articles[0]
        assert articles[0]["ai_summary"]["text"].startswith("Chevron said"), articles[0]
        # Budget of 1: the second article is untouched, and still carries
        # Alpaca's own summary.
        assert "ai_summary" not in articles[1], articles[1]
        assert articles[1]["summary"] == ARTICLES[1]["summary"], articles[1]
    finally:
        news_summarizer.openrouter_request = original_request
        news_summarizer._SUMMARY_CACHE.clear()
        os.environ.pop("NEWS_AI_SUMMARY_MAX_ARTICLES", None)
        os.environ["OPENROUTER_API_KEY"] = saved_key if saved_key is not None else ""


def check_recorded_verdict_reaches_the_summary(client: TestClient) -> None:
    """The already-recorded Shariah verdict must reach the model and the badge.

    This is what the endpoint's deviation from PORTFOLIO_HISTORY_AND_NEWS_CONTRACT.md
    buys: the contract sketch closes the database before fetching news, which
    would leave the summarizer unable to read any verdict at all. Without this
    assertion, closing early looks identical to keeping it open.
    """
    fake = FakeNews(articles=[dict(ARTICLES[0])])
    local_api.fetch_news = fake
    original_request = news_summarizer.openrouter_request
    saved_key = os.environ.get("OPENROUTER_API_KEY")
    seen = []
    try:
        connection = sqlite3.connect(local_api.DB_PATH)
        connection.row_factory = sqlite3.Row
        try:
            record_shariah_screen(
                connection,
                {
                    "symbol": "CVX",
                    "status": "COMPLIANT",
                    "reason": "debt 12.4% and cash 3.1% are both under the 33% limit",
                    "provider": "SEC_EDGAR",
                },
            )
        finally:
            connection.close()

        os.environ["OPENROUTER_API_KEY"] = "test-key"
        news_summarizer._SUMMARY_CACHE.clear()

        def capturing(payload, *, api_key):
            seen.append(payload["messages"][1]["content"])
            return {
                "ok": True,
                "status_code": 200,
                "data": {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    "Chevron said output rose. This app's existing "
                                    "screen records CVX as compliant."
                                )
                            }
                        }
                    ]
                },
            }

        news_summarizer.openrouter_request = capturing
        response = client.get("/news?symbols=CVX")
        assert response.status_code == 200, response.text
        article = response.json()["news"][0]

        # The stored verdict reached the prompt as already-decided background...
        assert seen, "the model was never called"
        assert "COMPLIANT" in seen[0], seen[0]
        assert "ALREADY DECIDED" in seen[0], seen[0]
        assert "under the 33% limit" in seen[0], seen[0]
        # ...and came back out for the dashboard's compliance badge.
        assert article["ai_summary"]["shariah_status"] == "COMPLIANT", article
        assert article["ai_summary"]["shariah_tradeable"] is True, article
    finally:
        news_summarizer.openrouter_request = original_request
        news_summarizer._SUMMARY_CACHE.clear()
        os.environ["OPENROUTER_API_KEY"] = saved_key if saved_key is not None else ""


def check_advisory_output_never_reaches_the_response(client: TestClient) -> None:
    """End to end: a model that advises produces an article with no AI block.

    The unit test covers the filter directly. This asserts the filter is actually
    wired into the path a browser hits, not merely present in the module.
    """
    fake = FakeNews()
    local_api.fetch_news = fake
    original_request = news_summarizer.openrouter_request
    saved_key = os.environ.get("OPENROUTER_API_KEY")
    try:
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        news_summarizer._SUMMARY_CACHE.clear()
        news_summarizer.openrouter_request = lambda payload, *, api_key: {
            "ok": True,
            "status_code": 200,
            "data": {
                "choices": [{"message": {"content": "CVX looks undervalued -- a clear buy here."}}]
            },
        }
        response = client.get("/news?symbols=CVX")
        assert response.status_code == 200, response.text
        articles = response.json()["news"]
        assert all("ai_summary" not in article for article in articles), articles
        body = response.text.lower()
        for word in ["undervalued", "a clear buy"]:
            assert word not in body, f"advisory text reached the HTTP response: {word}"
    finally:
        news_summarizer.openrouter_request = original_request
        news_summarizer._SUMMARY_CACHE.clear()
        os.environ["OPENROUTER_API_KEY"] = saved_key if saved_key is not None else ""


def main() -> None:
    local_api.DB_PATH = Path(fixture_dir.name) / "paper_trading.db"
    client = TestClient(app)
    original_fetch_news = local_api.fetch_news
    original_explain = news_summarizer.explain_symbol
    # _lookup_verdict falls back to a live explain_symbol -- and therefore a live
    # SEC fetch -- for any symbol with no screen on record. Stubbed for the whole
    # run so nothing in this file can reach the network, per CLAUDE.md's testing
    # conventions.
    news_summarizer.explain_symbol = lambda symbol: {
        "verdict": {"status": "COMPLIANT", "tradeable": True, "statement": "within limits"}
    }
    try:
        check_route_is_listed(client)
        check_explicit_symbols_pass_through(client)
        check_blank_symbols_resolve_to_watchlist_union_positions(client)
        check_summarizer_failure_never_500s(client)
        check_summaries_are_attached_when_configured(client)
        check_recorded_verdict_reaches_the_summary(client)
        check_advisory_output_never_reaches_the_response(client)
    finally:
        local_api.fetch_news = original_fetch_news
        news_summarizer.explain_symbol = original_explain

    print("PASS: /news serves Alpaca articles, and the AI layer is strictly additive.")


if __name__ == "__main__":
    import auth
    try:
        from local_api import app as _my_app, get_owner_actor as _get_owner_actor
    except ImportError:
        import local_api
        _my_app = local_api.app
        _get_owner_actor = local_api.get_owner_actor
    _my_app.dependency_overrides[_get_owner_actor] = lambda: auth.Actor(username='project_owner', role='admin')
    try:
        main()
    finally:
        _my_app.dependency_overrides.pop(_get_owner_actor, None)
