# Amanah Trader — Claude Code Working Notes

## What this is

A local-first paper-trading control system for Shariah screening. Deterministic Python agents
screen every order for Shariah compliance, option-structure permissibility, account-level Riba
exposure, and risk limits **before** it can enter the approval queue, and a human must type a
confirmation phrase before anything reaches the broker.

The point is not the screening — plenty of products screen stocks. The point is that the gate
chain **enforces** and **proves**: an order that fails any gate cannot be submitted, and every
decision is recorded with its evidence.

### How to describe this system, precisely

**Do not call the system "Shariah-compliant."** It has not been validated by any qualified
Shariah scholar, and no such claim should appear in code, docs, UI or writing about it. What it
does is *apply* an authority's determination and prove that application: for Malaysian equities
the Securities Commission Malaysia SAC list; for US equities a self-built ratio screen that
`sec_edgar_screen.py`'s own docstring calls "not a certified screening service".

Three corrections from a Shariah-expert reviewer, to be kept in every description:

1. **Being on the SC's Shariah-compliant list does not certify this system.** The list settles
   the *security*. It says nothing about the trading strategy, the execution mechanism, or the
   system as a whole.
2. **Classification of a security ≠ permissibility of a trading strategy.** Keep these visibly
   separate, including in the UI.
3. **Never write that algorithmic trading is compliant because it is systematic.** The
   defensible form, verbatim: *"the use of a systematic or algorithmic trading strategy does
   not, by itself, constitute maysir; Shariah compliance depends on the underlying securities,
   transaction structure, trading mechanism and applicable Shariah principles."*

Also: **"Shariah-aware" is not an established term** in Islamic finance. Do not coin it. The
established concept is Shariah compliance.

The same reviewer confirmed that a deterministic rule-based system does **not** need XAI
(SHAP/LIME); the requirements are rule transparency, parameter control, auditability and human
oversight. That is an argument for the existing architecture, not for adding interpretability
tooling.

### Option contracts are blocked, pending a ruling (2026-09-23)

`option_permissibility.py` records a determination: **option contracts are not permitted**,
on the grounds reported for the OIC Islamic Fiqh Academy and Mufti Taqi Usmani -- gharar
(whether the option is exercised is unknown when the contract is made), maysir (each party
bets against the other), and the premium, a promise not being a valid subject of sale.
Source: https://www.islamicfinanceguru.com/articles/options-trading-halal-or-haram

**The cited sources address option contracts generally.** They do not separately treat a
covered call written against owned shares or a cash-secured put backed by settled cash,
which is the only thing this system ever did. That silence is **not** permission -- the
objections attach to the contract rather than to the side taken, and the writer is the
party receiving the contested premium. The question is open with the owner's Shariah
lecturer, so the system fails closed.

This exposed a structural gap, not a cosmetic one. `option_structure_gate` asked "is this a
permitted Level 1 structure, and is it collateralised?" and never asked the prior question:
*may an option contract be entered into at all, and on whose authority?* That question had
no representation in the code.

Mechanics:

- It is a **module constant, not an env var**. Flipping it is a deliberate code change,
  reviewed and visible in git -- not a runtime flag someone flips because an order was
  inconvenient. `test_option_permissibility.py` asserts no shipped module may even name
  `OPTION_POLICY_PERMITTED`.
- Blocked at three places, because one was not enough: `check_structure` (approval and,
  by consequence, execute), `agent_coordinator.evaluate_candidate` (preview -- which builds
  no `option_structure` at all, so an option used to preview `READY_FOR_APPROVAL` and be
  refused only one step later), and `propose_option_strategy` (before any chain fetch).
- The blocker code is `option_contracts_not_permitted`, kept distinct from
  `option_structure_rejected`. The second can be fixed by sizing; the first cannot be fixed
  at all. Collapsing them would tell the owner to add collateral for an order no collateral
  can make permissible.
- **No option code was deleted or commented out.** Every line stays live, and
  `check_option_permissibility(determination=...)` is a test-only seam that keeps the
  covered-call, cash-secured-put, margin and naked-structure arithmetic exercised under a
  permissive determination. Commented-out code rots -- Ruff will not check it and no test
  runs it. A ruling either way is one constant away, not a resurrection.

Broker: **Alpaca**, paper only.

## Safety rules (these override convenience)

- **Live trading must remain impossible.** `ALPACA_MODE` is pinned to `paper` in
  `config.load_settings()`, and `alpaca_paper_adapter.ALPACA_PAPER_BASE_URL` is a hardcoded
  paper host with no live URL anywhere in the module and no env var that can repoint it. The
  MCP transport forces `ALPACA_PAPER_TRADE=true` on the server it spawns.
- Broker submission stays opt-in behind `POST /paper/execute/{queue_id}` with the confirmation
  phrase `EXECUTE PAPER`.
- Do not bypass or weaken the Shariah, option-structure, account, risk, approval, or
  confirmation gates. If a gate is inconvenient, that is the gate working.
