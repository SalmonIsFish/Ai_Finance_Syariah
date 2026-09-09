# Amanah Trader — Claude Code Working Notes

## What this is

A local-first, Shariah-compliant paper-trading control system. Deterministic Python agents
screen every order for Shariah compliance, option-structure permissibility, account-level Riba
exposure, and risk limits **before** it can enter the approval queue, and a human must type a
confirmation phrase before anything reaches the broker.

The point is not the screening — plenty of products screen stocks. The point is that the gate
chain **enforces** and **proves**: an order that fails any gate cannot be submitted, and every
decision is recorded with its evidence.

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
| Legacy | `moomoo_*.py` — superseded by Alpaca, retained for history. Do not extend. |

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
| `PAPER_EXECUTION_ADAPTER` | `disabled` | `disabled` \| `fake` \| `alpaca` \| `alpaca_mcp` \| `moomoo` |
| `PAPER_EXECUTION_ENABLED` | `false` | Master lock on broker submission. |
| `MARKET_DATA_PROVIDER` | `alpaca` | `alpaca` \| `tiingo` |
| `ZOYA_ENVIRONMENT` | `sandbox` | **Sandbox returns randomized data.** See Known limitations. |
| `TRADING_MODE` | `approval` | `advisory` \| `approval` \| `autonomous_paper` |
| Risk limits | see example | `MAX_POSITION_PCT`, `MAX_TOTAL_EXPOSURE_PCT`, `MAX_LOSS_PER_TRADE_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_ORDERS_PER_DAY` |

`SHARIAH_UNIVERSE_PATH` and `SHARIAH_WIKI_PATH` default to committed in-repo copies
(`data/shariah-universe/`, `docs/shariah-policy/`), so a fresh clone runs with no `.env` at all.
Set them only to point at a larger private vault.

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

Every test is a plain script with a `main()` that prints `PASS: ...`. No pytest. Run them **Exception**: Phase 4 testing (ackend/test_p4_*.py) uses pytest and fixtures natively. This is permitted strictly for the Backtest Engine and historical simulation components because injecting mock deterministic data providers requires robust fixture isolation. Do not convert legacy tests to pytest.
individually:

```powershell
.\.venv\Scripts\python.exe backend\test_account_shariah_agent.py
.\.venv\Scripts\python.exe backend\test_account_shariah_gate.py
.\.venv\Scripts\python.exe backend\test_agent_coordinator.py
.\.venv\Scripts\python.exe backend\test_alpaca_execution_wiring.py
.\.venv\Scripts\python.exe backend\test_alpaca_market_data.py
.\.venv\Scripts\python.exe backend\test_alpaca_news.py
.\.venv\Scripts\python.exe backend\test_alpaca_paper_adapter.py
.\.venv\Scripts\python.exe backend\test_alpaca_shariah_wiring.py
.\.venv\Scripts\python.exe backend\test_approval_workflow.py
.\.venv\Scripts\python.exe backend\test_execution_audit.py
.\.venv\Scripts\python.exe backend\test_investment_committee.py
.\.venv\Scripts\python.exe backend\test_local_api_smoke.py
.\.venv\Scripts\python.exe backend\test_market_overview.py
.\.venv\Scripts\python.exe backend\test_moomoo_paper_adapter.py
.\.venv\Scripts\python.exe backend\test_moomoo_status.py
.\.venv\Scripts\python.exe backend\test_option_execution_smoke.py
.\.venv\Scripts\python.exe backend\test_option_fill_ledger.py
.\.venv\Scripts\python.exe backend\test_option_strategy.py
.\.venv\Scripts\python.exe backend\test_option_strategy_api.py
.\.venv\Scripts\python.exe backend\test_option_structure_agent.py
.\.venv\Scripts\python.exe backend\test_option_structure_gate.py
.\.venv\Scripts\python.exe backend\test_paper_execution_gates.py
.\.venv\Scripts\python.exe backend\test_portfolio_metrics.py
.\.venv\Scripts\python.exe backend\test_portfolio_risk_limits.py
.\.venv\Scripts\python.exe backend\test_portfolio_snapshot_history.py
.\.venv\Scripts\python.exe backend\test_provision_cash_account.py
.\.venv\Scripts\python.exe backend\test_portfolio_store.py
.\.venv\Scripts\python.exe backend\test_positions_api.py
.\.venv\Scripts\python.exe backend\test_quant_agent_provider.py
.\.venv\Scripts\python.exe backend\test_repo_defaults.py
.\.venv\Scripts\python.exe backend\test_risk_checks.py
.\.venv\Scripts\python.exe backend\test_sec_edgar_cache.py
.\.venv\Scripts\python.exe backend\test_sec_edgar_screen.py
.\.venv\Scripts\python.exe backend\test_shariah_candidate.py
.\.venv\Scripts\python.exe backend\test_shariah_explain.py
.\.venv\Scripts\python.exe backend\test_shariah_screen_store.py
.\.venv\Scripts\python.exe backend\test_shariah_trace.py
.\.venv\Scripts\python.exe backend\test_single_screening_path.py
.\.venv\Scripts\python.exe backend\test_stock_profile.py
.\.venv\Scripts\python.exe backend\test_tiingo_prices.py
.\.venv\Scripts\python.exe backend\test_us_pipeline_fixture.py
.\.venv\Scripts\python.exe backend\test_watchlist_store.py
```

All 42 of those pass. There are 43 `test_*.py` files on disk; `test_moomoo.py` is the one
excluded, for the reason below. `check_moomoo_status()` pre-checks TCP reachability before
touching the moomoo
SDK, so a closed OpenD port fails in ~1.5s instead of the SDK's own multi-minute retry/backoff —
this is what used to make `test_local_api_smoke.py` and the dashboard's status refresh hang;
both now complete fast with no Moomoo gateway running. `test_moomoo.py` still hangs by design:
it instantiates the moomoo SDK directly, bypassing that pre-check, since its purpose is to
manually verify a *real* OpenD connection when you actually have one running — it is the one
suite not run as part of the regular list above.

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
   of the two-layer store in NEXT_STEPS.md; `sec_edgar_cache.py` remains the raw-response half
   and is **not** replaced by it. The log is observability, not a gate: `_record_screen` is a
   swappable seam and a failed write is swallowed, because a locked SQLite file must never turn
   a COMPLIANT company into an ERROR. Malaysia is structurally excluded — the hook sits in the
   US screen, and `_evaluate_malaysia` does not pass through it.
2. **Option fills are audited but not tracked as positions.** `portfolio_store` models whole
   shares only — no contract multiplier, strike, expiry, or assignment. `sync_filled_order`
   diverts option fills to `paper_fills` under the OCC symbol and returns
   `OPTION_FILL_RECORDED` without touching `paper_positions`. Alpaca is the source of truth for
   options P&L. Do not "fix" this by booking contracts as shares — that was a real bug.
3. **The strategy layer selects; selecting is not approving.** `option_strategy.py` calls
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
   equity, once option.** Both went preview → approval → `EXECUTE PAPER` → fill → reconcile →
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