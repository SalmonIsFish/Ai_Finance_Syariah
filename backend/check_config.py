"""Print safe configuration status without exposing credentials."""

from config import load_settings


def main() -> None:
    settings = load_settings()
    print("Configuration valid")
    print(f"Tiingo token configured: {bool(settings.tiingo_api_token)}")
    print(f"Moomoo mode: {settings.moomoo_mode}")
    print(f"Moomoo endpoint: {settings.moomoo_host}:{settings.moomoo_port}")
    print(f"Alpaca mode: {settings.alpaca_mode}")
    print(f"Alpaca key id configured: {bool(settings.alpaca_api_key_id)}")
    print(f"Alpaca secret configured: {bool(settings.alpaca_secret_key)}")
    print(f"Paper execution adapter: {settings.paper_execution_adapter}")
    print(f"OpenRouter key configured: {bool(settings.openrouter_api_key)}")
    print(f"OpenRouter model: {settings.openrouter_model}")
    print(f"News AI summaries enabled: {settings.news_ai_summary_enabled}")
    print(f"Quant strategies: {', '.join(settings.quant_strategies)}")
    print(
        "Sector exposure limit: "
        + (
            f"{settings.max_sector_exposure_pct}%"
            if settings.max_sector_exposure_pct
            else "disabled"
        )
    )


if __name__ == "__main__":
    main()