- Broker adapters perform **no** compliance checks of their own. They submit what an
  already-gated approval says to submit. Compliance lives in the gate chain, not the adapter.
- Never commit or print secrets from `backend/.env`. `.env` has never been committed — keep it
  that way. `backend/.env.example` documents the variables with empty values.
- Run the local tests before changing anything broker-facing.

## Architecture

```
Preview          POST /paper/preview
                   market data -> quant signal -> risk limits -> quote snapshot
                   carries asset_class + option_contract for option orders

Approval         POST /paper/approval
                   local_api.broker_account_context()  resolves live broker facts:
                     shares_held      <- portfolio_store.open_position_quantity
                     cash_collateral  <- settled cash (NEVER buying_power)
                     account_type     <- Alpaca multiplier (CASH / MARGIN)
                     uses_margin      <- conservative: account_type == MARGIN
                   shariah_candidate.build_shariah_candidate(...)  <-- the ONE gate entry point
                   approval_workflow.approve_candidate(...)
                     -> shariah_gate          is the company permissible?
                     -> option_structure_gate is the contract permissible?
                     -> account_shariah_gate  is the account free of Riba?
                   APPROVED_PAPER_READY | REJECT(reason)

Execution        POST /paper/execute/{queue_id}   requires "EXECUTE PAPER"
                   paper_execution.py re-audits the stored payload, re-checks reduce-only SELL
                   (equity only — a sell-to-open option is exempt; see Known limitations 4)
                   -> alpaca_paper_adapter.submit_paper_order(approval, broker)

Reconcile        POST /paper/reconcile/{queue_id}
                   -> portfolio_store.sync_filled_order  (equity only; see Known limitations)
```

### Module ownership

| Concern | Files |
|---|---|
| Broker + market data | `alpaca_paper_adapter.py`, `alpaca_market_data.py` |
| Gate chain | `shariah_gate.py`, `option_structure_gate.py`, `account_shariah_gate.py`, `shariah_candidate.py`, `agents/` |
| Compliance data | `sec_edgar_screen.py` (self-built SC screen), `zoya_compliance.py` (sandbox only) |
| Strategy | `option_strategy.py` — proposes a contract; approves nothing |
| Orchestration | `local_api.py`, `paper_execution.py`, `approval_workflow.py`, `agent_coordinator.py` |
| State | `approval_queue.py`, `portfolio_store.py`, `watchlist_store.py`, `shariah_screen_store.py` |
| Reporting | `portfolio_metrics.py` — risk-adjusted return from the broker equity curve; reports, never decides |
| Malaysian execution | `moomoo_*.py` — **no longer legacy as of 2026-09-22.** Alpaca has no Bursa access, so Moomoo is the only Malaysian route. See Known limitations 5. |

**`shariah_candidate.build_shariah_candidate()` is the only surface a broker adapter talks to.**
Adapters never import a gate module directly. If something a gate needs isn't reaching it, fix
the plumbing on the adapter side rather than changing the gate contract.

## Configuration

Everything is read via `os.getenv` in `config.load_settings()` from `backend/.env`.
See `backend/.env.example` for the full list. The ones that change behaviour most:

| Variable | Default | Notes |
|---|---|---|
| `ALPACA_API_KEY_ID` / `ALPACA_SECRET_KEY` | — | Paper keys. User sets these; never ask for them in chat. |
| `ALPACA_MODE` | `paper` | Any other value raises at startup. |
| `PAPER_EXECUTION_ADAPTER` | `disabled` | US adapter, and the global switch on `fake`/`disabled`. `disabled` \| `fake` \| `alpaca` \| `alpaca_mcp` \| `moomoo` |
| `PAPER_EXECUTION_ADAPTER_MY` | `disabled` | Bursa adapter. Off by design — see "Execution routing is per market". |
| `PAPER_EXECUTION_ENABLED` | `false` | Master lock on broker submission. |
| `MARKET_DATA_PROVIDER` | `alpaca` | `alpaca` \| `tiingo` |
| `ZOYA_ENVIRONMENT` | `sandbox` | **Sandbox returns randomized data.** See Known limitations. |
| `TRADING_MODE` | `approval` | `advisory` \| `approval` \| `autonomous_paper` |
| Risk limits | see example | `MAX_POSITION_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_LOSS_PER_TRADE_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_ORDERS_PER_DAY` |

`SHARIAH_UNIVERSE_PATH` and `SHARIAH_WIKI_PATH` default to committed in-repo copies
(`data/shariah-universe/`, `docs/shariah-policy/`), so a fresh clone runs with no `.env` at all.
Set them only to point at a larger private vault.

## Execution routing is per market

`broker_routing.adapter_for()` decides which broker submits an order, from the order's
own market rather than from one process-wide setting. It is the execution twin of
`market_data.provider_for`, and both route on the same `detect_market` the Shariah gate
uses, so pricing, screening and submission cannot disagree about one symbol.

