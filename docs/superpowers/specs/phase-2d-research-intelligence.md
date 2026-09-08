# Amanah Trader — Phase 2D Specification: Research Intelligence

## 1. Objective
Enhance Amanah Trader into a stronger research and decision-support system without weakening the deterministic Shariah, risk, governance, and execution boundaries established in Phases 0–2C. The AI Copilot is upgraded to a research assistant, heavily grounded by an expanded, provenance-preserving Vault knowledge and evidence API.

## 2. Architecture
The system maintains strict layer boundaries:
```text
SC Malaysia → Authoritative Shariah Databank → Deterministic Shariah Gate
Market Data → Quant Analysis
Obsidian Vault → Research Intelligence API (Read-Only)
(Shariah + Quant + Vault) → AI Copilot (Explanatory)
```
The AI Copilot does not create Shariah status, does not recommend trades, and operates completely downstream of the deterministic data.

## 3. Functional Requirements
### A. Research Knowledge Layer
- Extend `backend/vault_indexer.py` or `backend/screening_api.py` to allow contextual retrieval of Vault notes (methodologies, strategies, etc.) with explicit provenance (e.g. `source_type: "vault"`, `path`).
- The Vault remains strictly **READ-ONLY**.

### B. Security Research Context
- Aggregate deterministic fields into a unified read-only endpoint: Identity (ticker, exchange), Shariah (authoritative status, publication ID), Quant (signal, attractiveness), Risk (policy limits).
- Provide an aggregated evidence timeline distinguishing between SC publication events, Market data, Quant data, and Vault notes.

### C. Copilot 2D Upgrade
- Upgrade `copilot_api.py` to ingest the expanded research context.
- Support synthesizing evidence, retrieving methodology notes, and identifying missing information.
- Provide structured output segregating authoritative Shariah status from AI-generated explanations.
- Preserve the `status_check` as a strict consistency assertion (FAIL-CLOSED on mismatch).

## 4. API Requirements
- `GET /api/research/{ticker}`: Returns an aggregated, non-mutating view of identity, shariah, quant, risk_context, knowledge, and evidence.
- `POST /api/research/copilot`: An upgraded version of `POST /api/explain`, serving research interactions. Must remain read-only/non-mutating.

## 5. Frontend Requirements
- Extend `dashboard/screening/security.html` to include a "Research Intelligence" section.
- Maintain visual segregation between Official Status, Quant, Research/Vault, and AI Copilot.
- Must not touch `dashboard/index.html`.

## 6. Failure Behavior & Security
- Prompt-Injection Defense: All Vault content and user queries are untrusted. Malicious queries ("Ignore SC status") must safely degrade without altering the official output.
- Fail-Closed: If any provider fails, or if `status_check` mismatches, throw a safe HTTP 400/500 without optimistic data generation.
- No Secret Exposure: Environment tokens must never be sent to the LLM.

## 7. Explicit Non-Goals
- Live/Automated trading recommendations.
- A new unified AI Shariah or Investment score.
- Modifying or activating SC publications (`sc-sac-my-2026-05-29` remains pending).
- Writing to the Obsidian vault.
