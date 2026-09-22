"""The Trading Mandate must describe the system, not make claims about it.

A hand-written mandate is a claim. A generated one is a description. The whole
justification for generating it is that it cannot quietly drift away from what
the code enforces -- so these tests check that the numbers really are read from
live configuration, and that the document never starts asserting more than the
project can defend.

Background: a Shariah expert reviewing this project held that clicking "approve"
does not by itself establish niyyah/qasd, and that what matters is whether the
human knowingly authorized the mandate, parameters and transaction. This document
is that authorization. It is also the artifact most likely to be read by someone
who is not a programmer, which is why the claim-language test below is not
cosmetic.
"""

from dataclasses import replace

import config
import trading_mandate


def _settings():
    return config.load_settings()


def test_every_risk_limit_comes_from_live_config():
    settings = _settings()
    mandate = trading_mandate.build_mandate(settings)

    for value in (
        settings.max_position_pct,
        settings.max_total_exposure_pct,
        settings.max_sector_exposure_pct,
        settings.max_loss_per_trade_pct,
        settings.max_daily_loss_pct,
        settings.max_weekly_loss_pct,
    ):
        assert f"{value:g}%" in mandate, f"{value:g}% is enforced but absent from the mandate"

    assert str(settings.max_orders_per_day) in mandate
    assert settings.trading_mode in mandate
    assert settings.paper_execution_adapter in mandate
    print("PASS: every risk limit in the mandate comes from live config")


def test_the_mandate_tracks_a_changed_limit():
    """Proves the numbers are read, not transcribed.

    If someone tightens a limit without regenerating, the mandate would be a
    false statement about the system. This is the test that makes 'generated'
    mean something.
    """
    settings = _settings()
    tightened = replace(settings, max_position_pct=1.25, max_orders_per_day=3)

    original = trading_mandate.build_mandate(settings)
    changed = trading_mandate.build_mandate(tightened)

    assert "1.25%" in changed, "a changed position limit did not reach the mandate"
    assert "1.25%" not in original, "the fixture value leaked into the unchanged mandate"
    assert changed != original
    print("PASS: the mandate tracks a changed limit rather than transcribing one")


def test_the_mandate_never_claims_shariah_certification():
    """Guards the exact wording a Shariah reviewer objected to.

    He was explicit on two points: being on the SC list does not certify an
    automated trading system, and a systematic strategy is not outside maysir
    merely by being systematic. Neither claim may appear here.
    """
    mandate = trading_mandate.build_mandate(_settings())
    lowered = mandate.lower()

    forbidden = [
        "this system is shariah-compliant",
        "certified shariah",
        "shariah-certified",
        "shariah-aware",
        "guarantees shariah",
    ]
    for phrase in forbidden:
        assert phrase not in lowered, f"the mandate asserts more than it can defend: {phrase!r}"

    # And it must carry the disclaimers positively, not merely avoid the claim.
    assert "does not certify shariah compliance" in lowered
    assert "has not been validated by a qualified shariah scholar" in lowered
    assert "does not, by itself, constitute *maysir*" in lowered
    print("PASS: the mandate disclaims certification rather than implying it")


def test_the_mandate_states_the_non_negotiables():
    """The parts that make it an authorization rather than a description."""
    mandate = trading_mandate.build_mandate(_settings())

    assert "EXECUTE PAPER" in mandate, "the confirmation phrase must be stated"
    assert "UNKNOWN" in mandate and "Blocks trading" in mandate
    assert "Short selling" in mandate and "Margin / leverage" in mandate
    assert "Synthetic or unverifiable prices" in mandate
    assert "append-only evidence trail" in mandate
    assert "Authorized by:" in mandate, "an unsigned mandate authorizes nothing"
    print("PASS: the mandate states the confirmation phrase, exclusions and signature block")


def main():
    test_every_risk_limit_comes_from_live_config()
    test_the_mandate_tracks_a_changed_limit()
    test_the_mandate_never_claims_shariah_certification()
    test_the_mandate_states_the_non_negotiables()
    print()
    print("All trading-mandate tests passed.")


if __name__ == "__main__":
    main()
