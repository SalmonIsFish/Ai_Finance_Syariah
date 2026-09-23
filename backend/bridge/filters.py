"""Whether a model's narration is allowed into an outgoing message.

The bridge's promise is that facts outrank prose. `compose.py` guarantees the ordering;
this module guarantees the prose is not lying. It is the enforcement half, and the point
is that it does not depend on a model cooperating -- `news_summarizer.py:16-18` puts it
exactly right: *"A prompt is a request; the filter is the enforcement, so the filter is
what the tests mutation-check."*

Deliberately biased toward over-filtering. A false positive costs one paragraph of
narration; a false negative puts an invented verdict in front of the owner in a
compliance product, with a Telegram approve button underneath it. That trade is not close.

## Three layers, each catching what the others cannot

1. **Vocabulary** -- advisory and authority-claiming words. Cheap, and blind to a fluent
   fabrication that uses none of them.
2. **Polarity faithfulness** -- did the model restate OUR verdict, for OUR ticker, with
   OUR polarity? This catches the fabrication a word filter cannot see.
3. **Numeric containment** -- every number in the prose must appear in the rendered facts.
   The strongest of the three for this surface, because the bridge's numbers are
   actionable: a hallucinated queue id, strike, deadline or loss percentage is a
   different kind of wrong from a hallucinated adjective.

## Attribution

Layers 1 and 2 are **copied** from `news_summarizer.py`, not imported.
`test_bridge_no_llm_in_path.py` forbids the bridge importing that module -- it is an LLM
call site, and the bridge must contain none. Twenty saved lines are not worth weakening
that ban. The regexes below are the same, and the `NON_COMPLIANT` underscore caveat is
preserved with them because a first draft of it in the original silently dropped every
correct NON_COMPLIANT restatement.
"""

from __future__ import annotations

import re

# --- Layer 1: vocabulary -------------------------------------------------------------

# Copied from news_summarizer._FORBIDDEN_TERMS, plus terms specific to this surface.
_FORBIDDEN_TERMS = [
    # Advisory vocabulary (news_summarizer's list).
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
    # Specific to the bridge. A bot sits in front of an approval button, so a claim to
    # have already acted is worse here than on a news card.
    r"approve(s|d)?",
    r"execute(s|d)?",
    r"submit(s|ted|ting)?",
    r"placed the order",
    r"guarantee(s|d)?",
    r"certified",
    r"halal",
    r"haram",
    # CLAUDE.md: never coin "Shariah-aware", never call the system Shariah-compliant.
    r"shariah[-\s]?aware",
    r"shariah[-\s]?compliant (system|platform|app|product)",
]
_FORBIDDEN_PATTERN = re.compile(r"\b(" + "|".join(_FORBIDDEN_TERMS) + r")\b", re.IGNORECASE)


def _forbidden_term_in(text: str, *, rendered: str) -> str | None:
    """The first advisory term used as advice, or None.

    One exemption, found by running the real thing against the live deployment: the
    backend emits signals as the tokens BUY and SELL, and the quant bot exists to report
    them. A blanket ban on "buy" made a faithful restatement of `Signal BUY` inadmissible,
    which would have left that role unable to say the one thing it is for.

    So an UPPERCASE token that actually appears in the rendered facts is allowed -- it is
    the system's own word, quoted. Lowercase "buy" is advice and stays banned, and a term
    that appears nowhere in the facts is banned in any case. The case distinction is the
    whole exemption: "Signal BUY" passes, "a good time to buy" does not.
    """
    for match in _FORBIDDEN_PATTERN.finditer(text):
        term = match.group()
        if term.isupper() and re.search(r"\b" + re.escape(term) + r"\b", rendered):
            continue
        return term
    return None


# --- Layer 2: polarity faithfulness --------------------------------------------------

# Copied from news_summarizer. The underscore form matters more than it looks:
# NON_COMPLIANT is the exact status token the backend emits, so it is the spelling a
# model echoes most often. `_` is a word character, so \bnon[-\s]?compliant\b does not
# match it and \bcompliant\b does not match inside it.
_NON_COMPLIANT_CLAIM = re.compile(
    r"\bnon[-_\s]?compliant\b|\bnoncompliant\b|\bnot\s+(?:shariah[-\s]?)?compliant\b"
    r"|\bimpermissible\b|\bnot\s+permissible\b|\breject(ed)?\b",
    re.IGNORECASE,
)
_COMPLIANT_CLAIM = re.compile(r"\bcompliant\b|\bpermissible\b|\bpass(es|ed)?\b", re.IGNORECASE)

