# OpenClaw bridge — setup

The bridge connects OpenClaw (running on the owner's laptop) to the deployed Amanah
Trader at `https://amanahtrader.uk`. It is a **read-only** surface as of Phase 1: no bot
can queue, approve or execute anything.

## The shape

```
OpenClaw  --stdio-->  mcp_server.py  --HTTPS-->  amanahtrader.uk
(the model)           (Basic auth,               (the gate chain)
                       NO operator key)
```

Two processes, and the separation is the point:

| process | spawned by | Basic creds | operator key | can reach |
|---|---|---|---|---|
| `mcp_server.py` | OpenClaw | yes | **no** | reads only (Phase 1) |
| `relay.py` | the owner, via Task Scheduler | yes | yes | execute — **not built yet** |

`mcp_server.py` calls `load_bridge_config()` without `with_operator`, so
`X-Amanah-Operator` is absent from its address space. `/paper/execute` returns 401 at
nginx for that process regardless of what a model asks for. `test_bridge_mcp_server.py`
asserts this two ways, including an AST check that the module never passes
`with_operator`.

## Credentials

Create `backend/bridge/.env` (gitignored; never commit it):

```
BRIDGE_BASE_URL=https://amanahtrader.uk
BRIDGE_BASIC_USER=project_owner
BRIDGE_BASIC_PASSWORD=<the project_owner password>
# Relay only, Phase 3+. Do NOT put this in the MCP process's environment.
# BRIDGE_OPERATOR_KEY=<the 64-hex nginx operator key>
```

There is no session or token anywhere in the backend — `auth.py` re-verifies HTTP Basic
on every request at 260,000 PBKDF2 iterations, on a 2 GB droplet. That, not the nginx rate
limit, is the real reason the bridge caches and single-flights.

On Windows, prefer DPAPI or a user-only ACL on this file. A file readable only by your
account is the acceptable floor.

Verify without exposing anything:

```powershell
.\.venv\Scripts\python.exe backend\bridge\check_bridge.py --symbol 4197
.\.venv\Scripts\python.exe backend\bridge\check_bridge.py --symbol AAPL --risk
```

It prints booleans and rendered blocks, never values — the same discipline as
`check_config.py`.

## Wiring OpenClaw

One MCP server entry per role, under `mcp.servers` in OpenClaw's config:

```json
{
  "mcp": {
    "servers": {
      "amanah-shariah": {
        "command": ".venv/Scripts/python.exe",
        "args": ["backend/bridge/mcp_server.py", "--role", "shariah_narrator"],
        "cwd": "E:/Github2/Ai_Finance_Syariah"
      },
      "amanah-quant": {
        "command": ".venv/Scripts/python.exe",
        "args": ["backend/bridge/mcp_server.py", "--role", "quant"],
        "cwd": "E:/Github2/Ai_Finance_Syariah"
      }
    }
  }
}
```

Roles: `shariah_narrator`, `quant`, `risk_officer`, `portfolio_steward`, `auditor`,
`sc_watcher`, `chief_of_staff`.

**Do not put credentials in this config.** The bridge reads its own `.env`. A spawn config
is a file a model can sometimes be talked into reading; the `.env` is not passed through
OpenClaw at all.

`initialize` returns the role's instructions, so OpenClaw receives the persona text
automatically — a separate SKILL.md is optional and must never contain a URL or a
credential.

## What each role can reach

| role | tools |
|---|---|
| `shariah_narrator` | `shariah_status`, `screen_detail`, `universe`, `publication`, `publications`, `screens_latest` |
| `quant` | `quant_signal`, `market_data`, `research`, `screen_detail` |
| `risk_officer` | `risk_limits`, `risk_snapshot`, `positions`, `paper_account`, `portfolio` |
| `portfolio_steward` | `compliance_snapshot`, `portfolio_history`, `positions` |
| `auditor` | `evidence`, `execution_audit`, `approvals`, `audit_log` |
| `sc_watcher` | `publications`, `publication`, `universe` |
| `chief_of_staff` | **none** |

The chief of staff having no data access is deliberate and tested. With data access it
would see a choice point in code and become a model arbitrating a compliance question;
with none, it can only route and assemble.

A role calling another role's tool is refused **before the request is built**, so it never
reaches the network.

## Two markets, two authorities

`shariah_status` routes by market, and this is easy to get wrong:

- **Bursa code (numeric)** → `/api/shariah/{ticker}`, the SC Malaysia SAC list.
- **US ticker** → `/stock/{symbol}/explain`, this project's own SEC EDGAR ratio screen.

Asking the SC list about a US ticker returns `UNKNOWN / not_present_in_approved_publication`
— true of the list, and useless as an answer about the company. `bridge/markets.py`
mirrors the backend's `detect_market`, and `test_bridge_market_parity.py` fails the moment
the two disagree.

## What is deliberately unreachable

- **`/news` and `/copilot/*`.** They spend real OpenRouter money per call and the backend
  has no spend cap anywhere. Paying a model to summarise for another model is the worst
  version of that bill. `/api/research/{ticker}` is the deterministic feed the copilot
  reads, it is free, and the `research` tool uses it.
- **`/paper/approval` and `/paper/execute`.** Tap 1 and tap 2 are human actions carried out
  by the relay. If a bot could queue an order, the two-tap flow would be one tap with extra
  steps.
- **Option contracts.** Blocked system-wide pending a scholarly ruling; see
  `docs/shariah-policy/option-contracts-determination.md`.

## Narration is filtered (Phase 2)

`filters.py` decides whether a model's narration may be attached, in three layers, each
catching what the others cannot:

1. **Vocabulary** — advisory and authority-claiming words. One narrow exemption: an
   UPPERCASE token that actually appears in the rendered facts is the system's own word
   quoted, so `Signal BUY` may be restated while "a good time to buy" may not. That
   exemption was found by running the bridge against the live deployment, not by
   reasoning — a blanket ban left the quant bot unable to report the one thing it is for.
2. **Polarity faithfulness** — the narration must name the right ticker and carry the
   right verdict polarity. This catches a fluent fabrication that uses no banned word.
3. **Numeric containment** — every number in the narration must appear in the facts. The
   strongest layer here, because these numbers are actionable: a hallucinated queue id,
   strike, deadline or loss percentage is a different kind of wrong from an adjective.

`compose.py` puts the facts first and the narration in one trailing slot labelled
*"explanatory only — the lines above are authoritative"*, and only if the filter passes.
Otherwise the slot is dropped and the message says something was withheld, so a silently
shorter message never hides a refused claim.

Disagreeing blocks — a PASS verdict beside a BLOCKING risk snapshot — are both printed in
full under a header saying they disagree. Nothing reconciles them.

**There is no `assemble(blocks)` tool, deliberately.** Blocks arriving as strings from a
model would print above the authoritative line, so a fabricated block would render as
fact with the filter inspecting only the narration below it. Composition therefore
belongs to the relay, which fetches its own blocks. See the reasoning in `compose.py`.

## Not built yet

`proposals.py`, `relay.py`, `schedules.py` (Phase 3 — the Telegram two-tap flow and the
polling jobs, including the daily disposal-clock refresh).
