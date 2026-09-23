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
| `trader` | `preview_order`, `screen_detail`, `quant_signal`, `market_data` |
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

## The relay: two taps, and the operator key (Phase 3)

`relay.py` is a **separate process from the MCP servers**, started by you rather than by
OpenClaw. No model runs in it. It fetches its own facts, renders them, and carries out
exactly two human actions:

| | action | credentials |
|---|---|---|
| **Tap 1** | `POST /paper/approval` — queues | Basic auth only |
| **Tap 2** | `POST /paper/execute/{id}` — submits | Basic auth **+** operator key |

Those are not two steps this code invented. nginx already returns 401 on
`/paper/(execute|reconcile)/` without the operator header while letting
`/paper/(preview|approval)` through, so the two taps sit on two credential tiers the
deployment already enforces.

**Buttons only.** A text message can never cause a write — there is no `/execute 14`
command and there must never be one. A callback carries a server-side nonce, not a symbol
or a queue id. Two identity checks, not one: the chat must be allowlisted **and** the
presser must be your Telegram user id, because chat membership is not identity.

**`EXECUTE PAPER` lives only in `relay.py`.** It is in no tool schema, no role prompt and
no roster entry, so a model has never seen it anywhere that emitting it would act.
`test_bridge_no_secret_leak.py` asserts that.

**One order per approval.** `claim_execution` is a single conditional `UPDATE`, so the
database decides who wins a double tap — Telegram redelivers callbacks, and the cost of
losing that race is two orders. `UNIQUE(queue_id)` is the belt to that brace. A timeout
records `EXECUTION_UNCERTAIN` and is **never** retried: the request may already have
reached the broker, and resending to find out is the bug.

**Proposals expire.** Options 90s pending / 60s queued; equities 300s / 180s. CLAUDE.md
records queue 10 dying `BROKER_CANCELLED` because an option bid went from 1.05 to 1.00 in
under two minutes. A test asserts the option window stays inside that.

### Enabling execute

Without `BRIDGE_OPERATOR_KEY` the relay renders tap 2, shows the exact payload it would
send, and stops at a dry run recorded as `DRY_RUN` — never `EXECUTED`, because the store
must not claim an order was submitted when none was. Add the key to
`backend/bridge/.env` only when you want tap 2 to reach the broker.

**The relay asks the backend which markets it will execute** — it reads
`execution_markets` from `GET /paper/status` rather than restating the rule. A market that
is not enabled gets no execute button, the payload is not shown (showing it invites
sending it by hand), and a replayed callback is still refused at the method. Tap 1 stays
real and recorded either way.

Malaysian execution is off until `PAPER_EXECUTION_ADAPTER_MY=moomoo` is set on the
backend; set it and the button appears with no bridge change.

*(An earlier version of this page stated that as working when it was not: `/paper/status`
did not return `execution_markets` at all, so the relay refused every market including US.
Fixed, and `test_bridge_status_contract.py` now asserts the real route shape rather than a
fixture — which is what would have caught it.)*

The routing was never the only obstacle to Bursa execution: OpenD has to run somewhere the
backend can reach, and no Moomoo order has ever reached a broker.

## Polling (Phase 3)

Nothing on the droplet can call out — `VPS_RUNBOOK.md:387` lists "no monitoring or
alerting" as a known gap — so every alert is the laptop asking.

| job | interval | what it watches |
|---|---|---|
| `disposal_clock` | daily | non-compliant holdings and overdue deadlines |
| `publication_watch` | 30 min | a new or newly activated SC list |
| `risk_watch` | 5 min | a risk number going unbounded |

Steady state is under 20 requests/hour against a 240 r/m allowance, because every request
costs a 260,000-iteration PBKDF2 on a 2 GB droplet.

**The disposal-clock job is the one that must never be cached.** Calling
`GET /portfolio/compliance` is what advances and persists the clock — nothing else calls
`apply_disposal_clock` — so it uses `refresh_disposal_clock()`, and a test asserts it.

**No scheduled job may call `/paper/preview`, `/news` or `/copilot/*`**, asserted by an
AST check over `schedules.py`. A scheduled preview would flood `/api/evidence` with
records indistinguishable from orders you actually considered, and that distinction cannot
be recovered afterwards.

## Running the relay

Add to `backend/bridge/.env`:

```
BRIDGE_TELEGRAM_TOKEN=<from @BotFather>
BRIDGE_TELEGRAM_CHAT_ID=<the group or DM id>
BRIDGE_TELEGRAM_OWNER_ID=<your own Telegram user id>

# Optional forum topics, one per role, so a trade proposal and a routine compliance
# alert do not share a thread. Omit them and everything lands in the main chat.
BRIDGE_TELEGRAM_TOPIC_TRADES=
BRIDGE_TELEGRAM_TOPIC_COMPLIANCE=
BRIDGE_TELEGRAM_TOPIC_RISK=

# Leave this out until you want tap 2 to be real.
# BRIDGE_OPERATOR_KEY=<the 64-hex nginx operator key>
```

Then:

```powershell
.\.venv\Scripts\python.exe backend\bridge\relay.py
.\.venv\Scripts\python.exe backend\bridge\relay.py --iterations 1   # one poll, for a smoke test
```

It refuses to start if any of the three required variables is missing, naming which, and
it prints its execute capability on the first line:

```
Relay started. No operator key: tap 2 stops at a dry run.
Relay started. Operator key present: tap 2 WILL submit to the broker.
```

That line is deliberate — a relay that can only ever dry-run must not look identical to
one that can submit.

Start it under Windows Task Scheduler as yourself, not as a service account — the
operator key should live under your own ACL.

## Order of operations

The relay reads `execution_markets` from `/paper/status`, which the **deployed backend**
must be serving. Run it against an older production and every market fails closed, with no
execute button anywhere. So: deploy the backend first, then start the laptop side.

## Not built yet

A `--role trader` proposal has to be initiated by you or by OpenClaw; the relay does not
scan for candidates on its own, deliberately (see the note on scheduled previews above).