- **US** → `PAPER_EXECUTION_ADAPTER` (production: `alpaca_mcp`).
- **MY** → `PAPER_EXECUTION_ADAPTER_MY`, **default `disabled`**.
- `fake` and `disabled` on the primary setting are a test mode and an off switch, so they
  answer for every market at once. Splitting them would let a test configure `fake` and
  still reach a real adapter for the other market.

Malaysian execution is off by default on purpose: no Moomoo order has ever reached a
broker and OpenD has no runbook for the droplet, so routing Bursa orders there by default
would enable an unproven path by implication rather than by decision.

**A correction to an earlier claim in this file.** It previously said a Malaysian order
"would be submitted to Alpaca, which has no Bursa access". That was wrong.
`alpaca_paper_adapter.SUPPORTED_REAL_MARKETS` is `{"US"}` and a non-US approval was
refused with `UNSUPPORTED_MARKET` before anything was built, so the outcome was always
safe. Two things genuinely were wrong, and both are fixed: the **status probe** was picked
by the same global flag, so a Bursa order was gated on whether the *Alpaca* account was
ready; and the refusal's stated reason described Alpaca's limits rather than the system's
decision. `test_broker_routing.py` pins the old behaviour as a fact so the record stays
honest.

`reconcile_for_approval` needed no change — it already dispatches on the `adapter`
recorded on the submission rather than the current setting, which is what makes a
configuration change mid-flight safe.

`GET /paper/status` reports `execution_markets`, so a client does not have to restate the
rule. The bridge relay reads it instead of hardcoding "Malaysia cannot execute", which it
used to and which was a second copy of a decision owned here.

## Alpaca integration

Two transports, both paper-only, selected by `PAPER_EXECUTION_ADAPTER`:

- **`alpaca`** — REST over stdlib `urllib` against `https://paper-api.alpaca.markets`.
- **`alpaca_mcp`** — the official MCP server via `uvx alpaca-mcp-server` (needs `uv`; override
  the command with `ALPACA_MCP_COMMAND`). The server wraps every payload in a
  `{"_alpaca_mcp_security": {...}, "data": {...}}` trust envelope; `unwrap_mcp_envelope()`
  strips it. That envelope is a prompt-injection guard aimed at LLM callers — this adapter reads
  named fields out of `data` deterministically and never treats tool output as instructions.

Options are **Level 1 only**: sell covered call, sell cash-secured put, and closing those shorts.
Multi-leg spreads are rejected by design. Contracts are built as OCC-21 symbols with
`time_in_force=day` and an explicit `position_intent`.

Market data (`alpaca_market_data.py`) mirrors `tiingo_prices.fetch_eod_prices` exactly — same
signature, same bar shape — so `market_data.summarize_history` switches providers with nothing
downstream noticing. It also exposes `fetch_option_contracts` / `fetch_option_snapshots` /
`fetch_option_chain` for strike selection. On a plan that cannot query recent SIP data it
automatically retries on the IEX feed and labels the source `alpaca_iex`.

## Running it

