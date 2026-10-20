"""GREEN / YELLOW / RED over a Shariah verdict the screen already produced.

This module never screens, never fetches and never changes a verdict's
``status``. The order gate keeps reading ``status`` exactly as before; the
light is a monitoring view on top of it, for a holder deciding what to do.

    GREEN   compliant, and clearly so
    YELLOW  under review: near a limit, data missing, mixed business activity,
            or only just cleared after a failing report
    RED     non-compliant

The asymmetry is deliberate. Every rule here can only move a verdict *towards*
caution, with one exception that is itself cautious: a business-activity block
the screen rejects only because SEC's industry code mixes compliant and
non-compliant operators is shown YELLOW, "needs a revenue-source review",
rather than RED -- the screen's own label says the verdict is a placeholder,
not a finding. Its ``status`` stays REJECT, so it still cannot be bought.

Two things this layer will not do, each pinned by a test:

- An AI flag can add a YELLOW reason. It can never produce GREEN or soften a
  RED, whatever the flag says. A wrong GREEN is a religious error.
- History never softens a new RED. A stock that fails is RED at once; only a
  *recovery* waits for the next report to confirm it.

The confidence behind a colour is computed -- distance from a benchmark, a
missing filing line -- never a model's self-reported certainty.

This is the owner's application of the SC SAC methodology, not a ruling. This
module must not import ``sec_edgar_screen`` (see test_single_screening_path):
the limit is read off the verdict's ``ratios["limit_pct"]``.
"""

from __future__ import annotations

import sqlite3

import shariah_screen_store
from numeric_guards import is_finite_number

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"

# A ratio within this fraction *of* its limit is YELLOW: 33% * 0.10 = 3.3
# points, so 29.7% up to the limit. Chosen by the owner on 2026-10-20 and
# recorded on every result so a later change is visible in old verdicts.
NEAR_LIMIT_BAND = 0.10

POLICY_VERSION = "traffic-light-v1"

# Business-activity categories the screen rejects as a placeholder pending a
# revenue review, rather than as an established exclusion.
MIXED_ACTIVITY_CATEGORIES = {"hospitality_mixed_activity"}

_RATIOS = (
    ("debt_ratio_pct", "near_limit_debt", "interest-bearing debt / total assets"),
    ("cash_ratio_pct", "near_limit_cash", "conventional cash / total assets"),
)


def _reason(code: str, detail: str, **extra) -> dict:
    return {"code": code, "detail": detail, **extra}


def _result(light: str, reasons: list[dict]) -> dict:
    return {
        "light": light,
        "reasons": reasons,
        "near_limit_band": NEAR_LIMIT_BAND,
        "policy_version": POLICY_VERSION,
    }


def _flag_reasons(flags) -> list[dict]:
    """Every flag becomes a YELLOW reason. Nothing in a flag is read as a colour."""
    reasons = []
    for flag in flags or []:
        if not isinstance(flag, dict):
            continue
        reasons.append(
            _reason(
                str(flag.get("code") or "flagged_for_review"),
                str(flag.get("detail") or "flagged for review"),
                source=flag.get("source"),
                quote=flag.get("quote"),
            )
        )
    return reasons


def _near_limit_reasons(ratios: dict) -> list[dict]:
    limit = ratios.get("limit_pct")
    if not is_finite_number(limit) or limit <= 0:
        return [_reason("data_unavailable", "benchmark limit missing from the verdict")]
    threshold = limit * (1 - NEAR_LIMIT_BAND)
    reasons = []
    for key, code, label in _RATIOS:
        value = ratios.get(key)
        if not is_finite_number(value):
            reasons.append(_reason("data_unavailable", f"{label} is not a usable number"))
        elif value >= threshold:
            reasons.append(
                _reason(
                    code,
                    f"{label} {value:.1f}% is within {NEAR_LIMIT_BAND:.0%} of the "
                    f"{limit:.0f}% limit (yellow from {threshold:.1f}%)",
                    value_pct=value,
                    limit_pct=limit,
                )
            )
    return reasons


