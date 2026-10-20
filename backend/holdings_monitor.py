"""The Shariah monitor: holdings you report, screened and coloured every run.

This is the product's core loop, and it is deliberately separate from the paper
ledger. ``paper_positions`` is evidence of broker fills and must only ever be
written by reconciliation; what lives here is what the owner *says* they hold at
whatever broker they use (moomoo, IBKR, Webull...), typed in or imported from a
CSV export. Nothing in this module trades, and it never holds broker credentials.

One run (``run_monitor``) does five things, in order:

1. screens every reported holding through the one gate path
   (holdings_compliance.screen_holdings -> agents.shariah_agent);
2. colours it GREEN / YELLOW / RED (traffic_light), including review flags and
   the owner's escalations recorded here;
3. advances the disposal clock for RED holdings only, and estimates purification;
4. resolves sales recorded while a holding was YELLOW, once it turns RED or GREEN;
5. compares each colour with the last run and records a change as an alert, which
   the Telegram bridge relays.

The owner's position on YELLOW (2026-10-20): "under review -- do not buy or sell
until it resolves". The app cannot enforce that -- the owner trades at their own
broker -- so a sale made anyway is recorded, and if the stock resolves RED the
gain above cost on that sale is owed to baitulmal. This is the owner's
application of the SC SAC methodology, not a ruling; whether holding through a
YELLOW is better than selling is a question for a Shariah adviser.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timezone

import holdings_compliance
from holdings_compliance import ALERT_NON_COMPLIANT, ALERT_UNCONFIRMED
from numeric_guards import is_finite_number
from traffic_light import GREEN, RED, YELLOW

# Self-reported holdings are keyed in holdings_compliance_flags under this
# prefix, so the paper ledger's disposal clock and this one never clear each
# other's rows.
ACCOUNT_PREFIX = holdings_compliance.MONITOR_ACCOUNT_PREFIX

UNSCREENED = "UNSCREENED"

ADVICE = {
    GREEN: "Compliant on the latest report. Nothing to do.",
    YELLOW: (
        "Under review. Do not buy more or sell until it resolves -- you will be "
        "notified when it turns green or red."
    ),
    RED: (
        "Not compliant. Plan to dispose of it within the disposal window; keep your "
        "cost, and the gain above cost is owed to baitulmal. Confirm against the SC "
        "SAC guidance on disposal timing."
    ),
}

METHODOLOGY_SC = "SC_MY_SAC"
METHODOLOGY_AAOIFI = "AAOIFI"
METHODOLOGIES = (METHODOLOGY_SC, METHODOLOGY_AAOIFI)
BASIS = {
    METHODOLOGY_SC: (
        "Applies the SC Malaysia SAC screening methodology to the latest annual "
        "filing. Not a Shariah ruling and not reviewed by a Shariah adviser."
    ),
    METHODOLOGY_AAOIFI: (
        "Applies AAOIFI Shariah Standard 21 ratios (debt and cash under 30% of market "
        "capitalisation, using the current price rather than a multi-year average) to "
        "US holdings; Bursa holdings still follow the SC list. A comparison view, not "
        "a Shariah ruling, and not reviewed by a Shariah adviser."
    ),
}

REVIEW_KINDS = {"flag", "escalation", "note"}
REVIEW_SOURCES = {"owner", "model"}

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")
# A market prefix the monitor can screen, and ones it cannot yet. A Hong Kong
# code such as 00700 is all digits, which detect_market would read as Bursa --
# so an HK row is refused outright rather than screened as the wrong market.
_SUPPORTED_PREFIXES = {"US"}
_UNSUPPORTED_PREFIXES = {"HK", "SH", "SZ", "SG", "JP", "CN", "AU"}

_HEADER_ALIASES = {
    "symbol": {"symbol", "ticker", "code", "stock", "stockcode", "instrument", "security"},
    "quantity": {"quantity", "qty", "shares", "position", "holdings", "quantityheld", "units"},
    "average_cost": {
        "averagecost",
        "avgcost",
        "avgprice",
        "averageprice",
        "costprice",
        "averagecostprice",
        "unitcost",
        "dilutedcost",
        "costpershare",
    },
    "cost_basis": {"costbasis", "totalcost", "bookcost", "costvalue"},
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_monitor_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS monitor_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            quantity REAL NOT NULL,
            average_cost REAL NOT NULL,
            cost_basis REAL NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(symbol, broker)
        );
        CREATE TABLE IF NOT EXISTS monitor_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            quantity REAL NOT NULL,
            proceeds REAL NOT NULL,
            cost_basis REAL NOT NULL,
            sold_at TEXT NOT NULL,
            light_at_sale TEXT NOT NULL,
            resolved_light TEXT,
            resolved_at TEXT,
            purification_due REAL
        );
        CREATE TABLE IF NOT EXISTS monitor_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            report_date TEXT,
            kind TEXT NOT NULL,
            code TEXT NOT NULL,
            detail TEXT NOT NULL,
            quote TEXT,
            activity_pct REAL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS monitor_lights (
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            light TEXT NOT NULL,
            reasons TEXT NOT NULL,
            report_date TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (symbol, broker)
        );
        CREATE TABLE IF NOT EXISTS monitor_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS monitor_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            broker TEXT NOT NULL,
            from_light TEXT,
            to_light TEXT NOT NULL,
            message TEXT NOT NULL
        );
        """
    )
    connection.commit()


