"""One rule for numbers that reach a gate: if it cannot be computed, it does not pass.

Python makes the opposite easy. Every comparison against `NaN` is `False`, so a threshold
check written the obvious way turns fail-closed into fail-open the moment a value cannot
be computed:

    nan <= 0        -> False    # a price-sanity gate does not fire
    nan >= 33.0     -> False    # a Shariah ratio screens COMPLIANT
    nan > limit     -> False    # a position limit passes

Three variants recur across this codebase, and all three were found in one sweep:

* ``is None`` guards that miss ``NaN`` -- ``NaN`` is not ``None``, so the fallback never
  runs;
* ``float(x or 0)`` -- ``NaN`` is truthy, so the default never applies and the ``NaN``
  survives;
* ``max(0.0, nan)`` -- returns ``0.0``, flattening a non-computable value into a passing
  one, which is worse than propagating it.

This is the backend twin of ``bridge/sanitize.py``. It is a separate copy on purpose: the
bridge is a client and ``test_bridge_no_llm_in_path.py`` enforces that nothing in the
backend imports it.

Deliberately dependency-free, so any module in the gate chain can import it without
dragging anything else in.
"""

from __future__ import annotations

import math


def is_finite_number(value) -> bool:
    """True only for a real, finite number.

    ``bool`` is excluded because it is a subclass of ``int``, and ``True`` silently
    behaving as ``1`` in a quantity or a price is its own bug -- the same exclusion
    ``alpaca_paper_adapter`` already makes when validating an order quantity.

    Strings are rejected rather than coerced. A gate should not be the place where
    ``"nan"`` becomes a number.
    """
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def finite_or(value, fallback):
    """``value`` when it is a finite number, otherwise ``fallback``.

    The generalised form of the ``exposure_value`` fix: fall back when a value is missing
    **or** non-finite, not only when it is ``None``. The fallback is returned as given and
    is not itself checked -- callers pass a known-good constant, or a second field they
    have already reasoned about.
    """
    return value if is_finite_number(value) else fallback


def first_non_finite(**values) -> str | None:
    """The name of the first value that is not a finite number, or None if all are.

    For gates that need to say *which* input could not be computed. Naming the field is
    the difference between a refusal someone can act on and one that reads like a bug.
    """
    for name, value in values.items():
        if not is_finite_number(value):
            return name
    return None
