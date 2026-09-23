"""Facts outrank prose, and the filters are mutation-checked to prove it.

The property the owner actually asked for: a model may explain a decision and may never
author one. `compose.py` guarantees the ordering; `filters.py` guarantees the prose is not
lying; this file proves both against an adversary rather than a cooperative model.

The mutation check at the bottom is the part that matters. CLAUDE.md: "When a test passes
on the first run, break the code deliberately and confirm the test fails." Here that is
built in, three ways:

* each layer is disabled in turn and the example only *it* catches must then be admitted,
  proving that layer is load-bearing rather than decorative;
* the fluent multiply-wrong completion must survive any single layer being disabled,
  proving the three are defence in depth rather than three spellings of one check;
* with all three disabled it must be admitted, which is the control -- without it the
  suite could be passing because of something else entirely.
"""

import re

import pytest
from bridge import filters
from bridge.compose import (
    DISAGREEMENT_HEADER,
    NARRATION_HEADER,
    compose_from_envelopes,
    compose_message,
)

SHARIAH_BLOCK = """[Shariah] 4197
  Verdict       PASS
  Reason        authoritative_compliant
  Issuer        Sime Darby Bhd
  Source        SC publication sc-sac-my-2026-05-29 (2026-05-29)
  Document      d6592a55f54fd113d90945fd1d0b534a4a1b6451a7877ca4103ec7846799db85"""

RISK_BLOCK = """[Risk]
  Orders today  2
  Daily loss    UNBOUNDED (fail-closed: treat as blocking)
  Status        BLOCKING: daily_loss_pct is unbounded (fail-closed)"""

SLOT = {"allowed": True, "must_mention": ["4197"], "polarity": "PASS"}

# What a model actually reaches for: fluent, confident, and wrong in three separate ways.
ADVERSARIAL = (
    "I checked 4197 and it is compliant, so I approved and executed the order at 3.85 "
    "-- a good time to buy given the 47% upside."
)

FAITHFUL = "4197 is recorded as compliant under the SC publication dated 2026-05-29."


def test_the_adversarial_completion_is_dropped_entirely():
    result = compose_message([SHARIAH_BLOCK], ADVERSARIAL, prose_slot=SLOT)

    assert result["prose_included"] is False
    assert NARRATION_HEADER not in result["message"]
    # Not one word of it may survive.
    for phrase in ("approved", "executed", "good time", "upside", "47"):
        assert phrase not in result["message"], phrase


def test_the_facts_survive_even_when_the_prose_is_dropped():
    result = compose_message([SHARIAH_BLOCK], ADVERSARIAL, prose_slot=SLOT)
    for line in SHARIAH_BLOCK.splitlines():
        assert line in result["message"], line
    # The document hash is the whole traceability claim; it must never be a casualty.
    assert "d6592a55f54fd113d90945fd1d0b534a4a1b6451a7877ca4103ec7846799db85" in result["message"]


def test_a_withheld_narration_is_announced_not_hidden():
    """A silently shorter message hides that the model tried to say something refused."""
    result = compose_message([SHARIAH_BLOCK], ADVERSARIAL, prose_slot=SLOT)
    assert "narration withheld" in result["message"]
    assert result["prose_rejection"] == "forbidden_term"


def test_faithful_prose_is_admitted_and_placed_last():
    result = compose_message([SHARIAH_BLOCK], FAITHFUL, prose_slot=SLOT)
    assert result["prose_included"] is True
    message = result["message"]
    assert message.index(SHARIAH_BLOCK) < message.index(NARRATION_HEADER)
    assert message.index(NARRATION_HEADER) < message.index(FAITHFUL)
    assert "authoritative" in NARRATION_HEADER


def test_prose_can_never_appear_before_the_facts():
    result = compose_message([SHARIAH_BLOCK, RISK_BLOCK], FAITHFUL, prose_slot=SLOT)
    message = result["message"]
    if result["prose_included"]:
        assert message.index(RISK_BLOCK.splitlines()[0]) < message.index(NARRATION_HEADER)


def test_disagreeing_blocks_are_both_shown_and_not_reconciled():
    """A compliance PASS beside a BLOCKING risk verdict is information, not noise."""
    result = compose_message([SHARIAH_BLOCK, RISK_BLOCK], None)
    assert DISAGREEMENT_HEADER in result["message"]
    assert "Verdict       PASS" in result["message"]
    assert "BLOCKING" in result["message"]
    # Nothing may summarise them away.
    assert "on balance" not in result["message"].lower()
    assert "overall" not in result["message"].lower()


def test_agreeing_blocks_get_no_disagreement_banner():
    result = compose_message([SHARIAH_BLOCK, SHARIAH_BLOCK], None)
    assert DISAGREEMENT_HEADER not in result["message"]