def get_methodology(connection: sqlite3.Connection) -> str:
    ensure_monitor_tables(connection)
    row = connection.execute(
        "SELECT value FROM monitor_settings WHERE key = 'methodology'"
    ).fetchone()
    return row[0] if row and row[0] in METHODOLOGIES else METHODOLOGY_SC


def set_methodology(connection: sqlite3.Connection, methodology: str) -> dict:
    """Choose the screening standard for the monitor. The order gate is unaffected:
    it always screens on SC SAC."""
    ensure_monitor_tables(connection)
    if methodology not in METHODOLOGIES:
        raise HoldingError(f"methodology must be one of {list(METHODOLOGIES)}")
    connection.execute(
        "INSERT INTO monitor_settings (key, value, updated_at) VALUES ('methodology', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (methodology, _utc_now()),
    )
    connection.commit()
    return {"status": "OK", "methodology": methodology}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class HoldingError(ValueError):
    """An input the monitor refuses rather than guesses at."""


def normalize_symbol(raw) -> str:
    text = str(raw or "").strip().upper()
    if "." in text:
        prefix, _, rest = text.partition(".")
        if prefix in _UNSUPPORTED_PREFIXES:
            raise HoldingError(f"{text}: the {prefix} market is not supported yet (US first)")
        if prefix in _SUPPORTED_PREFIXES:
            text = rest
    if not _SYMBOL_RE.match(text):
        raise HoldingError(f"'{raw}' is not a recognisable ticker")
    return text


def normalize_broker(raw) -> str:
    text = re.sub(r"[^a-z0-9_-]", "", str(raw or "manual").strip().lower())
    return text or "manual"


def _number(raw, label: str) -> float:
    if isinstance(raw, bool):
        raise HoldingError(f"{label} must be a number")
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = re.sub(r"[,\s$]|USD|RM|MYR", "", str(raw or ""), flags=re.IGNORECASE)
        if not text:
            raise HoldingError(f"{label} is missing")
        try:
            value = float(text)
        except ValueError:
            raise HoldingError(f"{label} '{raw}' is not a number") from None
    if not is_finite_number(value):
        raise HoldingError(f"{label} is not a finite number")
    return value


def _validated(symbol, quantity, average_cost) -> tuple[str, float, float]:
    clean_symbol = normalize_symbol(symbol)
    qty = _number(quantity, "quantity")
    avg = _number(average_cost, "average cost")
    if qty <= 0:
        raise HoldingError(f"{clean_symbol}: quantity must be greater than zero")
    if avg < 0:
        raise HoldingError(f"{clean_symbol}: average cost cannot be negative")
    return clean_symbol, qty, avg


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------


def upsert_holding(
    connection: sqlite3.Connection,
    *,
    symbol,
    quantity,
    average_cost,
    broker="manual",
    source: str = "manual",
) -> dict:
    """Add a holding, or replace the quantity and cost of an existing one."""
    ensure_monitor_tables(connection)
    clean_symbol, qty, avg = _validated(symbol, quantity, average_cost)
    clean_broker = normalize_broker(broker)
    now = _utc_now()
    connection.execute(
        """
        INSERT INTO monitor_holdings
            (symbol, broker, quantity, average_cost, cost_basis, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, broker) DO UPDATE SET
            quantity = excluded.quantity,
            average_cost = excluded.average_cost,
            cost_basis = excluded.cost_basis,
            source = excluded.source,
            updated_at = excluded.updated_at
        """,
        (clean_symbol, clean_broker, qty, avg, round(qty * avg, 6), source, now, now),
    )
    connection.commit()
    return {"status": "OK", "symbol": clean_symbol, "broker": clean_broker}


