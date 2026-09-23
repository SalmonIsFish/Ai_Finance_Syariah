"""Deterministic renderers: backend fields in, text blocks out. No model involved.

This module is the reason the bridge can claim that facts outrank prose. Every number
and every verdict a bot shows comes from here, built out of named fields the backend
returned. A model can add narration around a block (filters.py decides whether it may),
but it can never write one.

Two standing rules, from CLAUDE.md:

* Never describe the system as "Shariah-compliant", and never coin "Shariah-aware".
  What the system does is APPLY an authority's determination and prove the application.
* Classification of a security is not permissibility of a trading strategy. The narrator
  footer says so on every block, unconditionally -- it is emitted here, not requested of
  a model that might forget.
"""

from __future__ import annotations

from bridge.sanitize import is_non_finite, non_finite_label

# The standing separation, emitted on every Shariah block. Wording follows CLAUDE.md.
CLASSIFICATION_FOOTER = (
    "Classification of a security is not permissibility of a trading strategy. "
    "This records how an authority's determination was applied; it is not a "
    "certification of this system."
)

UNKNOWN_VERDICT_NOTE = (
    "No approved publication covers this security, so there is no verdict to report. "
    "Treated as blocking."
)

# A non-finite risk number is the backend deliberately saying "stop"
# (local_api.py:347-351). It must never render as 0, a dash, or N/A -- a placeholder
# there inverts the meaning of a fail-closed signal.
UNBOUNDED_RISK = "UNBOUNDED (fail-closed: treat as blocking)"

# Every blocker code the gate chain can emit, with a fixed sentence. The auditor bot's
# "why was this rejected" is rendered from this dict; a model supplies connective prose
# at most. Unknown codes fail closed rather than being guessed at.
BLOCKER_SENTENCES: dict[str, str] = {
    "only_buy_side_supported": (
        "The order side is not supported for this asset class on this path."
    ),
    "shariah_rejected": (
        "The Shariah gate rejected the underlying security against the applicable "
        "authority's determination."
    ),
    "quant_no_buy_signal": (
        "The quant agent reported no BUY signal. This blocks directional equity entries; "
        "Level 1 option structures are exempt because none of them is a directional long."
    ),
    "synthetic_market_data": (
        "The price series was invented rather than fetched, so no decision may rest on it."
    ),
    "risk_rejected": "A configured risk limit was breached.",
    "valid_price_required": "No usable price was available for this order.",
    "option_structure_rejected": (
        "The option contract is not a permitted Level 1 structure, or its collateral "
        "was not proven."
    ),
    "option_contracts_not_permitted": (
        "Option contracts are not permitted under the determination currently in force. "
        "This is not a sizing or collateral problem and cannot be resolved by changing "
        "the order; it awaits a scholarly ruling."
    ),
    "portfolio_position_limit": (
        "The resulting position would exceed the per-position exposure limit."
    ),
    "portfolio_total_exposure_limit": ("The resulting book would exceed the total exposure limit."),
    "portfolio_existing_position": (
        "A position in this symbol already exists and this order would add to it."
    ),
    "portfolio_sell_without_position": (
        "A reduce-only SELL was requested with no local position to reduce."
    ),
    "portfolio_sector_concentration_limit": (
        "The resulting book would exceed the sector concentration limit."
    ),
    "portfolio_sell_exceeds_position": ("The SELL quantity is larger than the position held."),
    "portfolio_reduce_position": (
        "This order reduces an existing position (advisory note, not a blocker)."
    ),
}


def blocker_sentence(code: str) -> str:
    """Fixed sentence for a blocker code, failing closed on anything unrecognised."""
    known = BLOCKER_SENTENCES.get(code)
    if known is not None:
        return known
    return f"blocker '{code}' is not recognised by this bridge; read the raw record"


def _number(value, *, suffix: str = "", places: int = 2) -> str:
    """Render a number, or the fail-closed marker when the backend sent inf/nan."""
    if is_non_finite(value):
        return UNBOUNDED_RISK
    if value is None:
        return "not reported"
    if isinstance(value, (int, float)):
        return f"{value:.{places}f}{suffix}"
    return str(value)


