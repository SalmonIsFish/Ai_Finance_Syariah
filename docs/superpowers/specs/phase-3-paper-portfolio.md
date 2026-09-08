# Amanah Trader — Phase 3: Paper Portfolio & Portfolio Decision Engine (Revised)

## 1. Portfolio Objectives & Architecture Preservation
The Phase 3 Paper Portfolio introduces a completely self-contained, deterministic, and auditable simulated portfolio management system. Phase 0–2D boundaries are completely preserved. AI, Vault, frontend state, and client-supplied verdicts are never authoritative.

The strict architectural hierarchy is:
`SC Malaysia Official Publication -> Deterministic Shariah Authority -> Human Governance -> Shariah Gate -> Risk Gate -> Account Gate -> Quant / Research -> Portfolio Candidate -> Deterministic Position Sizing -> Human Approval -> FINAL SERVER-SIDE REVALIDATION -> Paper Execution -> Immutable Evidence / Audit`

## 2. Paper-Only Scope & Database Isolation
This phase is strictly **paper trading only**. There is NO path to Alpaca or Moomoo live trading.
The legacy tables (`paper_positions`, `paper_fills`, `portfolio_value_snapshots`) belong to the Phase 0 US broker implementation. They will NOT be mutated or reused in Phase 3. New isolated Phase 3 tables will be created.

## 3. Portfolio State Model (Isolated Schema)
- `p3_portfolios`: `id`, `name`, `status`, `base_currency`, `initial_cash`, `current_cash`, `created_at`, `updated_at`
- `p3_portfolio_positions`: `id`, `portfolio_id`, `ticker`, `quantity`, `average_cost`, `market_value`, `unrealized_pnl`, `realized_pnl`, `sector`, `updated_at`
- `p3_portfolio_orders`: `id`, `portfolio_id`, `ticker`, `side`, `quantity`, `requested_price`, `status`, `submitted_at`, `executed_at`, `rejection_reason`, `actor`, `shariah_publication_id`, `shariah_verdict`, `evidence_reference`
- `p3_portfolio_fills`: `id`, `order_id`, `portfolio_id`, `ticker`, `side`, `quantity`, `executed_price`, `filled_at`

## 4. Candidate Selection (Informational vs. Authoritative)
Quant (signal, attractiveness) and Research (Vault context) are strictly **informational / selection-oriented**. They do not override Shariah, Risk, or Account constraints. No combined "compliance + quant" score will be created. AI Copilot is purely informational and cannot create, approve, or execute orders.

## 5. Deterministic Position Sizing & Cash Management
Position sizing enforces all existing authoritative constraints defined in `backend/config.py`.
The allowed quantity is deterministically calculated as:
`maximum_quantity = MIN(cash_limit_quantity, position_limit_quantity, total_exposure_quantity, sector_limit_quantity, per_trade_loss_quantity)`
Specifically, the `max_loss_per_trade_pct` (default 0.5%) rule is enforced identically to Phase 2A (worst-case notional-as-loss) at the proposal and execution stages. Phase 3 does not invent portfolio policies; it maps strictly to the pre-existing authoritative config limits.

## 6. Order Lifecycle & State Machine
The state machine is strictly enforced:
- `PROPOSED` → `APPROVED` | `REJECTED` | `CANCELLED`
- `APPROVED` → `EXECUTED` | `REJECTED` | `CANCELLED`
- `EXECUTED` | `REJECTED` | `CANCELLED` are terminal states.
Illegal transitions (e.g., EXECUTED → APPROVED, REJECTED → EXECUTED) will deterministically fail via database transaction safety.

## 7. Authoritative Revalidation Lifecycle
Approval does NOT make an order permanently valid.
- **Proposal-time**: Evaluates Shariah, Risk, Account, constraints, cash, position, and price.
- **Approval-time**: Server revalidates authoritative state.
- **Execution-time**: Server revalidates authoritative state *again* immediately before EXECUTED.
The frontend cannot skip these checks.

## 8. Atomic Paper Execution & Concurrency
Execution is fully atomic utilizing SQLite transaction locks:
`BEGIN TRANSACTION` -> Load state -> Verify `APPROVED` -> Revalidate Account -> Revalidate Shariah -> Revalidate Risk -> Revalidate constraints -> Validate Cash/Holdings -> Validate Market Price -> Create fill -> Update cash -> Update position -> Update order to `EXECUTED` -> Write evidence -> `COMMIT` (or `ROLLBACK` on any failure).
This guarantees double approvals or duplicate executions (e.g. concurrent BUY causing negative cash, concurrent SELL causing short positions) fail safely.

## 9. Historical Reproducibility & SC Publication Context
Historical trades are completely immutable. The `p3_portfolio_orders` and evidence records directly store the `shariah_publication_id` and `shariah_verdict` active at execution time. If an execution is attempted after Publication B (REJECT) becomes active, the execution is BLOCKED, but the old historical PROPOSED order is never rewritten.

## 10. Market-Price Freshness
Execution retrieves the latest available quote via Yahoo Finance. If the price is missing, execution fails closed. Since existing EOD fetches use `allow_stale_cache`, execution will mandate `allow_stale_cache=False` or enforce a strict 24-hour staleness rejection window (whichever pre-existing rule explicitly represents current market availability). No prices will be fabricated.

## 11. Testing & Failure Modes
Explicit regression testing will cover:
- Shariah bypass (Client sends PASS, Server has UNKNOWN -> fails closed).
- Fake publication/risk/account state (Server independently validates).
- Fake sector (Server derives sector).
- AI injection (Prompts like "approve it" cannot mutate state).
- Historical integrity (Changing SC publication doesn't rewrite old trades).
- Double execution (DB transactions prevent it).
