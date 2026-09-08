# Amanah Trader — Phase 3: Implementation Plan

## Task 1 — Schema and state model
- Create explicitly isolated Phase 3 tables: `p3_portfolios`, `p3_portfolio_positions`, `p3_portfolio_orders`, `p3_portfolio_fills`.
- Ensure schema captures SC context (`shariah_publication_id`, `shariah_verdict`) on the orders themselves for historical reproducibility.

## Task 2 — Portfolio service
- Implement base CRUD for portfolios preserving strict transactional models and isolation from legacy Phase 0 tables.

## Task 3 — Deterministic position sizing
- Implement `calculate_max_quantity` respecting `MIN(cash_limit, position_limit, total_exposure, sector_limit, per_trade_loss)`.
- Explicitly integrate the `0.5% max_loss_per_trade_pct` configuration exactly as the worst-case notional loss.

## Task 4 — Authoritative pre-trade validation
- Create the validation pipeline ensuring Account Gate -> Shariah Gate -> Risk Gate -> Portfolio Constraints.

## Task 5 — Order state machine
- Implement state transitions ensuring only legal paths (PROPOSED -> APPROVED -> EXECUTED).

## Task 6 — Human approval
- Expose the API to transition PROPOSED to APPROVED, requiring a server-side revalidation of Shariah, Risk, and Account constraints.

## Task 7 — Atomic paper execution
- Implement the `BEGIN TRANSACTION` -> Revalidate -> Fill -> Update Cash/Position -> `COMMIT` atomic execution logic.

## Task 8 — Market-price freshness
- Integrate Yahoo Finance price fetches without caching (`allow_stale_cache=False`) at execution time to ensure immediate freshness, failing closed if unavailable.

## Task 9 — Evidence/audit integration
- Tie executions to the immutable audit/evidence trail preserving timestamps, publication hashes, and actor IDs.

## Task 10 — Portfolio valuation and metrics
- Calculate valuation on the fly from current positions using safe market-data wrappers.

## Task 11 — API
- Add required portfolio endpoints (`GET /api/portfolio/{id}`, `POST /api/portfolio/{id}/orders/{order_id}/execute`, etc.) enforcing RBAC.

## Task 12 — Frontend
- Build Phase 3 UI cleanly separated from legacy dashboard (`dashboard/index.html`). Emphasize distinct boundaries between informational (AI) and authoritative.

## Task 13 — Security/concurrency tests
- Write tests explicitly simulating concurrent `BUY` and `SELL`, AI prompt injection attacks, and Shariah bypass attempts.

## Task 14 — Full regression suite
- Run all backend `test_*.py` and frontend `node --test` scripts to guarantee Phase 0-2D integrity.

## Task 15 — Static/call-graph audit
- Audit import dependencies to guarantee AI and Vault cannot reach execution functions.

## Task 16 — Git/diff audit
- Confirm that no legacy Phase 0 code was unintentionally mutated.

## Task 17 — Final database verification
- Explicitly verify `sc-sac-my-2026-05-29` is pending via SQLite.

## Task 18 — Final report
- Generate `Phase3_Final_Report.md`.
