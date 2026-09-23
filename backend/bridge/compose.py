"""Assembles the outgoing message. Python writes the facts; a model may only annotate.

This is where "facts outrank prose" stops being a claim about intent and becomes a
property of the code. The message is built here, from the `render` blocks the tools
returned. A model never hands back a message -- it hands back at most one paragraph, which
goes in a single labelled slot at the end, and only if `filters.prose_is_admissible`
passes. If it fails, the slot is dropped and the message sends with facts alone.

That is deliberately the same trade `news_summarizer` makes: losing a paragraph costs
nothing; letting an invented verdict through costs the thing the product is for.

## Why the chief of staff assembles but does not arbitrate

When two specialists disagree -- a PASS verdict and a BLOCKING risk snapshot -- both
blocks are printed in full, in a fixed order, under a Python-written header saying they
disagree. Nothing reconciles them. A disagreement between a compliance verdict and a risk
verdict is information the owner needs, and a model summarising it into one sentence is a
model deciding which one mattered.

## Why there is no `assemble(blocks)` tool

The obvious way to make the chief of staff real is an MCP tool taking a list of blocks and
returning a composed message. **That would be a hole**, and a subtle one: the blocks would
arrive as strings *from the model*, and this function prints them above the line labelled
authoritative. A model that fabricated a verdict block would have it rendered as fact,
with the filter below it inspecting only the narration. The filter would pass, because the
lie was never in the narration.

Passing block *ids* instead of block text would close that -- but OpenClaw spawns one
stdio process per role, and the chief of staff has no backend tools, so it has no session
in which those ids exist. Cross-process block sharing is not worth building for this.

So composition belongs to the relay (Phase 3), which fetches the envelopes itself and
therefore knows the blocks are its own. Until then this module is a library with no
LLM-reachable entry point, which is the safe version of not-yet-finished.
"""

from __future__ import annotations

from bridge.filters import prose_is_admissible

NARRATION_HEADER = "Narration (explanatory only -- the lines above are authoritative)"

DISAGREEMENT_HEADER = (
    "These blocks disagree. Both are shown in full and unreconciled; "
    "deciding between them is yours, not the system's."
)

DROPPED_NOTE = "(narration withheld: {reason})"

# A block is "blocking" if its own deterministic text says so. Read from the rendered
# text rather than re-derived, so the banner can never disagree with what is displayed.
_BLOCKING_MARKERS = ("BLOCKING", "REJECT", "OVERDUE", "not permitted", "UNBOUNDED")
_CLEARING_MARKERS = ("Verdict       PASS", "Verdict       COMPLIANT")


def _is_blocking(block: str) -> bool:
    return any(marker in block for marker in _BLOCKING_MARKERS)


def _is_clearing(block: str) -> bool:
    return any(marker in block for marker in _CLEARING_MARKERS)


def blocks_disagree(blocks: list) -> bool:
    """True when at least one block clears and at least one blocks.

    Detected from the rendered text, so it cannot drift from what the owner sees.
    """
    return any(_is_clearing(b) for b in blocks) and any(_is_blocking(b) for b in blocks)


def compose_message(
    blocks: list,
    prose: str | None = None,
    *,
    rendered_for_prose: str | None = None,
    prose_slot: dict | None = None,
    show_disagreement: bool = True,
) -> dict:
    """Build one message. Facts first, always; narration last, conditionally.

    Returns {"status", "message", "prose_included", "prose_rejection"} so a caller can
    see *why* narration was withheld rather than silently receiving a shorter message.
    """
    ordered = [str(block).rstrip() for block in blocks if str(block or "").strip()]
    if not ordered:
        return {
            "status": "EMPTY",
            "message": "No facts were available to report.",
            "prose_included": False,
            "prose_rejection": "no_blocks",
        }

    parts = []
    if show_disagreement and blocks_disagree(ordered):
        parts.append(DISAGREEMENT_HEADER)
        parts.append("")

    parts.append("\n\n".join(ordered))

    # The prose is checked against the same text the owner will read above it, so a
    # number the model uses must be one actually shown.
    reference = rendered_for_prose if rendered_for_prose is not None else "\n\n".join(ordered)
    verdict = prose_is_admissible(prose, rendered=reference, prose_slot=prose_slot)

    if verdict["status"] == "PASS":
        parts.append("")
        parts.append(NARRATION_HEADER)
        parts.append(str(prose).strip())
        return {
            "status": "OK",
            "message": "\n".join(parts),
            "prose_included": True,
            "prose_rejection": None,
        }

    if prose and verdict["reason"] != "empty_prose":
        # Say that something was withheld. A silently shorter message hides the fact that
        # the model tried to say something the filter refused.
        parts.append("")
        parts.append(DROPPED_NOTE.format(reason=verdict["reason"]))

    return {
        "status": "OK",
        "message": "\n".join(parts),
        "prose_included": False,
        "prose_rejection": verdict["reason"],
    }


def compose_from_envelopes(envelopes: list, prose: str | None = None, **kwargs) -> dict:
    """Convenience over tool envelopes: pulls `render` out and keeps the first prose slot.

    The prose slot comes from the first envelope that supplies one, because that is the
    block a narration is about. An envelope whose slot forbids prose entirely -- a failed
    fetch, for instance -- wins, since narrating an unavailable answer is exactly the case
    where a model would invent one.
    """
    blocks = [envelope.get("render") or "" for envelope in envelopes]
    slot = None
    for envelope in envelopes:
        candidate = envelope.get("prose_slot") or {}
        if candidate.get("allowed") is False:
            slot = candidate
            break
        if slot is None and candidate:
            slot = candidate
    return compose_message(blocks, prose, prose_slot=slot, **kwargs)
