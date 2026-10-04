"""Generate the Trading Mandate from the values the system actually enforces.

WHY THIS EXISTS

A Shariah expert reviewing this project made the point that clicking "approve"
does not by itself establish *niyyah* / *qasd*. The distinction he drew is
between **authorization of the transaction** and **intention underlying it**:
where a system executes within parameters a human consciously authorized, the
human remains the accountable party and the software is a tool. That is
consistent with AI having no *ahliyyah*.

His guidance was to worry less about whether the human understands every
computational step, and more about whether they **knowingly authorized the
mandate, parameters and transaction** -- documented as a clear trading mandate
stating eligible securities, screening criteria, risk parameters, execution
conditions, and the circumstances under which trades may occur.

WHY IT IS GENERATED RATHER THAN WRITTEN

A hand-written mandate is a claim about the system. A generated one is a
description of it. Every number below is read from the same `config.load_settings()`
and the same gate modules the running system uses, so the mandate cannot quietly
drift away from what is actually enforced -- and if someone changes a limit
without meaning to, regenerating the mandate shows it.

The mandate does NOT establish that the system is Shariah-compliant, and must
not be presented as doing so. It states what the owner is authorizing. Whether
those parameters are themselves acceptable is a question for qualified scholars.

    .\\.venv\\Scripts\\python.exe backend\\trading_mandate.py
    .\\.venv\\Scripts\\python.exe backend\\trading_mandate.py --out docs/TRADING_MANDATE.md
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import config
from option_permissibility import check_option_permissibility
from option_structure_gate import ALLOWED_STRUCTURES, REJECTED_STRUCTURES


def _fmt_pct(value: float) -> str:
    return f"{value:g}%"


def _option_rows(determination: dict | None) -> tuple[str, str]:
    """Section 5 step and section 6 rows for options, from the determination in force.

    Read from option_permissibility rather than option_structure_gate alone: until
    2026-10-05 this listed the structure gate's allowed table as "permitted" while the
    determination blocked every option contract, so the mandate described a permission
    the system did not grant.
    """
    allowed = ", ".join(f"`{s}`" for s in sorted(ALLOWED_STRUCTURES))
    rejected = ", ".join(f"`{s}`" for s in sorted(REJECTED_STRUCTURES))
    verdict = check_option_permissibility(determination=determination)
    if verdict["status"] == "PASS":
        step = "For options, the option-structure gate returns PASS."
        rows = (
            f"| Rejected option structures | {rejected} |\n"
            f"| Permitted option structures | {allowed} — **pending scholar review**; "
            "loosened for a deadline and not re-vetted |"
        )
        return step, rows

    in_force = verdict["determination"]
    recorded = in_force.get("recorded_on", "unrecorded")
    authority = in_force.get("authority", "no authority recorded")
    step = (
        "No option contract may be entered into. Options are **not permitted** under the "
        f"determination recorded on {recorded}, and every option order is refused at "
        "preview, at approval and at strategy proposal."
    )
    adopted = in_force.get("adopted_on")
    adoption = f", adopted by the owner on {adopted}" if adopted else ""
    rows = (
        "| Option contracts | **Not permitted**, including a covered call on owned shares "
        f"and a fully cash-secured put — determination recorded {recorded} ({authority})"
        f"{adoption}. |\n"
        "| Option structures | Not reachable while the above holds. The structure gate's "
        f"table (refuses {rejected}; would otherwise accept {allowed}) remains in the code "
        "only so a future change is reviewable. |"
    )
    return step, rows


def build_mandate(settings, determination: dict | None = None) -> str:
    """``determination`` is a test seam, as in check_option_permissibility. main() never passes it."""
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    option_step, option_rows = _option_rows(determination)
    strategies = ", ".join(f"`{s}`" for s in settings.quant_strategies) or "none configured"

    return f"""# Trading Mandate — Amanah Trader

**Generated {generated} from the running configuration.** Do not edit by hand:
regenerate with `python backend/trading_mandate.py`. Every figure below is read
from the same settings and gate modules the system enforces at run time, so this
document cannot drift from actual behaviour.

---

## What this document is, and is not

This states the parameters the account owner authorizes in advance. It is the
record that the human knowingly authorized a mandate, rather than merely clicking
approve on an opaque suggestion.

**It does not certify Shariah compliance**, of this system or of any trade made
under it. It has not been validated by a qualified Shariah scholar. Being on the
Securities Commission Malaysia SAC list of Shariah-compliant securities settles
the status of a *security*; it certifies neither a trading strategy nor an
automated system.

A systematic or algorithmic strategy does not, by itself, constitute *maysir*.
Shariah compliance depends on the underlying securities, transaction structure,
trading mechanism and applicable Shariah principles.