From the repo root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn local_api:app --app-dir backend --host 127.0.0.1 --port 8000
```

or `backend\run_local.ps1`. Dashboard: open `dashboard\index.html`.

Config check (prints booleans, never values):

```powershell
.\.venv\Scripts\python.exe backend\check_config.py
```

## Tests

**Two conventions coexist**, and the difference decides how a file must be run:

- **Plain scripts** — a `main()` that prints `PASS: ...`, run directly. Most of the suite.
- **pytest-native** — `test_p4_*.py` (Backtest Engine and historical simulation, where
  injecting deterministic mock data providers needs real fixture isolation), plus the newer
  SC Malaysia, risk-verdict and P3 suites. Do not convert legacy tests to pytest.

**A third shape exists and is easy to misread.** A few files — `test_approval_workflow.py` and
`test_us_pipeline_fixture.py` — have neither a `__main__` guard nor `def test_` functions, and
execute every assertion at module import. Run under pytest they import cleanly (so the
assertions *do* run, and pass) and then report `no tests ran` with exit code 5, which looks
exactly like a failure and is not one. Dispatch on whether a file has collectable `test_`
functions, **not** on whether it has a `__main__` guard. A one-off runner that got this
backwards on 2026-09-21 manufactured two false failures before the mistake was caught.

**105 `test_*.py` files on disk. 104 run and all 104 pass; `test_moomoo.py` is the one excluded**
(full census, 2026-09-23). Treat that number as a measurement with a date on it, not a fact —
and do not trust a hardcoded list in this file. The list that used to sit here enumerated 42
files and asserted "All 42 of those pass" while the suite had grown past 80, so a fresh reader
began from a false picture of what was actually verified. Enumerate the current set instead:

```powershell
.\.venv\Scripts\python.exe backend\run_all_tests.py            # census; exits 1 on any failure
.\.venv\Scripts\python.exe backend\run_all_tests.py --verbose  # stream each result
.\.venv\Scripts\python.exe backend\run_all_tests.py --filter sc_
```

`run_all_tests.py` implements the dispatch rule above so you do not have to re-derive it. Use
it for the census — but it is not a substitute for evidence.

Run the files relevant to your change **individually** and show their real output. A bare
`pytest backend\` is not acceptable as evidence: it collects nothing from the plain-script
files, which are most of the suite, and reports success regardless.

`test_moomoo.py` hangs by design — it drives the moomoo SDK directly, bypassing the
`check_moomoo_status()` TCP pre-check, because its purpose is to verify a *real* OpenD
connection when you have one running. That pre-check is why a closed OpenD port now fails in
~1.5s instead of the SDK's multi-minute retry/backoff, which is what used to make
`test_local_api_smoke.py` and the dashboard's status refresh hang. Both now complete fast with
no Moomoo gateway running.

### Testing conventions

- Network access goes through one replaceable module-level seam — `alpaca_request`,
  `alpaca_data_request`, `load_alpaca_mcp_client`, `check_alpaca_status`. Tests swap the seam;
  they never hit a real API.
- Prefer asserting the *request that was built*, not just the response that came back.
- When a test passes on the first run, break the code deliberately and confirm the test fails.
  Several real bugs in this repo were found exactly that way.

## Known limitations — read before claiming anything works

1. **US screening is live on SEC EDGAR, and every call is uncached.** `agents/shariah_agent.py`
   routes the US path to `sec_edgar_screen.check_us_symbol`, reporting `provider: SEC_EDGAR`.
   Zoya is no longer in any screening path; `zoya_compliance.py` remains only for reference,
   imported solely by `check_zoya.py`, whose purpose is checking Zoya. `us_strategy.py` and
   `explain_compliance.py` both route through the one entry point, and
   `test_single_screening_path.py` enforces that with a static AST import check, so a new
   parallel screening path fails a test the moment it is written.
   Read the module docstring before trusting a verdict: business activity is approximated by
   SIC code, and XBRL cannot separate Islamic from conventional instruments, so both ratios
   are overstated. Both approximations err toward rejection.

   **Cost:** a cold screen is a live SEC fetch of up to ~4.7 MB taking roughly 0.7–2 s, and
   `/paper/preview`, `/stock/{symbol}/profile` and `/stock/{symbol}/explain` all sit on it.
   `sec_edgar_cache.py` — a **temporary shim, not the screening store** — now serves a repeat
   fetch of the same URL from `backend/sec_edgar_cache/` for 24 h and holds live fetches to
   ~8 req/s, under SEC's 10 req/s guidance. Measured: three symbols cold 4.53 s, warm 0.66 s
   with no SEC request at all. It caches raw responses only, never verdicts, and never caches
   a failure — a 404 is a fact about SEC, not about the company, and the screen fails closed
   on ERROR. Tests are unaffected — they supply a `shariah_override` or swap the
   `sec_request` seam, and none reach SEC or the cache.

   **Every verdict is now logged.** `check_us_symbol` is a thin wrapper over
   `_screen_us_symbol` that appends the verdict to the append-only `shariah_screens` table
   (`shariah_screen_store.py`), readable at `GET /shariah/screens`. That is the *verdict* half
   of a two-layer store; `sec_edgar_cache.py` remains the raw-response half and is **not**
   replaced by it. (This used to cite `NEXT_STEPS.md`, which is gitignored as internal
   planning notes — so the reference dangled for anyone working from a clone.) The log is observability, not a gate: `_record_screen` is a
   swappable seam and a failed write is swallowed, because a locked SQLite file must never turn
   a COMPLIANT company into an ERROR. Malaysia is structurally excluded — the hook sits in the
   US screen, and `_evaluate_malaysia` does not pass through it.
2. **Option fills are audited but not tracked as positions.** *(Historical as of
   2026-09-23: option contracts are blocked pending a ruling -- see "Option contracts are
   blocked" above. This limitation still describes what the code does, and matters again
   the moment a ruling permits options.)* `portfolio_store` models whole
   shares only — no contract multiplier, strike, expiry, or assignment. `sync_filled_order`
   diverts option fills to `paper_fills` under the OCC symbol and returns
   `OPTION_FILL_RECORDED` without touching `paper_positions`. Alpaca is the source of truth for
   options P&L. Do not "fix" this by booking contracts as shares — that was a real bug.
3. **The strategy layer selects; selecting is not approving.** *(As of 2026-09-23
   `propose_option_strategy` refuses before selecting anything, so the endpoint returns the
   determination rather than a contract. The selection rules below are unchanged and still
   tested; they are simply unreachable until a ruling.)* `option_strategy.py` calls
   `fetch_option_chain` and picks a contract for both Level 1 strategies: 1–7 DTE, the strike
   closest to 4% OTM inside a 2–7% band, filtered for a live bid, a spread under 15% of mid, a
   minimum premium, and a standard 100-share multiplier; sized from owned shares or settled
   cash. It emits an `option_contract` that drops straight into `build_shariah_candidate`, and
   a `rationale` string narrating the choice.

   It is now reachable over HTTP at `GET /stock/{symbol}/option-strategy`, which resolves
   account facts through `broker_account_context` (settled cash, never buying power) and
   returns the proposal plus a `next_step` block: `approved: false`, the four gates not yet
   run named explicitly, and the exact `/paper/preview` body to post next. A proposed contract
   has cleared **nothing** — `test_option_strategy_api.py` asserts a proposal can never read as
   approved, and `test_option_strategy.py` asserts a selected contract still has to clear the
   whole gate chain. The two Level 1 structures are no longer equally proven: a **cash-secured
   put has filled live** against the real broker (see 4 below), while the **covered call has
   not** — it is still exercised only against the mocked seam in `test_option_execution_smoke.py`.
   Nothing has yet written a call against real shares, and the test account cannot currently do
   it: `option_strategy` sizes a covered call from owned shares at the standard 100-share
   multiplier, and `0TCX` holds exactly 1 share of CVX.
4. **The end-to-end chain has run against real Alpaca — twice, on the test account: once
   equity, once option.** *(The option half is now history rather than a live capability:
   option contracts are blocked pending a ruling. The record below is kept exactly as it
   was — that fill happened, and erasing it would falsify the evidence trail. What changed
   is the policy, not the past.)* Both went preview → approval → `EXECUTE PAPER` → fill → reconcile →
   ledger against `https://paper-api.alpaca.markets` over the `alpaca_mcp` transport, with
   nothing mocked.

   **The equity fill — 2026-08-19.** Order `bc939dcd-edfd-428f-9227-272d2521300f` (`client_order_id
   amanah-queue-5`, queue 5) filled 1 CVX at **206.89** against a **207.60** limit and booked
   into `paper_positions` as `quantity 1.0, average_cost 206.89, cost_basis 206.89,
   realized_pnl 0.0, account_suffix 0TCX, account_type CASH`.

   Verified three ways, which is what makes it evidence rather than a green checkmark: the
   local ledger, the broker's own position (`avg_entry_price`), and the order's
   `filled_avg_price` all read 206.89. That agreement matters because the fill price and the
   limit price differed — `sync_filled_order` computes
   `float(dealt_avg_price or price)`, and since `0.0` is falsy a null fill price would have
   silently booked the **limit** and looked entirely plausible. A second reconcile returned
   `ALREADY_SYNCED` with the quantity still 1.0, so the `UNIQUE(queue_id)` guard holds.

   Evidence trail, all committed:

   | file | what it shows |
   |---|---|
   | `docs/live-trade-evidence/before-CVX.json` | the gate refusing a real order — `REJECT margin_account_not_permitted` while the account was still `MARGIN` |
   | `docs/live-trade-evidence/after-CVX.json` | the first real broker submission after the account fix |
   | `docs/live-trade-evidence/reconciled-CVX.json` | the fill reconciled into the ledger, `outcome: VERIFIED`, `problems: []` |

   **The option fill — 2026-08-20.** A Level 1 **cash-secured put** filled live. Order
   `3f06c708-d8fa-4d3a-8823-e3a78f9b3053` (`client_order_id amanah-queue-11`, queue 11) sold to
   open 1 contract of `AAPL260828P00305000` at **1.02** against a **1.00** limit — a sell-to-open
   filling *above* its limit, which is the correct direction for a credit. It booked to
   `paper_fills` under the OCC symbol and returned `OPTION_FILL_RECORDED`; `paper_positions` was
   **not** touched, so limitation 2 above still holds after a real option fill rather than merely
   in theory.

   Verified the same way, by independent readings agreeing rather than one green checkmark: the
   broker's order record (`filled_qty 1`, `filled_avg_price 1.02`), the broker's own position
   (`AAPL260828P00305000`, side `short`, `qty -1`, `avg_entry_price 1.02`), the locally synced
   fill row (`1.0 @ 1.02`), and settled cash moving 99793.10 → 99895.08 — the 102.00 credit less
   0.02 in fees — all agree.

   Getting there took three queue entries, and the two that failed are worth as much as the one
   that filled:

   | queue | outcome | what it shows |
   |---|---|---|
   | 9 | `PORTFOLIO_SELL_GATE_FAILED` | a real bug — see the fourth bug below |
   | 10 | `BROKER_CANCELLED` | the stale-limit failure again, at speed: 1.05 was the bid at submission and the bid was 1.00 under two minutes later, so it rested instead of crossing. Cancelled with `filled_qty 0`, reconciled, re-quoted. **Re-quote and submit as close together as possible** — an option bid is perishable in a way an equity bid is not. |
   | 11 | `BROKER_FILLED` | the fill above |

   Option evidence trail, all committed:

   | file | what it shows |
   |---|---|
   | `docs/live-trade-evidence/submitted-CVX-option.json` | the first option order to reach a broker at all — OCC symbology, `sell_to_open`, and the confirmation gate accepted, but *not* a fill |
   | `docs/live-trade-evidence/canceled-CVX-option.json` | that order cancelled unfilled, and reconcile handling a terminal **non**-fill: `BROKER_CANCELLED`, `portfolio_sync: null`, nothing written to either table |
   | `docs/live-trade-evidence/filled-AAPL-option.json` | the first real option fill, with the queue 9/10/11 sequence and the bug it exposed |

   **What this does not prove.** It ran on the *test* paper account (`0TCX`) from a *local*
   checkout. The submission demo trade still has to happen on the dedicated hackathon account
   once that is provisioned, and it should be run **against the deployed instance at
   https://amanahtrader.uk, not locally** — `backend/*.db` is gitignored by design, so a
   locally-run trade writes to a local SQLite file that the deployed instance never sees. Its
   positions and fills would simply be absent from the demo. This is not hypothetical: the CVX
   position above lives in the `.worktrees/live-trade-backend` database and appears in no other
   checkout. Run the demo trade through the deployed instance so its own database captures the
   position naturally.

   `test_option_execution_smoke.py` still covers the paths a single live trade cannot: a
   covered call, an unsupported strategy, a margin account, and an under-collateralized
   cash-secured put, through the real FastAPI app with only the `alpaca_request` seam mocked.
   Writing it found and fixed **three** real bugs, all the same shape: an equity-only rule
   applied to options — and a **fourth** of that shape turned up later in live execution, which
   is recorded after them because it was found differently. `agent_coordinator.evaluate_candidate`
   unconditionally blocked any non-BUY side, which made both Level 1 strategies (both
   sell-to-open) unreachable from `/paper/preview` at all (fixed with an `asset_class` param);
   and the portfolio risk overlay treated an option's contracts/premium as equity
   shares/share-price, producing nonsensical exposure percentages that would reject almost any
   option order once a real position existed (fixed by skipping that equity-specific overlay
   for `asset_class == "option"` — `option_structure_gate` and `account_shariah_gate` already
   provide correct option-native sizing); and the same function required a **BUY quant signal**
   for every order, so a fully-collateralized cash-secured put on a Shariah-PASS underlying was
   refused for `quant_no_buy_signal` alone (fixed on 2026-08-20 by scoping that filter to
   non-option orders — see below).

   **A fourth of the same shape surfaced on 2026-08-20, and this one no test caught — a real
   order did.** `paper_execution.validate_sell_reduction` required a local equity position for
   *any* `SELL`, with no `asset_class` exemption. Every Level 1 structure is sell-to-open, so a
   legitimate cash-secured put on an underlying the account holds no shares of was rejected at
   execution time with `no local AAPL position is available` — after clearing the Shariah,
   option-structure, account and risk gates at approval. The reduce-only rule is right for
   equity and meaningless for a sell-to-open option, whose collateral is proven by
   `option_structure_gate` and `account_shariah_gate` at approval time.

   It survived the smoke suite *and* a real live order because of a coincidence: the first live
   option order (CVX, queue 7) happened to sit on top of the 1-share CVX equity position left by
   the equity trade, so the equity check passed for the wrong reason. It took a put on an
   underlying with genuinely zero equity exposure to expose it. The regression test in
   `test_paper_execution_gates.py` is therefore placed **before** the existing `seed_position()`
   call, so it cannot pass by that same coincidence; it was confirmed red before the fix and
   green after.

   The lesson generalises past this one function: a live trade that passes proves less than it
   appears to when incidental account state can satisfy a check the code never meant to apply.

   The quant agent decides whether to open a *directional long*. No Level 1 structure is one: a
   covered call is written against stock already owned, a cash-secured put means "willing to own
   at this price", and buying a short leg back reduces risk. Requiring a breakout for any of them
   blocked the strategy whenever the underlying was merely calm — on 2026-08-20 that was 19 of 21
   liquid large caps scanned. No protection was removed: ownership and collateral are still proven
   by `option_structure_gate` and `account_shariah_gate` at approval time, and the signal is still
   reported in `agent_summary.quant`, just not as a blocker. Directional equity entries are
   unaffected and still require BUY.

   **The fixture masked it, which is why it survived so long.** Scenarios 1–4 send
   `test_fixture: true`, and `paper_test_overrides` injects a `quant_override` of `signal: "BUY"`
   as well as the Shariah verdict — so none of them could see what the quant agent actually says.
   Scenario 5 narrows the fixture to the Shariah verdict only and swaps
   `agent_coordinator.evaluate_quant` for a real `NO_SIGNAL` shape, asserting both that the option
   is approved and that a plain equity BUY on the same underlying is still blocked.

