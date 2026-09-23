"""Table-driven coverage of the prose admission filter.

The direct analogue of test_news_summarizer.py, pointed at the bridge. Where
test_bridge_formatter_outranks_prose.py proves the three layers are each load-bearing,
this one walks the vocabulary, the polarity cases and the numeric rule in detail --
including the real-world failure shape that motivated the polarity check in the first
place: a model restating the verdict of a *different* ticker mentioned in the same block.
"""

import pytest
from bridge.filters import (
    numbers_are_contained,
    polarity_is_faithful,
    prose_is_admissible,
)

RENDERED = """[Shariah] 4197
  Verdict       PASS
  Reason        authoritative_compliant
  Source        SC publication sc-sac-my-2026-05-29 (2026-05-29)
[Quant] 4197
  Signal        BUY
  Price         2.47
  Bars          312"""

PASS_SLOT = {"allowed": True, "must_mention": ["4197"], "polarity": "PASS"}
REJECT_SLOT = {"allowed": True, "must_mention": ["0026"], "polarity": "REJECT"}
NO_POLARITY_SLOT = {"allowed": True, "must_mention": [], "polarity": None}


# --- Layer 1: vocabulary -------------------------------------------------------------

FORBIDDEN_EXAMPLES = [
    "4197 is compliant and worth buying.",
    "4197 is compliant; I sold the position.",
    "4197 is compliant, a solid investment.",
    "4197 is compliant and I recommend it.",
    "4197 is compliant so you should act.",
    "4197 is compliant and clearly undervalued.",
    "4197 is compliant with strong upside.",
    "4197 is compliant and bullish.",
    "4197 is compliant -- a good time to act.",
    # Specific to a bot sitting in front of an approval button.
    "4197 is compliant and I approved it.",
    "4197 is compliant and the order executed.",
    "4197 is compliant and I submitted the order.",
    "4197 is compliant, guaranteed.",
    "4197 is compliant and certified.",
    "4197 is halal.",
    # CLAUDE.md forbids coining this term.
    "4197 is compliant on this Shariah-aware platform.",
    "This is a Shariah-compliant system, and 4197 passes.",
]


@pytest.mark.parametrize("prose", FORBIDDEN_EXAMPLES)
def test_forbidden_vocabulary_is_refused(prose):
    verdict = prose_is_admissible(prose, rendered=RENDERED, prose_slot=PASS_SLOT)
    assert verdict["status"] == "REJECT", prose
    assert verdict["reason"] == "forbidden_term", (prose, verdict)


def test_a_faithful_restatement_with_no_forbidden_word_is_admitted():
    prose = "4197 is recorded as compliant under the publication dated 2026-05-29."
    assert prose_is_admissible(prose, rendered=RENDERED, prose_slot=PASS_SLOT)["status"] == "PASS"


# --- Layer 2: polarity faithfulness --------------------------------------------------


def test_the_right_ticker_with_the_right_polarity_passes():
    assert polarity_is_faithful("4197 is compliant", must_mention=["4197"], polarity="PASS")
    assert polarity_is_faithful("0026 is non-compliant", must_mention=["0026"], polarity="REJECT")


def test_an_inverted_polarity_is_refused():
    assert not polarity_is_faithful("4197 is non-compliant", must_mention=["4197"], polarity="PASS")
    assert not polarity_is_faithful("0026 is compliant", must_mention=["0026"], polarity="REJECT")


def test_the_wrong_ticker_is_refused():
    """The real-world shape: a verdict restated for a company never screened.

    news_summarizer records a live run where the model was handed "NON_COMPLIANT for JPM"
    on a multi-symbol article about Tesla and returned "this app's screen found Tesla
    compliant" -- fabricated, inverted, and attributed to the app. No forbidden word
    appears in that sentence, which is precisely why a word filter cannot see it.
    """
    assert not polarity_is_faithful("TSLA is compliant", must_mention=["4197"], polarity="PASS")


def test_the_underscore_spelling_is_matched():
    """NON_COMPLIANT is the exact token the backend emits, so it is what a model echoes.

    `_` is a word character, so a naive \\bnon[-\\s]?compliant\\b misses it while
    \\bcompliant\\b also misses inside it -- a first draft of this regex in
    news_summarizer silently dropped every correct NON_COMPLIANT restatement.
    """
    assert polarity_is_faithful("0026 is NON_COMPLIANT", must_mention=["0026"], polarity="REJECT")
    assert not polarity_is_faithful("4197 is NON_COMPLIANT", must_mention=["4197"], polarity="PASS")


def test_claiming_both_polarities_at_once_is_refused():
    prose = "4197 is compliant, though some sources call it non-compliant"
    assert not polarity_is_faithful(prose, must_mention=["4197"], polarity="PASS")


def test_no_polarity_supplied_means_no_compliance_claim_may_be_made():
    """Inventing a status where none was given is fabrication by another route."""
    assert polarity_is_faithful("The signal is BUY on 312 bars", must_mention=[], polarity=None)
    assert not polarity_is_faithful("4197 is compliant", must_mention=[], polarity=None)
    assert not polarity_is_faithful("4197 is non-compliant", must_mention=[], polarity=None)


def test_an_unknown_verdict_has_no_polarity_to_restate():
    for status in ("UNKNOWN", "ERROR", ""):
        assert not polarity_is_faithful("4197 is compliant", must_mention=["4197"], polarity=status)
        assert polarity_is_faithful("No verdict is recorded", must_mention=[], polarity=status)


def test_a_reject_slot_refuses_prose_that_reads_as_clearance():
    prose = "0026 passes the screen."
    verdict = prose_is_admissible(prose, rendered=RENDERED, prose_slot=REJECT_SLOT)
    assert verdict["reason"] == "unfaithful_verdict_restatement"


