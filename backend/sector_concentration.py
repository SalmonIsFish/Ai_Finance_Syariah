"""Sector-concentration limit for the portfolio risk overlay.

The risk engine caps position size, total exposure, loss per trade, daily loss
and order count -- all of which a portfolio can satisfy while being five names in
one industry. This adds the missing axis: how much of the account may sit in any
single sector.

It needs no new data source. `sec_edgar_screen` already fetches each company's
SEC SIC code to decide business-activity exclusion, and now persists it on the
verdict, so the sector of every screened symbol is already in `shariah_screens`.
That is a deliberate reuse: the same classification that answers "is this a
conventional bank?" also answers "is this portfolio all banks?".

Two design points worth stating, because both are judgement calls:

  - **Unknown sectors are bucketed, not waved through.** Failing closed on a
    symbol with no screen would block every order until the whole book had been
    re-screened -- a hard breakage the moment this ships, since no existing row
    carries a SIC. Passing unknowns unconditionally would instead let a
    portfolio hide concentration behind missing data. So every unmapped symbol
    joins one explicit UNKNOWN bucket that is capped like any other sector.
  - **Options are out of scope here.** CLAUDE.md's known limitation 4 records a
    real bug where the equity exposure overlay treated an option's contracts and
    premium as shares and share-price. This module is only ever called from the
    equity branch of that overlay, for the same reason.
"""

from numeric_guards import finite_or, is_finite_number
import json
import sqlite3

UNKNOWN_SECTOR = "Unclassified"

# SEC SIC division blocks, collapsed to the coarse sectors a concentration limit
# actually cares about. Ranges are inclusive, and follow the same shape as
# sec_edgar_screen.EXCLUDED_SIC_RANGES so the two tables read alike.
SECTOR_SIC_RANGES = [
    ((100, 999), "Agriculture"),
    ((1000, 1099), "Materials"),
    ((1200, 1299), "Energy"),
    ((1300, 1399), "Energy"),
    ((1400, 1499), "Materials"),
    ((1500, 1799), "Industrials"),
    ((2000, 2199), "Consumer"),
    ((2200, 2399), "Consumer"),
    ((2400, 2599), "Industrials"),
    ((2600, 2699), "Materials"),
    ((2700, 2799), "Communications"),
    ((2800, 2829), "Materials"),
    ((2830, 2836), "Health Care"),
    ((2840, 2899), "Materials"),
    ((2900, 2999), "Energy"),
    ((3000, 3299), "Materials"),
    ((3300, 3399), "Materials"),
    ((3400, 3569), "Industrials"),
    ((3570, 3579), "Technology"),
    ((3580, 3599), "Industrials"),
    ((3600, 3629), "Industrials"),
    ((3630, 3669), "Technology"),
    ((3670, 3679), "Technology"),
    ((3680, 3689), "Technology"),
    ((3690, 3699), "Technology"),
    ((3700, 3799), "Industrials"),
    ((3800, 3826), "Industrials"),
    ((3827, 3851), "Health Care"),
    ((3860, 3999), "Consumer"),
    ((4000, 4499), "Industrials"),
    ((4500, 4599), "Industrials"),
    ((4600, 4699), "Energy"),
    ((4700, 4799), "Industrials"),
    ((4800, 4899), "Communications"),
    ((4900, 4999), "Utilities"),
    ((5000, 5199), "Industrials"),
    ((5200, 5999), "Consumer"),
    ((6000, 6499), "Financials"),
    ((6500, 6599), "Real Estate"),
    ((6600, 6799), "Financials"),
    ((7000, 7299), "Consumer"),
    ((7300, 7369), "Technology"),
    ((7370, 7379), "Technology"),
    ((7380, 7899), "Communications"),
    ((7900, 7999), "Consumer"),
    ((8000, 8099), "Health Care"),
    ((8100, 8199), "Industrials"),
    ((8200, 8299), "Consumer"),
    ((8300, 8399), "Health Care"),
    ((8400, 8999), "Industrials"),
]


def sector_for_sic(sic) -> str:
    """Coarse sector for a SEC SIC code. Anything unmapped is UNKNOWN_SECTOR."""
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return UNKNOWN_SECTOR
    for (low, high), sector in SECTOR_SIC_RANGES:
        if low <= code <= high:
            return sector
    return UNKNOWN_SECTOR


def sectors_for_symbols(connection: sqlite3.Connection, symbols) -> dict:
    """Map symbol -> sector from the most recent recorded screen for each.

    Symbols with no screen, or screened before SIC was persisted, are simply
    absent from the result; the caller buckets those as UNKNOWN_SECTOR. Never
    raises -- a lookup failure must not take down an order preview.
    """
    wanted = [str(s).strip().upper() for s in (symbols or []) if s]
    if not wanted:
        return {}
    sectors = {}
    try:
        placeholders = ",".join("?" for _ in wanted)
        rows = connection.execute(
            f"""
            SELECT symbol, payload FROM shariah_screens
            WHERE symbol IN ({placeholders})
            ORDER BY id ASC
            """,
            wanted,
        ).fetchall()
    except Exception:
        return {}

    for row in rows:
        try:
            payload = json.loads(row["payload"]) if row["payload"] else {}
            sic = payload.get("sic")
            if sic is None:
                continue
            # Ascending order means a later screen overwrites an earlier one.
            sectors[str(row["symbol"]).strip().upper()] = sector_for_sic(sic)
        except Exception:
            continue
    return sectors