5. **Malaysian execution is blocked by the moomoo ACCOUNT, not by this code.** Verified
   against a live OpenD gateway on 2026-09-23 — the first time anything here has been.

   `get_acc_list()` was enumerated under every `TrdMarket` filter. The login has exactly
   three accounts:

   | acc_id | env | type | trdmarket_auth |
   |---|---|---|---|
   | acc_id | env | type | sim_acc_type | trdmarket_auth |
   |---|---|---|---|---|
   | `286260078297602112` | **REAL** | MARGIN | — | HK, US, SG, MY, MYFUND, USFUND |
   | `2713262` | SIMULATE | CASH | `STOCK` | **HK only** |
   | `1721740` | SIMULATE | **MARGIN** | `STOCK_AND_OPTION` | **US only** |

   Checked exhaustively: **8 `SecurityFirm` values × 6 `TrdMarket` values**, 48 queries.
   Exactly those two simulate accounts appear, under every firm. So `security_firm` makes
   no difference to *discovery* — do not re-test that. Moomoo's own API docs list
   simulated trading for HK, US and CN; MY appears only in live-trading contexts.

   **There is no MY simulate account.** Filtering by `TrdMarket.MY` returns the REAL
   account and nothing else. Moomoo provisions a separate simulated account per market,
   and Malaysia has not been provisioned on this login — the RM1,000,000 Malaysian paper
   trading in the moomoo app is evidently not the same object as an OpenAPI simulate
   account. Unlocking trade does not change this: `unlock_trade` governs order placement,
   not account enumeration, and the list was identical before and after.

   So the `MY.5225` code format remains untested — **the account lookup fails first, and
   nothing reaches the code that builds a symbol.** That is a more useful fact than the
   open question it replaces.

   Two independent refusals sit behind it, both working as designed. The only MY-authorised
   account is `REAL`, and `TrdEnv.SIMULATE` is hardcoded at every call site so it can never
   be selected. And it is `MARGIN`, which `account_shariah_gate.check_account` refuses with
   `margin_account_not_permitted` — the same refusal `docs/live-trade-evidence/before-CVX.json`
   records on the Alpaca path before that account was switched to CASH. The US simulate
   account is margin too, so it would be refused on Riba grounds even for a US order.

   **The market-aware probe is what surfaced this.** Before `check_moomoo_status` took a
   market (fixed 2026-09-23), a Bursa order was gated on whether the *US* account was ready;
   it would have reported `paper_account_ready` and failed confusingly deeper in. It now
   returns `active_my_simulate_account_not_found`, which is the true reason.

   **The unblock condition is exact:** a simulate account with `MY` in `trdmarket_auth`.
   `check_moomoo_status("MY")` reports it the moment one exists, and its refusal now names
   the accounts that do exist — `active_my_simulate_account_not_found (simulate accounts on
   this login: HK/STOCK, US/STOCK_AND_OPTION)` — so the reader can tell "not provisioned"
   from "misconfigured" without re-running any of this.

   Open experiment: the HK and US paper accounts exist because they were used, so opening
   Bursa paper trading in the moomoo app and placing one trade may provision one. Re-run
   the check afterwards; that is how we will know.

   **One unverified risk was closed from moomoo's own reference code.** Their open-source
   agent skill (`MoomooOpen/moomoo-agent-hub`, `scripts/trade/place_order.py`) builds
   order kwargs conditionally:

   ```python
   if fill_outside_rth: order_kwargs["fill_outside_rth"] = True
   if session != Session.NONE: order_kwargs["session"] = session
   ```

   It **omits** both at their defaults. This adapter sent them unconditionally, and this
   file had flagged exactly that pair as a possible Bursa rejection — both are US-market
   concepts. They are now omitted, matching the vendor, which costs nothing: every order
   this system places is a regular-hours day order, so both were always at their defaults.
   `test_moomoo_paper_adapter.py` asserts their **absence**, not a default value.

   That skill is worth knowing about for a second reason: it gives a language model a
   `place_order` tool. It is a legitimate product for someone without a compliance gate,
   and it is the inverse of this architecture — an order placed through it would bypass
   the gate chain, the evidence trail and the two-tap approval, because it never touches
   this backend. Fair to it: its live path enforces a two-step `--confirmed` workflow it
   calls a hard constraint, which is the same shape as the relay's two taps. Read its
   scripts as reference; do not wire its trading tool into an agent.

   Two hardening fixes went in alongside this (2026-09-23), neither of which unblocks
   anything today. `security_firm` is now passed per market — `FUTUMY` for Bursa, since
   Moomoo Securities Malaysia is a separate legal entity — because order placement is a
   different call from discovery and querying the wrong entity for it was left to luck.
   And `find_active_simulate_account` now checks `trdmarket_auth` rather than returning
   the first SIMULATE row: the context filter was the only thing preventing a wrong-account
   submission, and the two accounts above differ in `acc_type`, so picking the wrong one
   silently changes whether the Riba gate refuses the order.

   *(Routing itself is solved: a Bursa order reaches the Moomoo adapter once
   `PAPER_EXECUTION_ADAPTER_MY=moomoo` is set. Moomoo also has no `client_order_id`
   equivalent for broker-side idempotency — only a free-text `remark`. Do not describe
   Bursa execution as working until an order has filled and reconciled.)*
   On 2026-09-22 `moomoo_paper_adapter.py` was extended to Bursa at the owner's direction,
   reversing this file's former "do not extend" note on the `moomoo_*` family. The reason is
   simple: Alpaca has no Bursa access of any kind, so Moomoo is the only possible Malaysian
   route. `SUPPORTED_REAL_MARKETS = {"US", "MY"}`, `MARKET_CODE_PREFIXES` builds `MY.5225`,
   and `market_to_trd_market` returns `TrdMarket.MY` — all verified against the installed
   SDK's own enums (moomoo 10.09.6908 has `TrdMarket.MY` and `Market.MY`) and asserted in
   `test_moomoo_paper_adapter.py`, which checks the request that gets *built*.

   *(That paragraph was written when nothing had been verified against a live gateway. It
   has now been — see the account table above. The `MY.` code format is still unproven, but
   for a more specific reason than "untested": the account lookup fails before a symbol is
   ever built.)*

   **A Malaysian order now completes preview → approval on real prices.** Verified
   2026-09-22 against live data: `4197` (Sime Darby) reached `READY_FOR_APPROVAL` with **no
   blockers** — Shariah PASS from `sc-sac-my-2026-05-29`, quant BUY on 312 live Bursa bars
   at RM 2.46 from `yahoo`, risk PASS — and the evidence record carries the SC document hash
   alongside `price_source: yahoo` and `as_of_date`.

   Bursa prices come from `yahoo_finance.py` via `market_data.provider_for()`, which routes
   **per market**: MY always uses Yahoo, US follows `MARKET_DATA_PROVIDER`. Alpaca and
   Tiingo carry no Bursa data at all, so the two markets would otherwise be mutually
   exclusive. Malaysian symbols also request a wider history window — 320 days leaves only
   ~18 bars of headroom over `MIN_BARS` on a market with more public holidays.

   **An earlier version of this section claimed the opposite** — that routing was
   Alpaca/Tiingo only and no Malaysian price source existed. That was written on
   2026-09-22 from a truncated grep that showed only the Alpaca import, and was wrong:
   `yahoo_finance.py` had been present and wired since `651d1ef`. The real defect was one
   line — `"yahoo"` missing from `quant_agent.LIVE_SOURCES` — so genuine Bursa bars were
   classified `unknown` and refused as `synthetic_market_data`. **Real data labelled
   synthetic is a worse failure than missing data, because the stated reason is untrue.**
   Recorded because a confident wrong entry in this file is more costly than a gap in it.

   Yahoo is an unofficial source with no SLA, and `yfinance` is imported lazily
   (`yahoo_finance._fetch_yfinance`) so a missing install degrades Malaysian pricing rather
   than failing app startup. That is a reason to keep recording provenance on every
   decision, not a reason to call live data synthetic — `fixture` means *we invented the
   numbers*, which is a different claim, and the blocker exists for that alone.

   Lot sizing is deliberately *not* enforced in the adapter. Bursa's board lot is 100 shares
   and `p3_decision_engine` already rounds to it, but Bursa also has an odd-lot market, so a
   local "must be a multiple of 100" rule could wrongly refuse a legitimate order. Sizing
   belongs to the decision engine; the adapter submits what it is given.