def classify(
    verdict: dict, *, prior_report_status: str | None = None, flags=None, escalations=None
) -> dict:
    """Colour one evaluate_shariah-shaped verdict.

    ``escalations`` are the owner's manual "treat this as RED" decisions. They
    are the only manual input, and they only ever move a colour to RED -- there
    is deliberately no manual way to produce GREEN.
    """
    result = _classify_screen(verdict, prior_report_status=prior_report_status, flags=flags)
    escalation_reasons = [
        _reason(
            "owner_escalation",
            str(item.get("detail") or "escalated to RED by the owner"),
            source="owner",
        )
        for item in escalations or []
        if isinstance(item, dict)
    ]
    if escalation_reasons and result["light"] != RED:
        return _result(RED, escalation_reasons + result["reasons"])
    return result


def _classify_screen(verdict: dict, *, prior_report_status: str | None = None, flags=None) -> dict:
    """Colour from the screen alone (plus review flags).

    ``prior_report_status`` is the raw screen status recorded for the previous
    annual report (see prior_report_status below); ``flags`` are review flags,
    e.g. from a model reading the annual report. Both can only add caution.
    """
    verdict = verdict if isinstance(verdict, dict) else {}
    status = verdict.get("status")
    details = verdict.get("details") if isinstance(verdict.get("details"), dict) else {}
    flag_reasons = _flag_reasons(flags)

    if status == "REJECT":
        if (
            verdict.get("market") == "US"
            and details.get("screen") == "business_activity"
            and details.get("exclusion_category") in MIXED_ACTIVITY_CATEGORIES
        ):
            return _result(
                YELLOW,
                [
                    _reason(
                        "mixed_business_activity",
                        "industry code mixes compliant and non-compliant operators; "
                        "needs a revenue-source review",
                    )
                ]
                + flag_reasons,
            )
        reason = (
            details.get("reason") or verdict.get("reason") or "the screen found it non-compliant"
        )
        return _result(RED, [_reason("screen_non_compliant", str(reason))] + flag_reasons)

    if status != "PASS":
        return _result(
            YELLOW,
            [_reason("data_unavailable", "the screen could not confirm this security")]
            + flag_reasons,
        )

    # PASS. Malaysia: the SC list itself is the determination -- no ratio of ours
    # second-guesses it.
    reasons = []
    if verdict.get("market") == "US":
        ratios = details.get("ratios") if isinstance(details.get("ratios"), dict) else {}
        reasons.extend(_near_limit_reasons(ratios))
        if not ratios.get("debt_concepts_used"):
            reasons.append(
                _reason(
                    "no_debt_line_in_filing",
                    "no interest-bearing debt line was found in the filing, so debt "
                    "was counted as zero",
                )
            )
        if prior_report_status is not None and prior_report_status != "COMPLIANT":
            reasons.append(
                _reason(
                    "recently_cleared",
                    f"previous annual report was {prior_report_status}; "
                    "the next report must confirm this one",
                )
            )

    reasons.extend(flag_reasons)
    return _result(YELLOW if reasons else GREEN, reasons)


def prior_report_status(
    connection: sqlite3.Connection,
    symbol: str,
    report_date: str | None,
    methodology: str | None = None,
) -> str | None:
    """Raw status of the latest logged screen for an *earlier* annual report.

    Screens without a report date (fetch errors, unreadable filings) say nothing
    about a report and are skipped. Returns None when there is no earlier report.
    Only screens under the same methodology are compared (default SC SAC): an
    AAOIFI pass after an SC fail is a different measurement, not a recovery.
    """
    if not report_date:
        return None
    shariah_screen_store.ensure_shariah_screen_tables(connection)
    row = connection.execute(
        "SELECT status FROM shariah_screens "
        "WHERE symbol = ? AND report_date IS NOT NULL AND report_date < ? "
        "AND COALESCE(json_extract(payload, '$.methodology'), 'SC_MY_SAC') = ? "
        "ORDER BY report_date DESC, id DESC LIMIT 1",
        (str(symbol or "").strip().upper(), report_date, methodology or "SC_MY_SAC"),
    ).fetchone()
    return row[0] if row else None