def remove_holding(connection: sqlite3.Connection, *, symbol, broker="manual") -> dict:
    """Delete a holding entered by mistake. A real disposal is record_sale."""
    ensure_monitor_tables(connection)
    clean_symbol = normalize_symbol(symbol)
    clean_broker = normalize_broker(broker)
    cursor = connection.execute(
        "DELETE FROM monitor_holdings WHERE symbol = ? AND broker = ?",
        (clean_symbol, clean_broker),
    )
    connection.commit()
    if cursor.rowcount == 0:
        return {"status": "NOT_FOUND", "symbol": clean_symbol, "broker": clean_broker}
    return {"status": "OK", "symbol": clean_symbol, "broker": clean_broker}


def list_holdings(connection: sqlite3.Connection) -> list[dict]:
    ensure_monitor_tables(connection)
    rows = connection.execute(
        "SELECT symbol, broker, quantity, average_cost, cost_basis, source, updated_at "
        "FROM monitor_holdings ORDER BY symbol, broker"
    ).fetchall()
    return [dict(row) for row in rows]


def _header_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def parse_holdings_csv(text: str) -> dict:
    """Read a broker's positions export. All-or-nothing: any bad row fails the file.

    Columns are matched by name, not position, against common broker spellings
    (Symbol/Ticker/Code, Quantity/Qty/Shares, Average Cost/Avg Price/Cost Price).
    A file whose columns cannot be identified is refused rather than guessed --
    a misread cost price would produce a wrong purification figure.
    """
    stream = io.StringIO(str(text or "").lstrip("﻿"))
    reader = csv.DictReader(stream)
    headers = reader.fieldnames or []
    mapping = {}
    for header in headers:
        key = _header_key(header)
        for field, aliases in _HEADER_ALIASES.items():
            if key in aliases and field not in mapping:
                mapping[field] = header
    missing = [f for f in ("symbol", "quantity") if f not in mapping]
    if "average_cost" not in mapping and "cost_basis" not in mapping:
        missing.append("average_cost or cost_basis")
    if missing:
        return {
            "status": "REJECTED",
            "errors": [f"could not find a column for: {', '.join(missing)}"],
            "headers": headers,
            "rows": [],
        }

    rows, errors, seen = [], [], set()
    for line_number, record in enumerate(reader, start=2):
        raw_symbol = (record.get(mapping["symbol"]) or "").strip()
        if not raw_symbol and not any((value or "").strip() for value in record.values()):
            continue  # blank line
        try:
            qty = _number(record.get(mapping["quantity"]), "quantity")
            if qty == 0:
                continue  # a closed position some exports still list
            if "average_cost" in mapping:
                avg = _number(record.get(mapping["average_cost"]), "average cost")
            else:
                avg = _number(record.get(mapping["cost_basis"]), "cost basis") / qty
            symbol, qty, avg = _validated(raw_symbol, qty, avg)
        except HoldingError as exc:
            errors.append(f"line {line_number}: {exc}")
            continue
        if symbol in seen:
            errors.append(f"line {line_number}: {symbol} appears more than once")
            continue
        seen.add(symbol)
        rows.append({"symbol": symbol, "quantity": qty, "average_cost": round(avg, 6)})

    if errors:
        return {"status": "REJECTED", "errors": errors, "headers": headers, "rows": []}
    if not rows:
        return {"status": "REJECTED", "errors": ["no holdings found in the file"], "rows": []}
    return {"status": "OK", "errors": [], "headers": headers, "columns": mapping, "rows": rows}


def import_holdings_csv(connection: sqlite3.Connection, text: str, *, broker) -> dict:
    """Replace every holding for one broker with the contents of its export."""
    ensure_monitor_tables(connection)
    parsed = parse_holdings_csv(text)
    if parsed["status"] != "OK":
        return parsed
    clean_broker = normalize_broker(broker)
    connection.execute("DELETE FROM monitor_holdings WHERE broker = ?", (clean_broker,))
    for row in parsed["rows"]:
        upsert_holding(connection, broker=clean_broker, source="csv", **row)
    return {
        "status": "OK",
        "broker": clean_broker,
        "imported": len(parsed["rows"]),
        "columns": parsed["columns"],
    }


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


