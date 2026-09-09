"""Local configuration for the read-only/paper backend.

Credentials are loaded from backend/.env and are never printed.
"""

from dataclasses import dataclass
from pathlib import Path
import os

BACKEND_DIR = Path(__file__).resolve().parent
REPO_ROOT = BACKEND_DIR.parent

# Committed defaults so a fresh clone runs without any local vault. Both can be pointed
# at a larger private research vault through backend/.env.
DEFAULT_SHARIAH_WIKI_PATH = REPO_ROOT / "docs" / "shariah-policy"
DEFAULT_SHARIAH_UNIVERSE_PATH = REPO_ROOT / "data" / "shariah-universe" / "2026-05-29.json"


def _load_local_env() -> None:
    """Load simple KEY=value entries without requiring a third-party package."""
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()


@dataclass(frozen=True)
class Settings:
    tiingo_api_token: str | None
    alpaca_api_key_id: str | None
    alpaca_secret_key: str | None
    alpaca_mode: str
    alpaca_data_feed: str | None
    market_data_provider: str
    shariah_universe_path: str | None
    shariah_wiki_path: str | None
    trading_mode: str
    moomoo_mode: str
    moomoo_host: str
    moomoo_port: int
    paper_execution_enabled: bool
    paper_execution_adapter: str
    paper_account_equity: float
    max_position_pct: float
    max_total_exposure_pct: float
    max_loss_per_trade_pct: float
    max_daily_loss_pct: float
    max_weekly_loss_pct: float
    max_orders_per_day: int
    openrouter_api_key: str | None
    openrouter_model: str
    news_ai_summary_enabled: bool
    news_ai_summary_max_articles: int
    news_ai_summary_cache_ttl_minutes: float
    quant_strategies: list[str]
    max_sector_exposure_pct: float


# Origins the API answers cross-origin requests from. The deployed instance serves
# the dashboard from its own origin, so it needs none of these -- it sets
# ALLOWED_ORIGINS to just itself. The default is the local-development set: "null"
# is what a browser sends for a dashboard/index.html opened straight off disk via
# file://, and the rest are the usual local static servers.
DEFAULT_ALLOWED_ORIGINS = (
    "null,http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500"
)