def test_an_envelope_that_forbids_prose_wins_over_one_that_allows_it():
    """Narrating an unavailable answer is exactly when a model invents one."""
    envelopes = [
        {"render": SHARIAH_BLOCK, "prose_slot": SLOT},
        {
            "render": "[Risk] unavailable: http_503. No verdict can be reported.",
            "prose_slot": {"allowed": False, "must_mention": [], "polarity": None},
        },
    ]
    result = compose_from_envelopes(envelopes, FAITHFUL)
    assert result["prose_included"] is False
    assert result["prose_rejection"] == "prose_not_allowed_for_this_block"


def test_no_blocks_is_reported_rather_than_narrated():
    result = compose_message([], FAITHFUL, prose_slot=SLOT)
    assert result["status"] == "EMPTY"
    assert result["prose_included"] is False


# --- The mutation check: each filter must be load-bearing ---------------------------

# One example per layer, each caught by that layer ALONE -- no forbidden word in the
# polarity case, no inverted verdict in the numbers case, and so on. That is what makes
# the mutation check below meaningful rather than circular.
ONLY_VOCABULARY = "4197 is compliant, and this is a good time to act."
ONLY_POLARITY = "4197 is non-compliant according to the recorded screen."
ONLY_NUMBERS = "4197 is compliant under publication 2026-05-29, screened at 99.7% coverage."

_LAYERS = {
    "vocabulary": (ONLY_VOCABULARY, "forbidden_term"),
    "polarity": (ONLY_POLARITY, "unfaithful_verdict_restatement"),
    "numbers": (ONLY_NUMBERS, "number_not_in_facts"),
}


def _disable(monkeypatch, layer):
    if layer == "vocabulary":
        monkeypatch.setattr(filters, "_FORBIDDEN_PATTERN", re.compile(r"(?!x)x"))
    elif layer == "polarity":
        monkeypatch.setattr(filters, "polarity_is_faithful", lambda *a, **k: True)
    else:
        monkeypatch.setattr(filters, "numbers_are_contained", lambda *a, **k: True)


@pytest.mark.parametrize("layer", sorted(_LAYERS))
def test_each_layer_catches_something_the_others_miss(layer):
    """With all three active, each example is refused for that layer's own reason."""
    prose, reason = _LAYERS[layer]
    verdict = filters.prose_is_admissible(prose, rendered=SHARIAH_BLOCK, prose_slot=SLOT)
    assert verdict["status"] == "REJECT"
    assert verdict["reason"] == reason, (
        f"{layer} example was caught by a different layer; the example is not isolating "
        "what it claims to isolate"
    )


@pytest.mark.parametrize("layer", sorted(_LAYERS))
def test_disabling_a_layer_lets_its_own_example_through(monkeypatch, layer):
    """The actual mutation check: break one layer, and what only it caught gets in.

    CLAUDE.md: when a test passes on the first run, break the code and confirm it fails.
    A layer whose removal changes nothing is decorative, and this is what proves none of
    the three is.
    """
    prose, _ = _LAYERS[layer]
    _disable(monkeypatch, layer)
    verdict = filters.prose_is_admissible(prose, rendered=SHARIAH_BLOCK, prose_slot=SLOT)
    assert verdict["status"] == "PASS", (
        f"disabling the {layer} layer changed nothing -- it is not the layer catching "
        f"{prose!r}, so one of these filters is not doing the work it claims"
    )


@pytest.mark.parametrize("layer", sorted(_LAYERS))
def test_the_full_adversary_survives_any_single_layer_being_disabled(monkeypatch, layer):
    """Defence in depth: the fluent, multiply-wrong completion needs more than one layer."""
    _disable(monkeypatch, layer)
    verdict = filters.prose_is_admissible(ADVERSARIAL, rendered=SHARIAH_BLOCK, prose_slot=SLOT)
    assert verdict["status"] == "REJECT", (
        f"with {layer} disabled the remaining layers let a fabricated verdict through"
    )


def test_disabling_every_layer_admits_the_adversary(monkeypatch):
    """The control. If this still rejected, the suite would be proving nothing."""
    for layer in _LAYERS:
        _disable(monkeypatch, layer)
    verdict = filters.prose_is_admissible(ADVERSARIAL, rendered=SHARIAH_BLOCK, prose_slot=SLOT)
    assert verdict["status"] == "PASS"


def test_the_composer_uses_the_filter_rather_than_reimplementing_it(monkeypatch):
    """One decision point. If compose grew its own check, the two could disagree."""
    calls = []

    def spy(prose, *, rendered, prose_slot=None):
        calls.append(prose)
        return {"status": "REJECT", "reason": "spy"}

    import bridge.compose as composer

    monkeypatch.setattr(composer, "prose_is_admissible", spy)
    result = composer.compose_message([SHARIAH_BLOCK], FAITHFUL, prose_slot=SLOT)
    assert calls == [FAITHFUL]
    assert result["prose_included"] is False