def current_light(connection: sqlite3.Connection, symbol: str, broker: str) -> str | None:
    ensure_monitor_tables(connection)
    row = connection.execute(
        "SELECT light FROM monitor_lights WHERE symbol = ? AND broker = ?", (symbol, broker)
    ).fetchone()
    return row[0] if row else None


def record_sale(
    connection: sqlite3.Connection, *, symbol, quantity, price, broker="manual", sold_at=None
) -> dict:
    """Record a sale the owner made at their broker, and reduce the holding.

    The colour at the moment of sale decides what is owed:
    RED -> the gain above cost is owed now; GREEN -> nothing; YELLOW or never
    screened -> pending, resolved by the next run that colours it RED or GREEN.
    """
    ensure_monitor_tables(connection)
    clean_symbol = normalize_symbol(symbol)
    clean_broker = normalize_broker(broker)
    qty = _number(quantity, "quantity")
    sale_price = _number(price, "sale price")
    if qty <= 0 or sale_price < 0:
        raise HoldingError("quantity must be positive and price non-negative")

    holding = connection.execute(
        "SELECT quantity, average_cost FROM monitor_holdings WHERE symbol = ? AND broker = ?",
        (clean_symbol, clean_broker),
    ).fetchone()
    if holding is None:
        raise HoldingError(f"no {clean_symbol} holding recorded at {clean_broker}")
    held, average_cost = float(holding[0]), float(holding[1])
    if qty > held + 1e-9:
        raise HoldingError(f"cannot sell {qty:g} {clean_symbol}; only {held:g} recorded")

    cost_basis = round(qty * average_cost, 6)
    proceeds = round(qty * sale_price, 6)
    light = current_light(connection, clean_symbol, clean_broker) or UNSCREENED
    resolved_light, resolved_at, due = None, None, None
    if light in (RED, GREEN):
        resolved_light, resolved_at = light, _utc_now()
        due = (
            holdings_compliance.purification_due(cost_basis=cost_basis, proceeds=proceeds)
            if light == RED
            else 0.0
        )

    connection.execute(
        """
        INSERT INTO monitor_sales
            (symbol, broker, quantity, proceeds, cost_basis, sold_at, light_at_sale,
             resolved_light, resolved_at, purification_due)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_symbol,
            clean_broker,
            qty,
            proceeds,
            cost_basis,
            sold_at or _utc_now(),
            light,
            resolved_light,
            resolved_at,
            due,
        ),
    )
    remaining = held - qty
    if remaining <= 1e-9:
        connection.execute(
            "DELETE FROM monitor_holdings WHERE symbol = ? AND broker = ?",
            (clean_symbol, clean_broker),
        )
    else:
        connection.execute(
            "UPDATE monitor_holdings SET quantity = ?, cost_basis = ?, updated_at = ? "
            "WHERE symbol = ? AND broker = ?",
            (
                remaining,
                round(remaining * average_cost, 6),
                _utc_now(),
                clean_symbol,
                clean_broker,
            ),
        )
    connection.commit()

    warning = None
    if light in (YELLOW, UNSCREENED):
        warning = (
            "This holding was under review when sold. If it resolves RED, the gain "
            "above cost on this sale will be owed to baitulmal."
        )
    return {
        "status": "OK",
        "symbol": clean_symbol,
        "broker": clean_broker,
        "light_at_sale": light,
        "purification_due": due,
        "pending": resolved_light is None,
        "warning": warning,
    }


def list_sales(connection: sqlite3.Connection) -> list[dict]:
    ensure_monitor_tables(connection)
    rows = connection.execute("SELECT * FROM monitor_sales ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def _resolve_pending_sales(connection, symbol: str, broker: str, light: str) -> int:
    if light not in (RED, GREEN):
        return 0
    pending = connection.execute(
        "SELECT id, proceeds, cost_basis FROM monitor_sales "
        "WHERE symbol = ? AND broker = ? AND resolved_light IS NULL",
        (symbol, broker),
    ).fetchall()
    now = _utc_now()
    for sale_id, proceeds, cost_basis in pending:
        due = (
            holdings_compliance.purification_due(cost_basis=cost_basis, proceeds=proceeds)
            if light == RED
            else 0.0
        )
        connection.execute(
            "UPDATE monitor_sales SET resolved_light = ?, resolved_at = ?, purification_due = ? "
            "WHERE id = ?",
            (light, now, due, sale_id),
        )
    return len(pending)


# ---------------------------------------------------------------------------
# Reviews: flags, owner escalations, notes
# ---------------------------------------------------------------------------


def add_review(
    connection: sqlite3.Connection,
    *,
    symbol,
    kind: str,
    detail: str,
    source: str,
    report_date: str | None = None,
    code: str | None = None,
    quote: str | None = None,
    activity_pct=None,
) -> dict:
    """Record a review against one annual report of one company.

    A review applies only to the report it names: when a newer annual report
    arrives, the screen runs fresh and old reviews lapse. That is how a YELLOW
    clears and how an escalation ends -- never by a manual "mark green".

    Only the owner may escalate. A model's review can be a flag (YELLOW) or a
    note (no effect); it can never escalate, and nothing here can clear a colour.
    """
    ensure_monitor_tables(connection)
    if kind not in REVIEW_KINDS:
        raise HoldingError(f"kind must be one of {sorted(REVIEW_KINDS)}")
    if source not in REVIEW_SOURCES:
        raise HoldingError(f"source must be one of {sorted(REVIEW_SOURCES)}")
    if kind == "escalation" and source != "owner":
        raise HoldingError("only the owner can escalate a holding to RED")
    text = str(detail or "").strip()
    if not text:
        raise HoldingError("a review needs a detail explaining it")
    pct = None
    if activity_pct is not None:
        pct = _number(activity_pct, "activity percentage")
        if not 0 <= pct <= 100:
            raise HoldingError("activity percentage must be between 0 and 100")

    clean_symbol = normalize_symbol(symbol)
    default_code = {"flag": "flagged_for_review", "escalation": "owner_escalation"}.get(
        kind, "note"
    )
    cursor = connection.execute(
        """
        INSERT INTO monitor_reviews
            (symbol, report_date, kind, code, detail, quote, activity_pct, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            clean_symbol,
            report_date,
            kind,
            code or default_code,
            text,
            quote,
            pct,
            source,
            _utc_now(),
        ),
    )
    connection.commit()
    return {"status": "OK", "id": cursor.lastrowid, "symbol": clean_symbol, "kind": kind}