---

## 1. Eligible securities

| | |
|---|---|
| Malaysian equities | Only securities on the **active, approved SC SAC publication**. Classification is the SC's, not this system's. |
| US equities | Screened by a **self-built ratio screen** over SEC EDGAR filings. `sec_edgar_screen.py` states in its own docstring that this is *not a certified screening service*: business activity is approximated by SIC code, and XBRL cannot separate Islamic from conventional instruments, so both ratios are overstated. Errors are in the direction of rejection. |
| Market routing | `detect_market()` — an all-digit symbol is Malaysian, otherwise US. The same function the gate routes on, so screening and sizing cannot disagree. |

## 2. Screening criteria

The Shariah gate returns **three** states and never collapses them:

- **PASS** — the authority confirms eligibility.
- **REJECT** — the authority confirms ineligibility.
- **UNKNOWN** — no approved publication covers this security. **Blocks trading**,
  but is never recorded as a ruling of ineligibility.

That distinction is deliberate: conflating "we could not confirm" with "the
authority said no" would either invent a divestment obligation or conceal one.

## 3. Risk parameters

Authorized limits, all enforced server-side at approval time from live portfolio
state rather than from anything the client claims:

| Limit | Value |
|---|---|
| Maximum single position | {_fmt_pct(settings.max_position_pct)} of account equity |
| Maximum total exposure | {_fmt_pct(settings.max_total_exposure_pct)} of account equity |
| Maximum sector exposure | {_fmt_pct(settings.max_sector_exposure_pct)} of account equity |
| Maximum loss per trade | {_fmt_pct(settings.max_loss_per_trade_pct)} of account equity |
| Maximum daily loss | {_fmt_pct(settings.max_daily_loss_pct)} of account equity |
| Maximum weekly loss | {_fmt_pct(settings.max_weekly_loss_pct)} of account equity |
| Maximum orders per day | {settings.max_orders_per_day} |
| Account equity basis | {settings.paper_account_equity:,.2f} |

**On "maximum loss per trade":** no stop-loss model exists in this system, so the
only bound on a single trade's downside that is actually true given long-only,
unlevered constraints is the position's entire cost. That figure is therefore
applied against the full notional — conservative, not precise, and deliberately
so.

## 4. Execution conditions

| | |
|---|---|
| Trading mode | `{settings.trading_mode}` |
| Broker submission enabled | `{settings.paper_execution_enabled}` |
| Execution adapter | `{settings.paper_execution_adapter}` |
| Broker environment | `{settings.alpaca_mode}` — paper only, pinned in `config.load_settings()` and hardcoded in the adapter host |
| Market data provider | `{settings.market_data_provider}` |
| Quant strategies authorized | {strategies} |
| Human confirmation | The phrase **`EXECUTE PAPER`** must be typed before any order reaches the broker |

## 5. When a trade may occur

All of the following, with no exceptions and no override path:

1. The Shariah gate returns **PASS** for the security.
2. {option_step}
3. The account gate confirms no leverage is available: the broker must report a
   buying-power multiplier of 1×. Alpaca offers no true cash account, so the account
   is a margin agreement capped at 1×; whether that is acceptable is **pending scholar
   review** (`docs/shariah-policy/margin-account-policy.md`).
4. Every risk limit in section 3 holds, recomputed from live state.
5. Market data is real. Synthetic or unverifiable prices **block** — a fallback to
   fixture data cannot produce a tradeable signal.
6. For equities, the quant strategy produces a BUY signal.
7. A human reviews the preview and approves it.
8. A human types the confirmation phrase.

A failure at any step stops the order. There is no path that submits an order
which failed a gate.

## 6. Excluded by design

| Excluded | Note |
|---|---|
| Short selling | Not supported at any layer |
| Margin / leverage | Account gate rejects any account the broker reports with leverage above 1×. A 1×-capped margin agreement passes; see section 5, step 3 |
| Multi-leg spreads | Rejected by the option-structure gate |
{option_rows}
| Live (non-paper) trading | Structurally impossible: no live host exists in the codebase |

## 7. Accountability

The software proposes and enforces. It does not decide to trade. Every order is
authorized by the account owner in advance through this mandate, and again
individually at approval and at execution.

Every decision — refusals included — is written to an append-only evidence trail
with the authority consulted, the source document hash, the as-of date, and the
market-data provenance.

---

**Authorized by:** ______________________  **Date:** ______________

*Signing this authorizes the parameters above, not any particular trade.*
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="write to this path instead of stdout")
    args = parser.parse_args()

    mandate = build_mandate(config.load_settings())

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mandate, encoding="utf-8")
        print(f"wrote {path}")
    else:
        print(mandate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
