"""A fail-closed risk number must never render as an innocuous placeholder.

GET /paper/risk-snapshot returns ``inf`` for daily_loss_pct / weekly_loss_pct when
equity is unusable or the P&L history is too short (local_api.py:347-351). That is the
backend deliberately saying "stop".

The failure this guards against is quiet and plausible: json.dumps refuses a non-finite
float, so a wrapper reaches for a default, and the number renders as 0, a dash, or N/A.
Every one of those reads as "fine". A fail-closed signal that renders as reassurance is
worse than no signal at all, because the stated reason is untrue -- the same shape of
defect CLAUDE.md records for Bursa prices labelled synthetic.
"""

from __future__ import annotations

import json
import math

import pytest
from bridge.format import UNBOUNDED_RISK, render_risk
from bridge.sanitize import is_non_finite, non_finite_label, sanitize_json

# Anything that would let an unbounded loss read as acceptable.
INNOCUOUS = ("0.00", "0.0%", "0%", "--", "N/A", "n/a", "None", "null", "not reported")


def test_sanitize_marks_each_non_finite_shape():
    sanitized = sanitize_json(
        {"daily_loss_pct": math.inf, "weekly_loss_pct": -math.inf, "drift": math.nan}
    )
    assert non_finite_label(sanitized["daily_loss_pct"]) == "inf"
    assert non_finite_label(sanitized["weekly_loss_pct"]) == "-inf"
    assert non_finite_label(sanitized["drift"]) == "nan"


def test_sanitized_output_survives_strict_json():
    """MCP and Telegram both re-serialise; strict json.dumps refuses a raw inf."""
    sanitized = sanitize_json({"daily_loss_pct": math.inf})
    encoded = json.dumps(sanitized, allow_nan=False)
    assert "Infinity" not in encoded


def test_finite_values_are_untouched():
    payload = {"orders_today": 3, "daily_loss_pct": 1.25, "flag": True, "name": "AAPL"}
    assert sanitize_json(payload) == payload
    assert is_non_finite(payload["daily_loss_pct"]) is False


@pytest.mark.parametrize("field", ["daily_loss_pct", "weekly_loss_pct"])
def test_an_unbounded_loss_renders_as_blocking(field):
    facts = sanitize_json({"orders_today": 2, "daily_loss_pct": 0.5, "weekly_loss_pct": 1.0})
    facts[field] = sanitize_json(math.inf)

    rendered = render_risk(facts)

    assert UNBOUNDED_RISK in rendered
    assert "BLOCKING" in rendered
    assert field in rendered


@pytest.mark.parametrize("placeholder", INNOCUOUS)
def test_an_unbounded_loss_never_renders_as_innocuous(placeholder):
    facts = sanitize_json(
        {"orders_today": 2, "daily_loss_pct": math.inf, "weekly_loss_pct": math.inf}
    )

    rendered = render_risk(facts)

    # Strip the orders_today line: an integer count legitimately contains "0.00"-ish
    # text and is not a loss percentage.
    loss_lines = [line for line in rendered.splitlines() if "loss" in line.lower()]
    assert loss_lines, "the risk block must report the loss percentages"
    for line in loss_lines:
        assert placeholder not in line, (
            f"an unbounded loss rendered as {placeholder!r} in {line!r}; "
            "a fail-closed signal must never read as acceptable"
        )


def test_a_finite_risk_snapshot_is_not_marked_blocking():
    facts = sanitize_json({"orders_today": 1, "daily_loss_pct": 0.4, "weekly_loss_pct": 0.9})
    rendered = render_risk(facts)
    assert "BLOCKING" not in rendered
    assert UNBOUNDED_RISK not in rendered
    assert "0.40%" in rendered