## Style

- Deterministic Python for anything that gates, screens, or executes. No LLM in the decision
  path — a language model may explain a decision but must never make, approve, or bypass one.
- Match the surrounding code: small pure functions, dict returns with a `status` key, fail
  closed on anything unknown.
- Keep `local_api.py` diffs small; it is the file most likely to be touched concurrently.

## Linting and formatting

This project uses Ruff for both linting and formatting. Do not call Black, flake8, isort,
or pylint. There is no `uv` project here — invoke Ruff through the shared `.venv` directly:

- Lint: `.venv\Scripts\ruff.exe check .`
- Lint and auto-fix: `.venv\Scripts\ruff.exe check --fix .`
- Format: `.venv\Scripts\ruff.exe format .`
- Check formatting without writing: `.venv\Scripts\ruff.exe format --check .`

Ruff configuration lives in `pyproject.toml` under `[tool.ruff]`. Do not add a separate
`ruff.toml` or `.ruff.toml`. Do not add inline `# noqa` comments without a rule code. The
rule selection is pinned to Ruff's traditional core set (`E4`, `E7`, `E9`, `F`) rather than
its current broader defaults — the broader set found ~100 pre-existing findings across
`backend/` on first run, which were not mass-fixed; expanding the rule set later is a
deliberate decision, not something to do incidentally while touching an unrelated file.

A `PostToolUse` hook (`.claude/settings.json` → `.claude/hooks/ruff_after_edit.py`) runs
`ruff check --fix` and `ruff format` on whatever `.py` file Claude just wrote or edited —
scoped to that one file, never the whole repo, so it can't retroactively touch the
pre-existing findings elsewhere.

**That hook deletes an import the instant its last use disappears**, because `--fix` resolves
F401. Two ways this bites, both of which cost time on 2026-09-21:

- Adding an import in one edit and the code that uses it in the next. The hook fires between
  them, sees an unused import, and removes it — so the second edit lands with a `NameError`.
  **Add the usage first, or both in one edit.**
- Temporarily commenting out the only use of an import during a deliberate-break check. The
  import is removed; restoring the code afterwards leaves it dangling.

It also reformats the *whole* file it touches, not just the edited lines, so a small change to
a long-unformatted file can produce a large diff of pure line-rewrapping. That is the hook
working as designed — check `git diff -w` to separate real changes from rewrapping.
