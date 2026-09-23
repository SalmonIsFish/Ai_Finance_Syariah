"""Make backend JSON safe to re-serialise and to render, without losing meaning.

The hazard this exists for: GET /paper/risk-snapshot returns ``inf`` for
daily_loss_pct / weekly_loss_pct when equity is unusable or P&L history is too short
(local_api.py:347-351). That is the backend deliberately saying "stop", not a missing
value. json.loads parses Infinity happily, but json.dumps in strict mode refuses it,
so a naive wrapper reaches for a default -- and a risk number that renders as 0, a
dash, or N/A inverts the meaning of a fail-closed signal.

So a non-finite float becomes an explicit marker that the renderer must handle, and
test_bridge_risk_non_finite.py asserts it never renders as an innocuous placeholder.
"""

from __future__ import annotations

import math

NON_FINITE_KEY = "non_finite"


def _label(value: float) -> str:
    if math.isnan(value):
        return "nan"
    return "inf" if value > 0 else "-inf"


def sanitize_json(value):
    """Recursively replace non-finite floats with {"value": None, "non_finite": ...}.

    Everything else is returned unchanged. Booleans are left alone -- bool is a
    subclass of int, not float, so it never reaches the float branch.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return {"value": None, NON_FINITE_KEY: _label(value)}
    if isinstance(value, dict):
        return {key: sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json(item) for item in value]
    return value


def is_non_finite(value) -> bool:
    """True if sanitize_json replaced this value because it was inf/-inf/nan."""
    return isinstance(value, dict) and NON_FINITE_KEY in value


def non_finite_label(value) -> str | None:
    """The original marker ("inf" | "-inf" | "nan"), or None if the value was finite."""
    if is_non_finite(value):
        return str(value[NON_FINITE_KEY])
    return None