def _line(label: str, value: str) -> str:
    return f"  {label:<14}{value}"


def render_shariah(facts: dict) -> str:
    """Render a Shariah verdict from /api/shariah/{ticker} or /api/screen/{ticker}.

    Accepts either shape: the universe handler nests under ``verdict``, the screen
    handler under ``shariah``. Both carry the same named fields.
    """
    ticker = str(facts.get("ticker") or "?")
    verdict = facts.get("verdict") or facts.get("shariah") or {}
    status = str(verdict.get("status") or "UNKNOWN")

    lines = [f"[Shariah] {ticker}", _line("Verdict", status)]

    reason = verdict.get("reason") or verdict.get("reason_code")
    if reason:
        lines.append(_line("Reason", str(reason)))
    if verdict.get("issuer_name"):
        lines.append(_line("Issuer", str(verdict["issuer_name"])))
    if verdict.get("board"):
        lines.append(_line("Board", str(verdict["board"])))
    if verdict.get("sector"):
        lines.append(_line("Sector", str(verdict["sector"])))

    publication_date = verdict.get("publication_date")
    publication_id = verdict.get("publication_id")
    document_hash = verdict.get("source_document_hash")
    if publication_date or publication_id:
        lines.append(
            _line("Source", f"SC publication {publication_id or '?'} ({publication_date or '?'})")
        )
    if document_hash:
        lines.append(_line("Document", str(document_hash)))

    if status == "UNKNOWN":
        lines.append(_line("Note", UNKNOWN_VERDICT_NOTE))

    lines.append("")
    lines.append(f"  {CLASSIFICATION_FOOTER}")
    return "\n".join(lines)


def render_quant(facts: dict) -> str:
    """Render a quant signal from /api/quant/{ticker} or the quant half of a screen."""
    ticker = str(facts.get("ticker") or "?")
    quant = facts.get("quant") or {}
    lines = [
        f"[Quant] {ticker}",
        _line("Signal", str(quant.get("signal") or "NO_SIGNAL")),
    ]
    if quant.get("reason_code") or quant.get("reason"):
        lines.append(_line("Reason", str(quant.get("reason_code") or quant.get("reason"))))
    if quant.get("strategy_id"):
        lines.append(_line("Strategy", str(quant["strategy_id"])))
    lines.append(_line("Price", _number(quant.get("price"))))
    lines.append(_line("Source", str(quant.get("price_source") or "unknown")))
    lines.append(_line("As of", str(quant.get("as_of_date") or "unknown")))
    lines.append(_line("Bars", _number(quant.get("bars"), places=0)))

    attractiveness = facts.get("attractiveness") or {}
    if attractiveness.get("attractiveness") is not None:
        lines.append(_line("Attractive", _number(attractiveness.get("attractiveness"))))
    return "\n".join(lines)


def render_risk(facts: dict, limits: dict | None = None) -> str:
    """Render /paper/risk-snapshot, preserving a fail-closed inf rather than hiding it."""
    lines = ["[Risk]"]
    lines.append(_line("Orders today", _number(facts.get("orders_today"), places=0)))
    lines.append(_line("Daily loss", _number(facts.get("daily_loss_pct"), suffix="%")))
    lines.append(_line("Weekly loss", _number(facts.get("weekly_loss_pct"), suffix="%")))

    blocking = [
        name
        for name in ("daily_loss_pct", "weekly_loss_pct")
        if non_finite_label(facts.get(name)) is not None
    ]
    if blocking:
        verb = "is" if len(blocking) == 1 else "are"
        lines.append(
            _line("Status", f"BLOCKING: {', '.join(blocking)} {verb} unbounded (fail-closed)")
        )

    for key, value in (limits or {}).items():
        lines.append(_line(key, _number(value)))
    return "\n".join(lines)


