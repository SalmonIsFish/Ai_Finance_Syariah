"""Re-screen held positions against the current Shariah authority.

Screening happens at order time. Nothing re-checks a position afterwards, so a
security that was COMPLIANT when bought can be reclassified NON_COMPLIANT by a
later SC publication with no signal to the owner. The SC/SAC list updates on the
last Friday of May and November, so this is a scheduled certainty, not a risk.

This module answers one question for every open position: *does the current
authority still confirm this holding?* It reports; it never trades and never
decides. The owner reads the alert, confirms against the SC paper, and instructs
any disposal.

Three outcomes, mirroring the gate's own three states. UNKNOWN is never collapsed
into NON_COMPLIANT -- "the authority says this is not eligible" and "we could not
confirm" carry different obligations, and conflating them would either raise a
false divestment duty or hide a real one:

    PASS    -> compliant, no alert
    REJECT  -> NON_COMPLIANT_HOLDING, the owner has a divestment decision
    UNKNOWN -> UNCONFIRMED_HOLDING, needs attention but is not a ruling

Market routing deliberately reuses agents.shariah_agent.evaluate_shariah, which
routes on detect_market(). That function is the gate's own classifier, so a
holding is screened here exactly as it would be gated at order time. A separate
market column on paper_positions was considered and rejected: it would be a
second source of truth that could drift from the gate, letting a position be
gated as Malaysian but swept as US.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import portfolio_store

COMPLIANT = "PASS"
NON_COMPLIANT = "REJECT"
UNCONFIRMED = "UNKNOWN"

ALERT_NON_COMPLIANT = "NON_COMPLIANT_HOLDING"
ALERT_UNCONFIRMED = "UNCONFIRMED_HOLDING"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_evaluate(symbol: str) -> dict:
    # Imported lazily so a caller that supplies its own `evaluate` (every test
    # does) never pulls in the SEC EDGAR path or its network dependency.
    from agents.shariah_agent import evaluate_shariah

    return evaluate_shariah(symbol)


def _alert_for(status: str) -> str | None:
    if status == COMPLIANT:
        return None
    if status == NON_COMPLIANT:
        return ALERT_NON_COMPLIANT
    return ALERT_UNCONFIRMED


def screen_holdings(
    connection: sqlite3.Connection, *, evaluate=None, market: str | None = None
) -> dict:
    """Screen every open position against the current authority.

    `evaluate` is the replaceable seam: it takes a symbol and returns the
    agents.shariah_agent.evaluate_shariah shape. Tests swap it so they never
    reach SEC EDGAR or a real publication store.

    `market` restricts the sweep to one market ("MY" or "US"), classified by the
    same detect_market() the gate routes on. This is not a convenience: an SC
    publication changes Malaysian eligibility only, and screening a US holding
    goes through sec_edgar_screen, which is a live SEC fetch of up to ~4.7 MB.
    A caller reacting to an SC activation must be able to sweep the affected
    market without firing network requests for holdings that publication cannot
    possibly have reclassified.

    An unrecognised verdict is treated as UNCONFIRMED rather than trusted, so a
    malformed or future status can never read as compliant.
    """
    evaluate = evaluate or _default_evaluate
    portfolio_store.ensure_portfolio_tables(connection)

    rows = connection.execute(
        """
        SELECT symbol, account_suffix, quantity, average_cost, cost_basis
        FROM paper_positions
        WHERE quantity != 0
        ORDER BY symbol, account_suffix
        """
    ).fetchall()

    if market is not None:
        from agents.shariah_agent import detect_market

        rows = [row for row in rows if detect_market(dict(row)["symbol"]) == market]

    holdings = []
    for row in rows:
        position = dict(row)
        verdict = evaluate(position["symbol"])
        status = verdict.get("status")
        if status not in (COMPLIANT, NON_COMPLIANT, UNCONFIRMED):
            status = UNCONFIRMED

        holdings.append(
            {
                "symbol": position["symbol"],
                "account_suffix": position["account_suffix"],
                "quantity": position["quantity"],
                "average_cost": position["average_cost"],
                "cost_basis": position["cost_basis"],
                "market": verdict.get("market"),
                "provider": verdict.get("provider"),
                "status": status,
                "reason": verdict.get("reason"),
                "publication_id": verdict.get("publication_id"),
                "publication_date": verdict.get("publication_date"),
                "alert": _alert_for(status),
            }
        )

    flagged = [h for h in holdings if h["alert"] is not None]
    return {
        "status": "OK",
        "screened_at": _utc_now(),
        "market_filter": market,
        "position_count": len(holdings),
        "holdings": holdings,
        "flagged": flagged,
        "flagged_count": len(flagged),
        "non_compliant_count": sum(1 for h in flagged if h["alert"] == ALERT_NON_COMPLIANT),
        "unconfirmed_count": sum(1 for h in flagged if h["alert"] == ALERT_UNCONFIRMED),
    }


def purification_due(*, cost_basis: float, proceeds: float) -> float:
    """Amount owed to baitulmal on disposal of a non-compliant holding.

    THE RULING GOVERNS THIS FUNCTION, NOT THE CODE. Per the project owner's
    reading of the SC guidance: a holding reclassified non-compliant is disposed
    of within one month, the original cost is retained, and anything above cost
    is given away. So:

        proceeds > cost_basis -> keep cost_basis, donate (proceeds - cost_basis)
        proceeds <= cost_basis -> a loss; nothing to purify

    Deliberately kept to two explicit inputs with no price source, no date logic
    and no database access, so a scholar can review the whole rule at once. Three
    questions this function does NOT answer, because they are fiqh and not
    engineering -- confirm each before relying on a number it produces:

      1. Which price is `proceeds` -- the announcement-day close, or the actual
         disposal price? They differ, and the difference is the donation.
      2. Are dividends received while the holding was compliant treated
         separately from capital gain?
      3. What applies if the price never recovers to cost within the month?

    Negative inputs raise rather than silently producing a plausible number: a
    wrong purification figure is a religious error, not a rounding error.
    """
    if cost_basis < 0 or proceeds < 0:
        raise ValueError(
            f"purification inputs must be non-negative (cost_basis={cost_basis}, proceeds={proceeds})"
        )
    return round(max(0.0, proceeds - cost_basis), 2)


def purification_ledger(screening: dict, *, price_lookup=None) -> dict:
    """Purification owed across every NON_COMPLIANT holding, as an estimate.

    Estimate, not a bill: nothing has been sold. `price_lookup` takes a symbol
    and returns a current price, or None when unavailable.

    A holding whose price cannot be resolved is reported under `unpriced` rather
    than assumed to be worth zero -- an unpriced holding that silently
    contributed 0.00 would understate what is owed, which is the one direction
    this must never err in. UNCONFIRMED holdings are excluded entirely: no
    authority has ruled them non-compliant, so no obligation has arisen.
    """
    entries = []
    unpriced = []
    total = 0.0

    for holding in screening.get("flagged", []):
        if holding.get("alert") != ALERT_NON_COMPLIANT:
            continue
        price = price_lookup(holding["symbol"]) if price_lookup else None
        if price is None:
            unpriced.append(holding["symbol"])
            continue
        proceeds = price * holding["quantity"]
        due = purification_due(cost_basis=holding["cost_basis"], proceeds=proceeds)
        total += due
        entries.append(
            {
                "symbol": holding["symbol"],
                "quantity": holding["quantity"],
                "cost_basis": holding["cost_basis"],
                "current_price": price,
                "estimated_proceeds": round(proceeds, 2),
                "purification_due": due,
            }
        )

    return {
        "status": "ESTIMATE",
        "entries": entries,
        "total_purification_due": round(total, 2),
        "unpriced": unpriced,
        "complete": not unpriced,
    }