def sector_exposure(positions, sectors: dict) -> dict:
    """Total exposure per sector across the held positions."""
    totals: dict = {}
    for position in positions or []:
        try:
            symbol = str(position.get("symbol") or "").strip().upper()
            value = float(position.get("exposure_value") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        # `nan <= 0` is False, so a NaN exposure used to escape this filter and poison
        # the sector total. Treated as unusable rather than as zero: a position that
        # cannot be valued is not a position worth nothing.
        if not is_finite_number(value) or value <= 0:
            continue
        sector = sectors.get(symbol, UNKNOWN_SECTOR)
        totals[sector] = round(totals.get(sector, 0.0) + value, 4)
    return totals


def check_sector_concentration(
    *,
    symbol: str,
    added_exposure: float,
    positions,
    sectors: dict,
    account_equity: float,
    max_sector_pct,
) -> dict:
    """Would this order push its sector past the cap? PASS/REJECT with the numbers.

    Follows risk_checks.check_order's shape: a dict with a status key, the
    figures behind the decision, and a message written for a human.
    """
    normalized = str(symbol or "").strip().upper()
    sector = sectors.get(normalized, UNKNOWN_SECTOR)

    try:
        cap = float(max_sector_pct) if max_sector_pct is not None else 0.0
    except (TypeError, ValueError):
        # A malformed limit used to become 0.0, which the `cap <= 0` branch below turns
        # into PASS / sector_limit_disabled. A typo in a risk limit must not disable the
        # gate it configures.
        cap = float("nan")

    current = sector_exposure(positions, sectors).get(sector, 0.0)
    # The symbol's own existing exposure is already inside `current`, so the
    # projection is simply the sector total plus what this order adds.
    # `max(0.0, nan)` returns 0.0, so an order whose notional could not be computed used
    # to add *nothing* to the sector total and arrive at the gate looking clean. That is
    # the most dangerous variant of this bug: a fail-closed signal flattened into a
    # passing value before the gate sees it. Kept non-finite so the check below refuses.
    # Not float(added_exposure or 0): that raises on a non-numeric and treats a real 0.0
    # as missing. finite_or hands anything unusable straight to the refusal below.
    added = finite_or(added_exposure, float("nan"))
    projected = round(current + max(0.0, added), 4) if is_finite_number(added) else float("nan")

    base = {
        "sector": sector,
        "symbol": normalized,
        "current_exposure": current,
        "projected_exposure": projected,
        "max_sector_pct": cap,
    }

    if not is_finite_number(cap):
        return {
            **base,
            "status": "REJECT",
            "reason": "sector_limit_misconfigured",
            "projected_pct": None,
            "message": (
                f"The sector exposure limit is not a usable number ({max_sector_pct!r}), "
                "so the sector concentration check cannot run. Treated as blocking."
            ),
        }
    if cap <= 0:
        return {**base, "status": "PASS", "reason": "sector_limit_disabled", "projected_pct": None}

    try:
        equity = float(account_equity)
    except (TypeError, ValueError):
        equity = 0.0
    # Previously PASS. `_period_loss_pct` returns `inf` for exactly this condition in
    # order to fail CLOSED, so two modules answered one question in opposite directions.
    # An account whose equity cannot be read is an account whose limits cannot be checked.
    if not is_finite_number(equity) or equity <= 0:
        return {
            **base,
            "status": "REJECT",
            "reason": "account_equity_unavailable",
            "projected_pct": None,
            "message": (
                "Account equity could not be read, so sector concentration cannot be "
                "measured against it. Treated as blocking."
            ),
        }

    projected_pct = (
        round((projected / equity) * 100, 4) if is_finite_number(projected) else float("nan")
    )
    if not is_finite_number(projected_pct):
        return {
            **base,
            "status": "REJECT",
            "reason": "sector_exposure_unknown",
            "projected_pct": None,
            "message": (
                f"{sector} exposure could not be valued for {normalized}, so the sector "
                "limit cannot be checked. Treated as blocking."
            ),
        }
    if projected_pct > cap:
        return {
            **base,
            "status": "REJECT",
            "reason": "sector_concentration_limit",
            "projected_pct": projected_pct,
            "message": (
                f"{normalized} would take {sector} exposure to {projected_pct:.2f}% of paper "
                f"account equity, above the {cap:.2f}% sector limit."
            ),
        }
    return {
        **base,
        "status": "PASS",
        "reason": "within_sector_limit",
        "projected_pct": projected_pct,
        "message": (
            f"{sector} exposure would be {projected_pct:.2f}% of paper account equity, "
            f"within the {cap:.2f}% sector limit."
        ),
    }