def render_blockers(blocker_messages: list | None, blockers: list | None) -> str:
    """Render why an order was blocked.

    Prefers the backend's own ``blocker_messages`` -- human sentences it generated
    deterministically -- and falls back to BLOCKER_SENTENCES for a bare code list.
    Either way the text is the system's, never the model's.
    """
    lines = ["[Blocked]"]
    if blocker_messages:
        for item in blocker_messages:
            code = str(item.get("blocker") or "?")
            message = str(item.get("message") or blocker_sentence(code))
            lines.append(f"  - {code}: {message}")
        return "\n".join(lines)
    for code in blockers or []:
        lines.append(f"  - {code}: {blocker_sentence(str(code))}")
    if len(lines) == 1:
        lines.append("  - no blockers reported")
    return "\n".join(lines)


def render_compliance(facts: dict) -> str:
    """Render the disposal clock from /portfolio/compliance.

    ``overdue`` is only present on NON_COMPLIANT_HOLDING entries, so it is read with
    .get(); ``days_remaining`` is floored and goes negative once a deadline has passed;
    ``disposal_deadline`` is null for an UNCONFIRMED holding because an unconfirmed
    holding is not a ruling and no countdown should be invented for it.
    """
    screening = facts.get("screening") or {}
    flagged = screening.get("flagged") or []
    window = screening.get("disposal_window_days")
    overdue_count = screening.get("overdue_count") or 0

    lines = [
        "[Holdings compliance]",
        _line("Positions", _number(screening.get("position_count"), places=0)),
        _line("Flagged", _number(screening.get("flagged_count"), places=0)),
        _line("Non-compliant", _number(screening.get("non_compliant_count"), places=0)),
        _line("Unconfirmed", _number(screening.get("unconfirmed_count"), places=0)),
        _line("Overdue", _number(overdue_count, places=0)),
        _line("Window", f"{window} days" if window is not None else "not reported"),
    ]

    for holding in flagged:
        symbol = str(holding.get("symbol") or "?")
        alert = str(holding.get("alert") or "?")
        lines.append("")
        lines.append(f"  {symbol} -- {alert}")
        deadline = holding.get("disposal_deadline")
        days = holding.get("days_remaining")
        basis = holding.get("deadline_basis")
        if deadline is None:
            lines.append(
                "      no deadline: an unconfirmed holding is not a ruling, "
                "so no countdown is invented"
            )
        else:
            state = "OVERDUE" if holding.get("overdue") else "due"
            lines.append(f"      {state} {deadline} ({days} days remaining, basis: {basis})")
            if basis == "first_observed":
                lines.append(
                    "      basis is first observation, so the true SC deadline may be earlier"
                )
        if days is None and alert == "NON_COMPLIANT_HOLDING":
            lines.append("      days_remaining is unavailable -- needs attention, not safe")

    purification = facts.get("purification") or {}
    if purification:
        lines.append("")
        lines.append(_line("Purification", _number(purification.get("total_purification_due"))))
        if purification.get("complete") is False:
            unpriced = purification.get("unpriced") or []
            lines.append(
                _line("Note", f"estimate understated; unpriced: {', '.join(map(str, unpriced))}")
            )
    return "\n".join(lines)