def reviews_for(connection: sqlite3.Connection, symbol: str, report_date: str | None) -> list:
    ensure_monitor_tables(connection)
    rows = connection.execute(
        "SELECT * FROM monitor_reviews WHERE symbol = ? AND report_date IS ? ORDER BY id",
        (symbol, report_date),
    ).fetchall()
    return [dict(row) for row in rows]


def _review_lookup(connection):
    def lookup(symbol: str, report_date: str | None):
        reviews = reviews_for(connection, symbol, report_date)
        flags = [r for r in reviews if r["kind"] == "flag"]
        escalations = [r for r in reviews if r["kind"] == "escalation"]
        return flags, escalations

    return lookup


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _alert_message(symbol: str, previous: str | None, light: str, reasons: list) -> str:
    why = "; ".join(r.get("detail", "") for r in reasons[:3] if r.get("detail"))
    head = f"{symbol}: {previous or 'new'} -> {light}"
    return f"{head}. {ADVICE[light]}" + (f" Why: {why}" if why else "")


def run_monitor(connection: sqlite3.Connection, *, evaluate=None, price_lookup=None) -> dict:
    """Screen, colour, clock, resolve sales and detect colour changes. Writes."""
    ensure_monitor_tables(connection)
    holdings = list_holdings(connection)
    methodology = get_methodology(connection)
    if evaluate is None:
        from agents.shariah_agent import evaluate_shariah

        def evaluate(symbol):
            # SC goes through the exact call the order gate makes.
            if methodology == METHODOLOGY_SC:
                return evaluate_shariah(symbol)
            return evaluate_shariah(symbol, methodology=methodology, price_lookup=price_lookup)

    positions = [
        {
            "symbol": h["symbol"],
            "account_suffix": ACCOUNT_PREFIX + h["broker"],
            "quantity": h["quantity"],
            "average_cost": h["average_cost"],
            "cost_basis": h["cost_basis"],
        }
        for h in holdings
    ]
    # A symbol fully sold while YELLOW is no longer held but still has to be
    # screened, or its pending sale could never resolve.
    held_keys = {(h["symbol"], h["broker"]) for h in holdings}
    for symbol, broker in connection.execute(
        "SELECT DISTINCT symbol, broker FROM monitor_sales WHERE resolved_light IS NULL"
    ).fetchall():
        if (symbol, broker) not in held_keys:
            positions.append(
                {
                    "symbol": symbol,
                    "account_suffix": ACCOUNT_PREFIX + broker,
                    "quantity": 0.0,
                    "average_cost": 0.0,
                    "cost_basis": 0.0,
                }
            )

    screening = holdings_compliance.screen_holdings(
        connection,
        evaluate=evaluate,
        positions=positions,
        review_lookup=_review_lookup(connection),
    )

    # The monitor's duties follow the colour, not the raw status: a mixed
    # business-activity block is REJECT to the order gate but YELLOW here, and
    # an owner escalation is RED here although the screen said PASS.
    alert_for_light = {RED: ALERT_NON_COMPLIANT, YELLOW: ALERT_UNCONFIRMED, GREEN: None}
    for holding in screening["holdings"]:
        holding["broker"] = holding["account_suffix"][len(ACCOUNT_PREFIX) :]
        holding["alert"] = alert_for_light[holding["traffic_light"]["light"]]
    screening["flagged"] = [
        h for h in screening["holdings"] if h["alert"] is not None and h["quantity"] > 0
    ]
    screening = holdings_compliance.apply_disposal_clock(
        connection, screening, scope_prefix=ACCOUNT_PREFIX
    )
    purification = holdings_compliance.purification_ledger(screening, price_lookup=price_lookup)

    transitions = []
    now = _utc_now()
    resolved_sales = 0
    for holding in screening["holdings"]:
        symbol, broker = holding["symbol"], holding["broker"]
        light = holding["traffic_light"]["light"]
        reasons = holding["traffic_light"]["reasons"]
        holding["advice"] = ADVICE[light]
        resolved_sales += _resolve_pending_sales(connection, symbol, broker, light)
        if holding["quantity"] <= 0:
            continue
        previous = current_light(connection, symbol, broker)
        if previous != light and not (previous is None and light == GREEN):
            message = _alert_message(symbol, previous, light, reasons)
            connection.execute(
                "INSERT INTO monitor_alerts "
                "(created_at, symbol, broker, from_light, to_light, message) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (now, symbol, broker, previous, light, message),
            )
            transitions.append(
                {
                    "symbol": symbol,
                    "broker": broker,
                    "from": previous,
                    "to": light,
                    "message": message,
                }
            )
        connection.execute(
            """
            INSERT INTO monitor_lights (symbol, broker, light, reasons, report_date, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, broker) DO UPDATE SET
                light = excluded.light, reasons = excluded.reasons,
                report_date = excluded.report_date, updated_at = excluded.updated_at
            """,
            (
                symbol,
                broker,
                light,
                json.dumps(reasons, default=str),
                holding.get("report_date"),
                now,
            ),
        )
    connection.commit()

    held = [h for h in screening["holdings"] if h["quantity"] > 0]
    sales = list_sales(connection)
    return {
        "status": "OK",
        "screened_at": screening["screened_at"],
        "holdings": held,
        "counts": {
            light: sum(1 for h in held if h["traffic_light"]["light"] == light)
            for light in (GREEN, YELLOW, RED)
        },
        "transitions": transitions,
        "purification": purification,
        "sales": {
            "pending": [s for s in sales if s["resolved_light"] is None],
            "owed": [s for s in sales if (s["purification_due"] or 0) > 0],
            "resolved_this_run": resolved_sales,
            "total_owed_on_sales": round(sum(s["purification_due"] or 0 for s in sales), 2),
        },
        "disposal_window_days": screening.get("disposal_window_days"),
        "overdue_count": screening.get("overdue_count", 0),
        "methodology": methodology,
        "basis": BASIS[methodology],
    }


def list_alerts(connection: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    ensure_monitor_tables(connection)
    rows = connection.execute(
        "SELECT * FROM monitor_alerts ORDER BY id DESC LIMIT ?", (max(1, min(500, limit)),)
    ).fetchall()
    return [dict(row) for row in rows]
