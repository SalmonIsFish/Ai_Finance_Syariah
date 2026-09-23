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
from datetime import datetime, timedelta, timezone

from numeric_guards import first_non_finite, is_finite_number

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


# The owner's reading of the SC guidance: a reclassified holding is disposed of
# within one month. Expressed as 30 days because a date needs arithmetic, and
# flagged as approximate because "one month" and "30 days" are not the same
# claim -- one of several date questions in this area the code cannot settle on
# its own. See the note on the clock's start below, which matters more.
DISPOSAL_WINDOW_DAYS = 30


def ensure_compliance_flag_tables(connection: sqlite3.Connection) -> None:
    """Persist when a holding was first seen non-compliant.

    Without this the one-month duty is unmeasurable. `screen_holdings` stamps
    `screened_at` on its *response*, never on the *holding*, so a position
    flagged a year ago rendered identically to one flagged this morning: no
    clock could start, no deadline could be warned about or breached, and the
    disposal window existed only as a sentence in the UI.
    """
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS holdings_compliance_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            account_suffix TEXT NOT NULL,
            alert TEXT NOT NULL,
            first_flagged_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            publication_id TEXT,
            publication_date TEXT,
            cleared_at TEXT,
            UNIQUE(symbol, account_suffix, alert)
        )
        """
    )
    connection.commit()


def _deadline_from(start_date: str | None) -> tuple[str | None, int | None]:
    """(deadline ISO date, whole days remaining) from a start date, or (None, None)."""
    if not start_date:
        return None, None
    try:
        start = datetime.fromisoformat(str(start_date))
    except ValueError:
        try:
            start = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
        except ValueError:
            return None, None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    deadline = start + timedelta(days=DISPOSAL_WINDOW_DAYS)
    remaining = (deadline - datetime.now(timezone.utc)).total_seconds() / 86400
    # Floored: 0.4 days left is not "0 days left", and rounding up would report
    # a day of grace that does not exist.
    return deadline.date().isoformat(), int(remaining // 1)


def apply_disposal_clock(connection: sqlite3.Connection, screening: dict) -> dict:
    """Record when each alert was first raised, and annotate the deadline.

    Kept out of `screen_holdings` on purpose: that function reads and reports
    and writes nothing, which is what lets any caller run it freely, including
    against a live SEC fetch budget. This is the step that persists, so it is
    separate and named for what it does.

    WHERE THE CLOCK STARTS, AND WHY IT IS NOT WHEN WE NOTICED

    The SC's month runs from its ruling taking effect, not from the day this
    system happened to sweep. Those differ by however long the software was not
    looking -- and if a publication is activated late, they differ a lot. Taking
    our own observation as the start would silently extend a religious deadline
    to suit our uptime, always in the permissive direction.

    So the deadline is computed from the publication's own date when the verdict
    carries one, and only falls back to first observation when it does not. Both
    are reported, along with which one was used, so the difference is visible
    rather than assumed away.
    """
    ensure_compliance_flag_tables(connection)
    now = _utc_now()
    flagged = screening.get("flagged") or []

    live_keys = set()
    for holding in flagged:
        key = (holding["symbol"], holding["account_suffix"], holding["alert"])
        live_keys.add(key)
        connection.execute(
            """
            INSERT INTO holdings_compliance_flags
                (symbol, account_suffix, alert, first_flagged_at, last_seen_at,
                 publication_id, publication_date, cleared_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(symbol, account_suffix, alert) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                publication_id = excluded.publication_id,
                publication_date = excluded.publication_date,
                -- Re-raising a previously cleared alert starts a new clock:
                -- a security reclassified, sold, re-bought and reclassified
                -- again owes a fresh month, not the remainder of an old one.
                first_flagged_at = CASE
                    WHEN holdings_compliance_flags.cleared_at IS NOT NULL
                    THEN excluded.first_flagged_at
                    ELSE holdings_compliance_flags.first_flagged_at
                END,
                cleared_at = NULL
            """,
            (
                holding["symbol"],
                holding["account_suffix"],
                holding["alert"],
                now,
                now,
                holding.get("publication_id"),
                holding.get("publication_date"),
            ),
        )

    # Anything previously flagged and no longer flagged is cleared -- the
    # position was disposed of, or a later publication reinstated it. Recorded
    # rather than deleted: the trail of having been flagged is worth keeping.
    for row in connection.execute(
        "SELECT symbol, account_suffix, alert FROM holdings_compliance_flags "
        "WHERE cleared_at IS NULL"
    ).fetchall():
        key = (row[0], row[1], row[2])
        if key not in live_keys:
            connection.execute(
                "UPDATE holdings_compliance_flags SET cleared_at = ? "
                "WHERE symbol = ? AND account_suffix = ? AND alert = ?",
                (now, row[0], row[1], row[2]),
            )
    connection.commit()

    stored = {
        (row[0], row[1], row[2]): {"first_flagged_at": row[3], "publication_date": row[4]}
        for row in connection.execute(
            "SELECT symbol, account_suffix, alert, first_flagged_at, publication_date "
            "FROM holdings_compliance_flags WHERE cleared_at IS NULL"
        ).fetchall()
    }

    for holding in flagged:
        key = (holding["symbol"], holding["account_suffix"], holding["alert"])
        record = stored.get(key, {})
        first_flagged_at = record.get("first_flagged_at")
        holding["first_flagged_at"] = first_flagged_at

        # Only a REJECT carries the disposal duty. An UNCONFIRMED holding is not
        # a ruling of ineligibility, so attaching a countdown to it would invent
        # an obligation the authority never stated.
        if holding["alert"] != ALERT_NON_COMPLIANT:
            holding["disposal_deadline"] = None
            holding["days_remaining"] = None
            holding["deadline_basis"] = None
            continue

        publication_date = holding.get("publication_date") or record.get("publication_date")
        basis = "publication_date" if publication_date else "first_observed"
        deadline, remaining = _deadline_from(publication_date or first_flagged_at)
        holding["disposal_deadline"] = deadline
        holding["days_remaining"] = remaining
        holding["deadline_basis"] = basis
        holding["overdue"] = remaining is not None and remaining < 0

    screening["disposal_window_days"] = DISPOSAL_WINDOW_DAYS
    screening["overdue_count"] = sum(1 for h in flagged if h.get("overdue"))
    return screening


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
    # `nan < 0` is False, so the guard below never fired on a non-finite input, and
    # `max(0.0, nan)` then returned 0.0 -- the exact silent zero this function's own
    # docstring says must never happen. A religious obligation reported as nil because a
    # price was missing is the one direction this must not err in.
    unusable = first_non_finite(cost_basis=cost_basis, proceeds=proceeds)
    if unusable is not None:
        raise ValueError(f"purification input {unusable} is not a finite number")
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
        # `is None` missed NaN, so an unpriced holding was not recorded in `unpriced`,
        # contributed 0.00 to the total, and the ledger still reported complete: True.
        if not is_finite_number(price):
            unpriced.append(holding["symbol"])
            continue
        proceeds = price * holding["quantity"]
        if not is_finite_number(proceeds):
            unpriced.append(holding["symbol"])
            continue
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
