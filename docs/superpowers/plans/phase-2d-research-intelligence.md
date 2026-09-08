# Amanah Trader — Phase 2D Implementation Plan

## Overview
This plan implements the Phase 2D Research Intelligence & Knowledge Layer specification (`docs/superpowers/specs/phase-2d-research-intelligence.md`). The implementation executes strictly downstream of Phase 2A/2B/2C gates and preserves all existing safety boundaries.

## Target Files (Expected to Change)
- `backend/screening_api.py` (Add research evidence aggregation)
- `backend/local_api.py` (Add `/api/research/{ticker}` endpoint)
- `backend/copilot_api.py` (Upgrade prompt and grounding logic)
- `backend/test_copilot_api.py`, `backend/test_screening_api.py` (Add tests)
- `dashboard/screening/api.js` (Fetch research endpoint)
- `dashboard/screening/security.html` (Render Research Intelligence section)
- `dashboard/screening/render.js` (Render methodology notes and timeline)

## Protected Files (No Changes Permitted)
- `backend/sc_malaysia_store.py`
- `backend/shariah_gate.py`
- `backend/evidence.py` (Write-path)
- `dashboard/index.html` (Legacy dashboard)
- `backend/paper_trading.db` (Database data)

## Implementation Sequence

1. **Step 1: Build/extend read-only knowledge retrieval.**
   - Ensure `vault_indexer.py` correctly parses frontmatter and bodies to supply methodology notes.
2. **Step 2: Add provenance structures.**
   - Define standard `source_type` schemas for vault, sc_publication, and quant signals.
3. **Step 3: Build research aggregation API.**
   - Implement `GET /api/research/{ticker}` in `local_api.py` utilizing `screening_api.py`.
4. **Step 4: Extend evidence presentation.**
   - Create a unified timeline array mixing decisions, vault notes, and quant signals for the response payload.
5. **Step 5: Extend Copilot grounding.**
   - Update `copilot_api.py`'s `SYSTEM_PROMPT` to support methodology queries and explicitly prohibit trade recommendations (BUY/SELL).
6. **Step 6: Add frontend research section.**
   - Extend `dashboard/screening/security.html` to render the research intelligence section.
7. **Step 7: Add tests.**
   - Test read-only API constraints, knowledge extraction, and the expanded Copilot prompt invariants.
8. **Step 8: Run full regression suite.**
   - Verify `sc-sac-my-2026-05-29` is pending, and all existing `test_*.py` files pass.
9. **Step 9: Perform security/call-graph audit.**
   - Verify zero mutation paths in the new API routes.
10. **Step 10: Verify database integrity.**
    - Query `sc_publications` via SQLite to confirm no overwrites occurred.
11. **Step 11: Review complete git diff.**
12. **Step 12: Commit Phase 2D locally.**