def render_shariah_us(facts: dict) -> str:
    """Render the US SEC EDGAR screen from /stock/{symbol}/explain.

    A different surface from the SC Malaysia list, and deliberately rendered separately
    rather than squeezed into render_shariah: the fields genuinely differ, and so does
    the strength of the claim. The SC list is an authority's determination; this is a
    self-built ratio screen whose own module docstring calls it "not a certified
    screening service". Flattening the two would misrepresent the weaker one.
    """
    symbol = str(facts.get("symbol") or "?")
    verdict = facts.get("verdict") or {}
    provenance = facts.get("provenance") or {}

    lines = [
        f"[Shariah] {symbol} (US)",
        _line("Verdict", str(verdict.get("status") or "UNKNOWN")),
        _line("Tradeable", str(verdict.get("tradeable"))),
    ]
    if verdict.get("statement"):
        lines.append(_line("Statement", str(verdict["statement"])))

    rule = facts.get("rule") or {}
    if rule.get("label"):
        lines.append(_line("Rule", f"{rule.get('label')} (tier {rule.get('tier')})"))
    if rule.get("narrowest_margin_pct") is not None:
        lines.append(_line("Margin", _number(rule.get("narrowest_margin_pct"), suffix="%")))

    lines.append(_line("Provider", str(provenance.get("provider") or "unknown")))
    lines.append(_line("Report date", str(provenance.get("report_date") or "unknown")))
    lines.append(_line("Screened at", str(provenance.get("screened_at") or "unknown")))

    # The limitations are the honest half of this screen and must not be dropped:
    # business activity is approximated by SIC code, and XBRL cannot separate Islamic
    # from conventional instruments, so both ratios are overstated.
    for limitation in provenance.get("limitations") or []:
        lines.append(f"      - {limitation}")

    lines.append("")
    lines.append(f"  {CLASSIFICATION_FOOTER}")
    return "\n".join(lines)


def render_publications(facts: dict) -> str:
    """Render /api/universe/publications -- which SC list is active, and what preceded it."""
    publications = facts.get("publications") or []
    lines = ["[SC publications]", _line("Count", _number(len(publications), places=0))]
    for publication in publications[:10]:
        active = " ACTIVE" if publication.get("activated_at") else ""
        lines.append("")
        lines.append(f"  {publication.get('id')}  {publication.get('publication_date')}{active}")
        lines.append(_line("  Review", str(publication.get("human_review_status") or "unknown")))
        lines.append(_line("  Document", str(publication.get("source_document_hash") or "unknown")))
        official = publication.get("official_record_count")
        parsed = publication.get("parsed_record_count")
        if official is not None or parsed is not None:
            lines.append(_line("  Records", f"{parsed} parsed of {official} official"))
    return "\n".join(lines)


def render_evidence(facts: dict) -> str:
    """Render /api/evidence/{ticker} -- decisions this system actually recorded."""
    ticker = str(facts.get("ticker") or "?")
    decisions = facts.get("decisions") or []
    lines = [f"[Evidence] {ticker}", _line("Records", _number(facts.get("count"), places=0))]
    for decision in decisions[:10]:
        shariah = decision.get("shariah") or {}
        quant = decision.get("quant") or {}
        lines.append("")
        lines.append(f"  {decision.get('timestamp')}  {decision.get('decision')}")
        # `source` separates an order the owner actually considered from watchlist scan
        # noise; without it a scan of 30 symbols looks like 30 considered trades.
        lines.append(_line("  Source", str(decision.get("source") or "unknown")))
        lines.append(_line("  Reason", str(decision.get("decision_reason") or "none")))
        lines.append(_line("  Shariah", f"{shariah.get('status')} ({shariah.get('reason')})"))
        if shariah.get("source_document_hash"):
            lines.append(_line("  Document", str(shariah["source_document_hash"])))
        if quant:
            lines.append(_line("  Quant", f"{quant.get('signal')} at {quant.get('price')}"))
    return "\n".join(lines)


def render_universe(facts: dict) -> str:
    """Render /api/universe -- the eligible Malaysian securities under the active list."""
    active = facts.get("active_publication") or {}
    securities = facts.get("securities") or []
    lines = ["[Universe]"]
    if not active:
        lines.append(_line("Active list", "none -- no approved publication is active"))
        return "\n".join(lines)
    lines.append(_line("Active list", f"{active.get('id')} ({active.get('publication_date')})"))
    lines.append(_line("Filter", str(facts.get("shariah_status_filter") or "PASS")))
    lines.append(_line("Count", _number(facts.get("count"), places=0)))
    lines.append(_line("Showing", _number(len(securities), places=0)))
    for security in securities[:25]:
        lines.append(
            f"  {str(security.get('ticker') or '?'):<8} {security.get('verdict'):<7} "
            f"{security.get('issuer_name') or ''}"
        )
    return "\n".join(lines)