# Backend statuses mapped onto the polarity a restatement must carry. Anything not
# listed has no polarity to restate, and a model claiming one is fabricating.
_POSITIVE = {"PASS", "COMPLIANT"}
_NEGATIVE = {"REJECT", "NON_COMPLIANT"}


def polarity_is_faithful(text: str, *, must_mention: list | None, polarity: str | None) -> bool:
    """Did the model restate OUR verdict, for OUR ticker, with OUR polarity?

    Mirrors news_summarizer._verdict_restatement_is_faithful. When no polarity was
    supplied, no compliance claim may be made at all -- inventing a status out of nowhere
    is the same fabrication by another route.
    """
    says_negative = bool(_NON_COMPLIANT_CLAIM.search(text))
    # Strip the negative phrases before looking for a bare positive, since "non-compliant"
    # contains "compliant".
    says_positive = bool(_COMPLIANT_CLAIM.search(_NON_COMPLIANT_CLAIM.sub(" ", text)))

    normalized = str(polarity or "").upper()
    if normalized not in _POSITIVE | _NEGATIVE:
        return not says_negative and not says_positive

    for token in must_mention or []:
        if not re.search(rf"\b{re.escape(str(token))}\b", text, re.IGNORECASE):
            return False

    if normalized in _POSITIVE:
        return says_positive and not says_negative
    return says_negative and not says_positive


# --- Layer 3: numeric containment ----------------------------------------------------

_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)*")

# Numbers a model may use as ordinary prose without them appearing in the facts. Kept
# tiny on purpose: every entry is a hole.
_PROSE_NUMBERS = {"0", "1", "2", "3"}


def _normalize_number(token: str) -> str:
    """Compare 1.50, 1.5 and 1,50 as the same quantity; drop thousands separators."""
    cleaned = token.replace(",", "")
    try:
        value = float(cleaned)
    except ValueError:
        return cleaned
    if value == int(value):
        return str(int(value))
    return ("%f" % value).rstrip("0").rstrip(".")


def _numbers_in(text: str) -> set[str]:
    return {_normalize_number(match.group()) for match in _NUMBER.finditer(text)}


def numbers_are_contained(text: str, rendered: str) -> bool:
    """Every number in the prose must already appear in the rendered facts.

    Cheap and strong. It kills a hallucinated risk percentage, an invented strike, a
    wrong queue id and a made-up deadline in one rule -- and unlike the vocabulary
    filter it cannot be talked around by choosing different words.
    """
    available = _numbers_in(rendered)
    for token in _numbers_in(text):
        if token in _PROSE_NUMBERS or token in available:
            continue
        return False
    return True


# --- The decision --------------------------------------------------------------------


def prose_is_admissible(
    prose: str | None, *, rendered: str, prose_slot: dict | None = None
) -> dict:
    """May this narration be attached? Returns a dict with a `status` key, failing closed.

    `rendered` is the deterministic block the prose accompanies; it defines both the
    numbers the prose may use and the verdict it may restate.
    """
    slot = prose_slot or {}
    if not prose or not prose.strip():
        return {"status": "REJECT", "reason": "empty_prose"}
    if slot.get("allowed") is False:
        return {"status": "REJECT", "reason": "prose_not_allowed_for_this_block"}

    text = prose.strip()

    offending = _forbidden_term_in(text, rendered=rendered)
    if offending:
        return {"status": "REJECT", "reason": "forbidden_term", "term": offending}

    if not polarity_is_faithful(
        text, must_mention=slot.get("must_mention"), polarity=slot.get("polarity")
    ):
        return {"status": "REJECT", "reason": "unfaithful_verdict_restatement"}

    if not numbers_are_contained(text, rendered):
        return {"status": "REJECT", "reason": "number_not_in_facts"}

    return {"status": "PASS", "reason": "admissible"}
