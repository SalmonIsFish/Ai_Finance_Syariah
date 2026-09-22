# Trading Mandate — Amanah Trader

**Generated 2026-09-22 08:44 UTC from the running configuration.** Do not edit by hand:
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
| Maximum single position | 5% of account equity |
| Maximum total exposure | 25% of account equity |
| Maximum sector exposure | 20% of account equity |
| Maximum loss per trade | 0.5% of account equity |
| Maximum daily loss | 1% of account equity |
| Maximum weekly loss | 2% of account equity |
| Maximum orders per day | 5 |
| Account equity basis | 10,000.00 |

**On "maximum loss per trade":** no stop-loss model exists in this system, so the
only bound on a single trade's downside that is actually true given long-only,
unlevered constraints is the position's entire cost. That figure is therefore
applied against the full notional — conservative, not precise, and deliberately
so.

## 4. Execution conditions

| | |
|---|---|
| Trading mode | `approval` |
| Broker submission enabled | `True` |
| Execution adapter | `alpaca_mcp` |
| Broker environment | `paper` — paper only, pinned in `config.load_settings()` and hardcoded in the adapter host |
| Market data provider | `alpaca` |
| Quant strategies authorized | `S001`, `S002` |
| Human confirmation | The phrase **`EXECUTE PAPER`** must be typed before any order reaches the broker |

## 5. When a trade may occur

All of the following, with no exceptions and no override path:

1. The Shariah gate returns **PASS** for the security.
2. For options, the option-structure gate returns PASS.
3. The account gate confirms no standing *riba* exposure (cash account, no margin).
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
| Margin / leverage | Account gate rejects margin-enabled accounts |
| Multi-leg spreads | Rejected by the option-structure gate |
| Rejected option structures | `naked_call`, `naked_put`, `straddle`, `strangle` |
| Permitted option structures | `cash_secured_put`, `collar`, `covered_call`, `protective_put` — **pending scholar review**; loosened for a deadline and not re-vetted |
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