# --- Layer 3: numeric containment ----------------------------------------------------


def test_numbers_present_in_the_facts_are_allowed():
    assert numbers_are_contained("the price was 2.47 across 312 bars", RENDERED)


def test_an_invented_number_is_refused():
    assert not numbers_are_contained("the price was 2.48", RENDERED)
    assert not numbers_are_contained("across 313 bars", RENDERED)


def test_formatting_differences_are_not_treated_as_invention():
    """1.50 and 1.5 are the same quantity; a filter that disagrees is just noise."""
    rendered = "  Price         1.50\n  Notional      1,250"
    assert numbers_are_contained("the price was 1.5", rendered)
    assert numbers_are_contained("notional 1250", rendered)


def test_small_counting_numbers_are_allowed_as_prose():
    """A tiny allowance so ordinary sentences are possible. Every entry is a hole."""
    assert numbers_are_contained("there are 2 findings", "")
    assert not numbers_are_contained("there are 47 findings", "")


@pytest.mark.parametrize(
    "prose",
    [
        "4197 is compliant with 47% headroom.",
        "4197 is compliant; queue 14 is ready.",
        "4197 is compliant at a strike of 305.",
        "4197 is compliant and the deadline is in 87 days.",
    ],
)
def test_hallucinated_figures_are_refused(prose):
    """The numbers this surface carries are actionable, unlike a news adjective."""
    verdict = prose_is_admissible(prose, rendered=RENDERED, prose_slot=PASS_SLOT)
    assert verdict["status"] == "REJECT", prose
    assert verdict["reason"] == "number_not_in_facts", (prose, verdict)


# --- The decision wrapper ------------------------------------------------------------


def test_every_failure_mode_returns_a_reason_not_an_exception():
    cases = [None, "", "   ", "buy it", "TSLA is compliant", "the price was 9.99"]
    for prose in cases:
        verdict = prose_is_admissible(prose, rendered=RENDERED, prose_slot=PASS_SLOT)
        assert verdict["status"] in {"PASS", "REJECT"}
        assert verdict["reason"]


def test_a_slot_that_forbids_prose_refuses_even_faithful_text():
    slot = {"allowed": False, "must_mention": ["4197"], "polarity": "PASS"}
    prose = "4197 is recorded as compliant."
    verdict = prose_is_admissible(prose, rendered=RENDERED, prose_slot=slot)
    assert verdict["reason"] == "prose_not_allowed_for_this_block"


def test_the_filter_fails_closed_on_a_missing_slot():
    """No slot means no verdict was supplied, so no compliance claim may be made."""
    assert prose_is_admissible("4197 is compliant", rendered=RENDERED)["status"] == "REJECT"
    assert prose_is_admissible("The bar count is 312", rendered=RENDERED)["status"] == "PASS"


# --- The one vocabulary exemption ----------------------------------------------------
#
# Found by running the real bridge against the live deployment, not by reasoning: the
# backend emits signals as the tokens BUY and SELL, and a blanket ban on "buy" made the
# quant bot unable to report the one thing it exists for. The exemption is narrow, and
# these tests are what stop it widening.

SIGNAL_RENDERED = """[Quant] 4197
  Signal        BUY
  Price         2.47
  Bars          312"""

SIGNAL_SLOT = {"allowed": True, "must_mention": [], "polarity": None}


def test_an_uppercase_signal_token_quoted_from_the_facts_is_allowed():
    prose = "The recorded signal is BUY, computed from 312 bars."
    assert (
        prose_is_admissible(prose, rendered=SIGNAL_RENDERED, prose_slot=SIGNAL_SLOT)["status"]
        == "PASS"
    )


def test_the_lowercase_word_is_still_advice_and_still_refused():
    """The case distinction IS the exemption. Widening it to lowercase removes the rule."""
    prose = "The signal suggests you buy now."
    assert (
        prose_is_admissible(prose, rendered=SIGNAL_RENDERED, prose_slot=SIGNAL_SLOT)["status"]
        == "REJECT"
    )


def test_an_uppercase_token_absent_from_the_facts_is_refused():
    """Quoting only works if there is something to quote."""
    rendered = "[Quant] 4197\n  Signal        NO_SIGNAL"
    prose = "The recorded signal is BUY."
    verdict = prose_is_admissible(prose, rendered=rendered, prose_slot=SIGNAL_SLOT)
    assert verdict["status"] == "REJECT"
    assert verdict["term"] == "BUY"


def test_the_exemption_does_not_extend_to_inflected_forms():
    """SELL is a token the backend emits; SELLING is not, however it is capitalised."""
    rendered = "[Quant] 4197\n  Signal        SELL"
    assert (
        prose_is_admissible("The signal is SELL.", rendered=rendered, prose_slot=SIGNAL_SLOT)[
            "status"
        ]
        == "PASS"
    )
    assert (
        prose_is_admissible("I am SELLING it.", rendered=rendered, prose_slot=SIGNAL_SLOT)["status"]
        == "REJECT"
    )


def test_the_exemption_never_licenses_a_claim_to_have_acted():
    """APPROVED and EXECUTED must stay banned even if they appear in an audit block."""
    rendered = "[Approvals]\n  #14 4197 SELL x1 -> APPROVED_PAPER_READY / BROKER_SUBMITTED"
    for prose in ("I APPROVED it.", "The order EXECUTED.", "I approved it."):
        assert (
            prose_is_admissible(prose, rendered=rendered, prose_slot=SIGNAL_SLOT)["status"]
            == "REJECT"
        ), prose