def allowed_origins() -> list[str]:
    """Parse ALLOWED_ORIGINS into a list for CORSMiddleware.

    Read here rather than on Settings so that a malformed risk limit elsewhere in
    load_settings() cannot stop the API from booting with correct CORS -- this is
    consulted once, at import, before any request is served.
    """
    raw = os.getenv("ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["null"]


def _float_env(name: str, default: str, *, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _int_env(name: str, default: str, *, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _path_env(name: str, default: Path) -> str | None:
    """Env var wins; otherwise use the committed default when it actually exists."""
    configured = os.getenv(name)
    if configured:
        return configured
    return str(default) if default.exists() else None


def load_settings() -> Settings:
    trading_mode = os.getenv("TRADING_MODE", "approval").strip().lower()
    if trading_mode not in {"advisory", "approval", "autonomous_paper"}:
        raise ValueError("TRADING_MODE must be advisory, approval, or autonomous_paper")

    mode = os.getenv("MOOMOO_MODE", "paper").strip().lower()
    if mode != "paper":
        raise ValueError("MOOMOO_MODE must remain 'paper'; live mode is disabled")

    alpaca_mode = os.getenv("ALPACA_MODE", "paper").strip().lower()
    if alpaca_mode != "paper":
        raise ValueError("ALPACA_MODE must remain 'paper'; live mode is disabled")

    market_data_provider = os.getenv("MARKET_DATA_PROVIDER", "alpaca").strip().lower()
    if market_data_provider not in {"alpaca", "tiingo", "yahoo"}:
        raise ValueError("MARKET_DATA_PROVIDER must be alpaca, tiingo, or yahoo")

    try:
        port = int(os.getenv("MOOMOO_PORT", "11111"))
    except ValueError as exc:
        raise ValueError("MOOMOO_PORT must be an integer") from exc
    paper_execution_enabled = (
        os.getenv("PAPER_EXECUTION_ENABLED", "false").strip().lower() == "true"
    )
    paper_account_equity = _float_env("PAPER_ACCOUNT_EQUITY", "10000", minimum=0.01)
    max_position_pct = _float_env("MAX_POSITION_PCT", "5.0", minimum=0)
    max_total_exposure_pct = _float_env("MAX_TOTAL_EXPOSURE_PCT", "25.0", minimum=0)
    max_loss_per_trade_pct = _float_env("MAX_LOSS_PER_TRADE_PCT", "0.5", minimum=0)
    max_daily_loss_pct = _float_env("MAX_DAILY_LOSS_PCT", "1.0", minimum=0)
    # 2% per the Obsidian vault's risk-policy.md ("Maximum weekly realised
    # loss (% of portfolio)"). Not invented -- see local_api.py's
    # _start_of_iso_week_utc for the calendar-week-vs-rolling-7-day
    # interpretation this limit is checked against.
    max_weekly_loss_pct = _float_env("MAX_WEEKLY_LOSS_PCT", "2.0", minimum=0)
    max_orders_per_day = _int_env("MAX_ORDERS_PER_DAY", "5", minimum=1)
    max_sector_exposure_pct = _float_env("MAX_SECTOR_EXPOSURE_PCT", "20.0", minimum=0)
    news_ai_summary_enabled = os.getenv("NEWS_AI_SUMMARY_ENABLED", "true").strip().lower() == "true"
    news_ai_summary_max_articles = _int_env("NEWS_AI_SUMMARY_MAX_ARTICLES", "5", minimum=0)
    news_ai_summary_cache_ttl_minutes = _float_env(
        "NEWS_AI_SUMMARY_CACHE_TTL_MINUTES", "60", minimum=0
    )
    quant_strategies = [
        item.strip().upper()
        for item in os.getenv("QUANT_STRATEGIES", "").split(",")
        if item.strip()
    ] or ["S001", "S002"]

    return Settings(
        tiingo_api_token=os.getenv("TIINGO_API_TOKEN") or None,
        alpaca_api_key_id=os.getenv("ALPACA_API_KEY_ID") or None,
        alpaca_secret_key=os.getenv("ALPACA_SECRET_KEY") or None,
        alpaca_mode=alpaca_mode,
        alpaca_data_feed=os.getenv("ALPACA_DATA_FEED") or None,
        market_data_provider=market_data_provider,
        shariah_universe_path=_path_env("SHARIAH_UNIVERSE_PATH", DEFAULT_SHARIAH_UNIVERSE_PATH),
        shariah_wiki_path=_path_env("SHARIAH_WIKI_PATH", DEFAULT_SHARIAH_WIKI_PATH),
        trading_mode=trading_mode,
        moomoo_mode=mode,
        moomoo_host=os.getenv("MOOMOO_HOST", "127.0.0.1"),
        moomoo_port=port,
        paper_execution_enabled=paper_execution_enabled,
        paper_execution_adapter=os.getenv("PAPER_EXECUTION_ADAPTER", "disabled").strip().lower(),
        paper_account_equity=paper_account_equity,
        max_position_pct=max_position_pct,
        max_total_exposure_pct=max_total_exposure_pct,
        max_loss_per_trade_pct=max_loss_per_trade_pct,
        max_daily_loss_pct=max_daily_loss_pct,
        max_weekly_loss_pct=max_weekly_loss_pct,
        max_orders_per_day=max_orders_per_day,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-chat").strip(),
        news_ai_summary_enabled=news_ai_summary_enabled,
        news_ai_summary_max_articles=news_ai_summary_max_articles,
        news_ai_summary_cache_ttl_minutes=news_ai_summary_cache_ttl_minutes,
        quant_strategies=quant_strategies,
        max_sector_exposure_pct=max_sector_exposure_pct,
    )
