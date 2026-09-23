# VPS Runbook — amanahtrader.uk

The production topology for Amanah Trader, which until now existed only on the box. A rebuild
or a handover was blocked without it. **No secrets in this file** — every credential is
referenced by location, never by value.

Audited over SSH on 2026-08-22. Everything below is read from the running server, not inferred
from the repo.

## Topology

| | |
|---|---|
| Host | `159.65.220.83`, DigitalOcean droplet `amanahtrader-vps` (kvm, 2 GB RAM, 48 GB disk, 7% used) |
| OS | Ubuntu 24.04.4 LTS, kernel 6.8.0-138-generic, no reboot pending |
| Domain | `amanahtrader.uk` + `www.amanahtrader.uk` |
| TLS | Let's Encrypt, expires 2026-11-18, `certbot.timer` enabled |
| Web | nginx 1.24.0 (Ubuntu), the only public surface |
| App | systemd `amanah-trader.service`, uvicorn on `127.0.0.1:8000` |
| Login | `ssh -i ~/.ssh/amanahtrader_vps amanah@159.65.220.83` |

`root` is locked at the OS account level by design — a password reset does not enable it. `amanah`
is the only login, is in `sudo`, and has **passwordless** sudo.

## The app

Checkout lives at `/home/amanah/amanah-trader`, tracking `origin/master` from
`github.com/SalmonIsFish/Alpaca_Hackhaton_Ai_Finance_Syariah.git`. Working tree clean.

```ini
# /etc/systemd/system/amanah-trader.service
[Unit]
Description=Amanah Trader FastAPI backend
After=network.target

[Service]
Type=simple
User=amanah
Group=amanah
WorkingDirectory=/home/amanah/amanah-trader/backend
Environment="PATH=/home/amanah/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/home/amanah/amanah-trader/.venv/bin/python -m uvicorn replit_app:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Three things about that unit are load-bearing and easy to break:

- **The entrypoint is `replit_app:app`, not `local_api:app`.** Despite the name, `replit_app.py`
  imports the same `local_api.app` object and mounts `/dashboard` onto it, so it serves the whole
  API *plus* the static dashboard. `backend/replit_start.sh` is **not** used and binds
  `0.0.0.0:8080`, which is wrong for this host — ignore it.
- **The bind is `127.0.0.1`.** uvicorn is not reachable except through nginx. Confirmed against
  `ss -tlnp`: nothing but 22, 80 and 443 listen on a public interface.
- **`Environment=PATH` starts with `/home/amanah/.local/bin`.** That is the only reason
  `PAPER_EXECUTION_ADAPTER=alpaca_mcp` works: the adapter shells out to `uvx alpaca-mcp-server`,
  and `uv`/`uvx` 0.12.5 live in that directory and are **not** on the default login PATH. Strip or
  reorder that line and broker execution breaks at demo time with a confusing error. The uv
  package cache is warm at `~/.cache/uv`.

Python 3.12.3 in `/home/amanah/amanah-trader/.venv`.

### Deploy

There is no deploy script. Deployment is `git pull` in the checkout followed by
`sudo systemctl restart amanah-trader`. `backend/.env` is **not** in git (`.gitignore:1`) and is
placed on the box by hand — see Credentials below.

**The dashboard does not ship with the pull.** `local_api.py:142` serves
`dashboard-v2/dist`, and `dist` is gitignored (`dashboard-v2/.gitignore:11`), so a `git pull`
updates the *source* and leaves the served bundle untouched. After any dashboard change:

```bash
cd /home/amanah/amanah-trader/dashboard-v2
npm ci --no-audit --no-fund     # node 22 / npm 10 are installed on the box
npm run build                   # writes dist/, ~1s
```

Verified 2026-09-21: the box reproduces the local build exactly, same asset hashes.

**The checkout's `origin` is the pre-rename URL** (`Alpaca_Hackhaton_Ai_Finance_Syariah.git`).
GitHub redirects it to `Ai_Finance_Syariah.git`, and `git ls-remote` on both returns identical
refs, so pulls and pushes work — it is confusing, not broken. Worth re-pointing next time
someone is on the box, so nobody concludes the droplet tracks a different repository.

### Deployed 2026-09-23: 30799f0 -> 85e744e (options, the bridge, per-market routing)

**Done and verified in production.** Pushed, pulled, restarted, and checked live:

| check | result |
|---|---|
| `GET /api/shariah/4197` | PASS, publication `sc-sac-my-2026-05-29`, hash `d6592a55...` |
| `GET /stock/AAPL/option-strategy` | the determination, not a contract |
| `GET /api/quant/AAPL` | BUY, 218 bars, `alpaca_iex` |
| `GET /paper/status` (owner) | `execution_markets`: `US.enabled true` / `MY.enabled false` |

That last one is the one that mattered: it is the key the OpenClaw relay reads, and the
bug fixed in `85e744e` was that this route never returned it. `US.reason` reads
`"US orders are submitted through 'alpaca_mcp'"` — an affirmative reason, not the `None`
that made an enabled market render as disabled. `broker_submission` is now `true`, which
it always should have been.

The notes below are kept as the record of what this deploy changed and how it was judged.

Audited before deploying. **Low risk**: no new packages (`requirements.txt` unchanged), no
required new environment variable, no import cycle (`import local_api` verified end to
end), and **no dashboard rebuild** -- `git diff 30799f0..HEAD -- dashboard dashboard-v2`
is empty, so skip the `npm run build` above. Equity execution is byte-identical.

```bash
ssh -i ~/.ssh/amanahtrader_vps amanah@159.65.220.83
cd /home/amanah/amanah-trader && git pull
sudo systemctl restart amanah-trader && systemctl status amanah-trader --no-pager
```

Add `PAPER_EXECUTION_ADAPTER_MY=disabled` to `backend/.env`. It is the default anyway, so
this documents the decision rather than changes behaviour -- and remember `config.py:29`
loads with `setdefault`, so **edit in place, never append** a second line for a key.

**What a live user sees change**, all of it intended:

| Surface | Before | After |
|---|---|---|
| `POST /paper/preview` with `asset_class=option` | `READY_FOR_APPROVAL` | `REJECT`, blocker `option_contracts_not_permitted`, with a human sentence in `blocker_messages` |
| `POST /paper/approval` for an option | `APPROVED_PAPER_READY` | `REJECT` carrying the determination |
| `GET /stock/{symbol}/option-strategy` | a proposed contract | the determination, before any chain fetch |
| `GET /system/mode` -> `broker_submission` | `false`, which was wrong | `true` |
| `GET /paper/status` | seven keys | plus `execution_markets` |
| `POST /paper/execute` on a Bursa order | `UNSUPPORTED_MARKET` (Alpaca's words) | `ADAPTER_NOT_CONFIGURED_FOR_MARKET` (the system's) |

Options are blocked pending a scholarly ruling -- see
`docs/shariah-policy/option-contracts-determination.md`. The dashboard already renders
`blocker_messages` and never reads `/system/mode.broker_submission` for a badge, so no UI
breaks.

**Smoke after restart:**

```bash
curl -s https://amanahtrader.uk/api/shariah/4197 | head -c 200      # PASS + SC hash
curl -s https://amanahtrader.uk/stock/AAPL/option-strategy | head -c 200   # the determination
curl -su project_owner https://amanahtrader.uk/paper/status | head -c 400  # execution_markets
```

The last one must contain `execution_markets` with `US.enabled: true` and
`MY.enabled: false`. The OpenClaw relay reads that key; without it, it refuses every
market. Do not start the laptop side until this returns it.

### Runtime configuration

`/home/amanah/amanah-trader/backend/.env` — mode `0600`, owner `amanah:amanah`.
The execution-deciding values, which are not secrets:

```
PAPER_EXECUTION_ADAPTER=alpaca_mcp
PAPER_EXECUTION_ENABLED=true
```

`ALPACA_MODE` is absent and therefore defaults to `paper`; `alpaca_paper_adapter` hardcodes the
paper host regardless, so live trading stays impossible by construction rather than by config.

`ALLOWED_ORIGINS=https://amanahtrader.uk` was set on 2026-08-22 when the CORS change deployed.

### The Shariah vault, and a bug the `scp` left behind

The research vault is at `/home/amanah/shariah-vault` (`0750 amanah:amanah`, 20 MB, 34 `.md`
files), copied from `E:\Projects Stuff\Multi_Ai_IslamicFinance` on 2026-08-22 with
`08-Data-Governance/provider-decision-and-enquiry-drafts.md` deliberately excluded.

```
SHARIAH_UNIVERSE_PATH=/home/amanah/shariah-vault/08-Data-Governance/shariah-universe/staging/2026-05-29.json
SHARIAH_WIKI_PATH=/home/amanah/shariah-vault/01-Shariah-Principles
```

**Both variables previously held Windows paths** — `E:\Projects Stuff\...` — carried over verbatim
by the `scp` of the local `.env` that provisioned this box. On Linux they resolve to nothing, so
from the VPS build until 2026-08-22:

- `shariah_gate.check_symbol` returned `REJECT / universe_file_missing` for **every** symbol,
  which means the **Malaysian screening path was dead in production**. It fails closed, so nothing
  unsafe was ever approved, and the US path (`sec_edgar_screen`) was unaffected because it does
  not read this file — which is why the AAPL and CVX demos worked and hid the problem.
- `find_policy_context` returned `[]`, since its root did not exist.

Two things make this worth recording rather than just fixing. First, `config.py:29` loads `.env`
with `os.environ.setdefault`, so the **first** occurrence of a key wins — appending a corrected
line leaves the broken one in charge, silently. Edit the existing line; do not append. Second,
a config that fails closed looks identical to a config that is working correctly on the happy
path, and only a symbol that *should* pass distinguishes them. The fix was confirmed by checking
both directions: `0001` → `PASS / symbol_compliant` and `9999` → `REJECT / symbol_not_in_universe`.
A blanket `REJECT` on both would have meant it was still broken.

The vault universe file was verified identical in parsed form to the committed
`data/shariah-universe/2026-05-29.json` (688 records, `validation.status: active`) before being
pointed at. Note two decoy JSONs sit beside it — `schema.json` and `universe-manifest.json` —
and either would fail the gate; the live file is the one under `staging/`.

`SHARIAH_WIKI_PATH` feeds `wiki_context.find_policy_context`, which is called **only** from
`explain_compliance.main()`, the CLI. It is not reachable over HTTP — `local_api.py` never imports
it, and `/stock/{symbol}/explain` returns no `policy_context` key. Vault note content is therefore
never served to the public API. Worth knowing before pointing this at anything, because the vault
contains long verbatim extracts from copyrighted books (`Usmani-Intro-to-Finance.md`,
`Islamic-Finance-hans-vimmer-Summary.md`) that must not become a public endpoint's output.

## State

`/home/amanah/amanah-trader/backend/paper_trading.db` — SQLite, mode `0644`, owner `amanah`.

This is the demo's system of record and it exists **only on this box**. `backend/*.db` is
gitignored by design, so a trade run from a local checkout writes to a local file the deployed
instance never sees. Run the demo trade *through the deployed instance* so its own database
captures the position. It is backed up hourly — see `## Backups` below.

## Backups

`backend/backup_database.py` takes a timestamped, checksummed backup of `paper_trading.db` using
SQLite's own online backup API (safe to run while the app is serving traffic) into
`PAPER_DB_BACKUP_DIR` (default: a `backups/paper_trading_db/` directory one level above the app
checkout — deliberately outside `/home/amanah/amanah-trader` so a lost checkout does not also lose
the backups). Each backup gets a `.manifest.json` sidecar recording its SHA-256 and a row count per
table.

Scheduled via cron on the VPS (`crontab -e` as `amanah`):

```cron
0 * * * * cd /home/amanah/amanah-trader/backend && /home/amanah/amanah-trader/.venv/bin/python backup_database.py >> /home/amanah/backups/paper_trading_db/backup.log 2>&1
```

`PAPER_DB_BACKUP_DIR=/home/amanah/backups/paper_trading_db` should be set in the VPS `backend/.env`
so the cron line and any manual run agree on where backups land. Retention defaults to the newest 90
backups (`PAPER_DB_BACKUP_RETENTION`); at hourly cadence that is a little under 4 days — raise it if
more history is wanted, mindful of the droplet's 48 GB disk (7% used as of the 2026-08-22 audit).

**Restoring:** stop the service first (`sudo systemctl stop amanah-trader`), then:

```bash
cd /home/amanah/amanah-trader/backend
/home/amanah/amanah-trader/.venv/bin/python restore_database.py --backup /home/amanah/backups/paper_trading_db/paper_trading-<timestamp>.db
sudo systemctl start amanah-trader
```

`restore_database.py` verifies the backup's checksum against its manifest before writing anything,
and copies whatever was previously at the target aside as `paper_trading.db.pre-restore-<timestamp>.bak`
rather than discarding it. The restore drill in `backend/test_backup_restore.py` proves this whole
path — backup, corrupt, restore, verify row counts and checksums match — against scratch files; it
never touches the real database.

**Off-box replication is a known, accepted gap, not an oversight.** This backup lives on the same
droplet as the live file, on a separate directory rather than a separate disk or host, so a
droplet-level failure (not just a bad file or a bad deploy) would lose both. Deliberately not
built: it protects against a low-probability tail risk (total droplet loss) for a roughly one-week
hackathon window, at the cost of a new paid external service and credentials to manage under time
pressure — disproportionate to the risk. The two live trades that already ran (CVX, the AAPL
option) are independently safe regardless: their evidence is committed to git under
`docs/live-trade-evidence/`, which is genuinely off-box already.

## Watchlist scan freshness

`GET /opportunities` scans the watchlist and persists the result itself (throttled to once per
`min_scan_interval_minutes`, default 10) — it was never stale by design, only in practice, because
nothing was calling it except a human loading the dashboard. Cron now pings it every 30 minutes so
the quant engine (S001/S002) always has a result no older than that, whether or not anyone is
looking at the dashboard:

```cron
*/30 * * * * curl -s -o /dev/null http://127.0.0.1:8000/opportunities
```

Hits loopback directly, bypassing nginx — `/opportunities` is an open read with no operator key
needed, same as the dashboard's own requests. A cold scan takes ~5s; most runs land inside the
10-minute throttle window and return instantly with the existing snapshot re-served, so this is
cheap regardless of cadence.

## Audit results, 2026-08-22

### Sound

| Check | Result |
|---|---|
| `PasswordAuthentication` | `no` |
| `PermitRootLogin` | `no` |
| `PubkeyAuthentication` | `yes`, `PermitEmptyPasswords no`, `KbdInteractive no` |
| ufw | active, `deny (incoming)` default, only 22/80/443 open (v4 and v6) |
| Listening publicly | 22, 80, 443 only — app bound to loopback |
| `backend/.env` | `0600 amanah:amanah` |
| unattended-upgrades | enabled and active |
| certbot | `certbot.timer` enabled, cert valid 88 days |
| Secrets in `~/.bash_history` | **0 matches** |
| Secrets in `/root/.bash_history` | no file |
| Secrets in `journalctl -u amanah-trader` | **0 matches** |
| Secrets in nginx logs | **0 matches** |
| Alpaca key shape (`PK…`) anywhere in the journal | **0 matches** |

The secret scan counted matches without printing values. Nothing needs rotating on the evidence
of this audit.

### Gaps found

1. **No authentication on the deployed API.** `POST /paper/execute/{queue_id}` is reachable by
   anonymous callers; its only gate is the phrase `EXECUTE PAPER`, which is hardcoded at
   `backend/local_api.py:110`, published in this open-source repo, and echoed back to any caller
   in the rejection payload at `local_api.py:1533`. It is a typo-guard, not a credential. Two real
   broker fills have already been placed through this instance.
2. **`fail2ban` is not installed, against 11,348 failed SSH auth attempts in 7 days.** None can
   succeed — password auth is off and only pubkey is offered — so this is noise rather than
   exposure, but it is free to stop.
3. **nginx proxies everything to uvicorn, including junk.** The vhost is Certbot's default: a
   single `location /`. Of **261,092** requests logged today from 1,079 unique IPs, **260,957 were
   404s** and only **101** were 200s. Real traffic in that window was 61 dashboard loads and a
   handful of API calls. Every scanner request currently costs a Python round-trip and about
   89 MB/day of access log.
4. **The box is actively scanned for exactly this project's secrets.** Bots have requested
   `/secrets.yml`, `/secrets.json` and `/.streamlit/secrets.toml` repeatedly from Google Cloud
   ranges. They found nothing — those paths 404 — but it establishes that "nobody will look" is
   not a defence.
5. **`server_tokens` is commented out** (`nginx.conf:21`), so `Server: nginx/1.24.0 (Ubuntu)`
   discloses the exact version. No HSTS, `X-Content-Type-Options`, `X-Frame-Options` or
   `Referrer-Policy` headers are set.
6. **`/docs`, `/redoc` and `/openapi.json` are public**, advertising the full write surface. They
   have been hit 14 times.
7. **`amanah` has passwordless sudo and runs the app.** Any RCE in the app is root on this box.
   Accepted for a hackathon deployment; recorded so the trade-off is deliberate.
8. **4 packages upgradable.** Routine `apt update && apt upgrade` was last run manually by the
   project owner; no reboot is pending.

Nothing has hit `/paper/execute`, `/paper/reconcile` or `POST /audit` — **0 requests** across the
whole log. The exposure is real but has not been exercised.

## Hardening applied 2026-08-22

Gaps 1, 2, 3, 5 and 6 above are closed. Applied in this order — fail2ban, then the operator key,
then the vhost last, so the one change touching live traffic went in with the rest already
verified.

| | |
|---|---|
| `fail2ban` | installed, `sshd` jail only, `bantime 1h` / `findtime 10m` / `maxretry 5`, systemd backend. Config at `/etc/fail2ban/jail.local`. Deliberately **not** watching nginx: banning a judge mid-demo for clicking quickly is worse than the traffic it would stop. |
| Operator key | `/etc/nginx/conf.d/amanah_operator_key.conf`, `0600 root:root`, 64 hex chars from `openssl rand -hex 32`. Generated on the box; never printed, never committed. |
| Bucket tuning | `/etc/nginx/conf.d/00-amanah-tuning.conf`, `map_hash_bucket_size 128`. See below. |
| Vhost | replaced from `docs/deployment/nginx/amanahtrader.uk.conf`. Previous version backed up at `/etc/nginx/sites-available/amanahtrader.uk.bak-preharden`. |

**The bucket-size trap, recorded because it nearly caused an outage.** A 64-hex-character key
overflows nginx's default `map_hash_bucket_size` of 64, and the failure is not scoped to the map —
the *entire* config fails to build with `could not build map_hash`. Dropping the key file in place
left `nginx -t` failing while nginx kept serving from memory, so the next reload or reboot would
have taken the site down with no obvious cause. It was caught because `nginx -t` was run
immediately after writing the file rather than at the end.

A dry run of the vhost against a staged copy in `/tmp` had passed earlier and did **not** catch
this, because the placeholder key used there was 37 characters and fit in the default bucket.
**Validate with a key of realistic length**, or the test proves nothing about the real one.

### Verified after applying

| Probe | Before | After |
|---|---|---|
| `GET /health`, `/system/mode`, `/paper/status`, `/approvals`, `GET /audit` | 200 | 200 (unchanged, deliberately) |
| `GET /docs`, `/redoc`, `/openapi.json` | 200 | **404** |
| `Server:` header | `nginx/1.24.0 (Ubuntu)` | `nginx` |
| HSTS / nosniff / SAMEORIGIN / Referrer-Policy | absent | all four present |
| `POST /paper/execute/{id}` with no key | **200, wrote a ledger row** | **401** |
| `POST /paper/reconcile/{id}` with no key | 200 | **401** |
| `POST /audit` with no key | 422 (reached the app) | **401** |
| `POST /paper/execute/{id}` with a wrong key | n/a | **401** |
| `POST /paper/execute/{id}` with the right key | n/a | 200, the app's own `CONFIRMATION_REQUIRED` |
| `/secrets.json`, `/.env` | 404 from the app | 404 from nginx, logged to `scanner.log` |
| 30 parallel requests to a rate-limited route | all 200 | 13 × 200, 17 × **429** |

The `401`s are enforced at the edge: `journalctl -u amanah-trader` shows the app received the
pre-hardening probe and the correctly-keyed request, and **none** of the rejected ones.

The pre-hardening probe is worth keeping as evidence of what was actually exposed. A single
anonymous `POST /paper/execute/999999` returned `200`, created execution row 32 in the ledger, and
replied with `"required_confirmation": "EXECUTE PAPER"` — handing the caller the exact phrase
needed to proceed.

Demo re-verified end to end afterwards: `https://amanahtrader.uk/dashboard/` loads, API reports
Connected, and an AAPL preview completes — Shariah `PASS / US` off a live SEC EDGAR screen, price
source `alpaca_iex` over 221 bars, risk `PASS`, coordinator `REJECT` on `quant NO_SIGNAL`. That
rejection is the gate chain working as designed: a directional equity BUY still requires a BUY
signal. No console errors on a fresh load.

## The nginx vhost

As audited, before hardening. Certbot generated all of it; nothing was added by hand. The live
file is now the hardened version; this is kept for reference and is what
`amanahtrader.uk.bak-preharden` contains.

```nginx
server {
    server_name amanahtrader.uk www.amanahtrader.uk;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    listen 443 ssl; # managed by Certbot
    ssl_certificate /etc/letsencrypt/live/amanahtrader.uk/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/amanahtrader.uk/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
}

server {
    if ($host = www.amanahtrader.uk) { return 301 https://$host$request_uri; }
    if ($host = amanahtrader.uk)     { return 301 https://$host$request_uri; }
    listen 80;
    server_name amanahtrader.uk www.amanahtrader.uk;
    return 404; # managed by Certbot
}
```

The hardened replacement is kept in the repo at `docs/deployment/nginx/amanahtrader.uk.conf`, so
the vhost is reviewable in git even though the file that runs lives on the box. The operator-key
map is deliberately **not** in that file — it is a separate include holding a secret.

## Credentials

`deployed-instance-trades.json:16` records that the local `backend/.env` was `scp`-ed to the VPS
verbatim, which puts the same Alpaca credentials on a Windows dev box and on a public server.

**At kickoff (Aug 27–28), do not repeat that.** When the dedicated competition account is created,
issue it a **separate key pair for the VPS** and place only that pair in the VPS `.env`. The local
checkout keeps its own keys for the test account.

Kickoff sequence:

1. Create the dedicated Alpaca paper account.
2. Issue VPS-only API keys; write them into `/home/amanah/amanah-trader/backend/.env` (`0600`).
3. Run `provision_cash_account.py --no-shorting --apply` against the new account.
4. Confirm `check_alpaca_status` reports `CASH`, `max_margin_multiplier=1`, `no_shorting=True`.
5. Restart the unit; confirm `/system/mode` on the live host.

The nginx operator key lives only in `/etc/nginx/conf.d/amanah_operator_key.conf`, mode `0600`,
root-owned. It is never committed and never printed into a session — read it with `cat` on the box
when you need to drive a demo trade.

## Known gaps, not yet closed

- Backups exist (see `## Backups`) but are not off-box — a droplet-level failure still loses both
  the live file and its backups, since they sit on the same disk.
- No infrastructure as code. This document is the recovery path; a rebuild is manual.
- No monitoring or alerting. A crashed unit is discovered by loading the site.
- ~~**The nginx vhost in this repo has drifted from the one actually running**~~ —
  **RECONCILED 2026-09-21** (`29a0f73`). The repo copy is now byte-identical to the running
  file apart from its header comment, and that header now says *diff before you copy* and
  explains why. Rate limiting was applied by patching the live file rather than overwriting
  it; verified against a pre-reload baseline, with every endpoint returning the same status
  afterwards and 429s appearing once the burst was consumed.

  Kept below because the shape of the near-miss is the useful part. The live
  `/etc/nginx/sites-available/amanahtrader.uk` contained three blocks that
  `docs/deployment/nginx/amanahtrader.uk.conf` did not, and the repo file's own header said
  to `cp` over the live one:

  | live-only block | what it does |
  |---|---|
  | `location /hackathon/` | proxies to `127.0.0.1:8001` |
  | `location /thetanuts/api/` + `location /thetanuts/` | proxies to `127.0.0.1:8790` and serves `/home/amanah/thetanuts-copilot/frontend/dist/` |
  | `location = /` | 302 redirect from root to `/dashboard/` |

  **Copying the repo file over the live one would have taken down all three**, and the repo
  file's own header told you to do exactly that. The lesson outlives the fix: when a document
  says two things are kept in step, that is a claim to verify, not a fact to rely on. Diff
  first — the runbook now carries the command.

  The rate limiting is live: a 240r/m `amanah_general` zone on `location /`, plus the existing
  write zone on `/p3/*` and `/copilot/*`. `limit_req` sits inside `location /` rather than the
  server block, so `/hackathon/` and `/thetanuts/` are deliberately unchanged — they are other
  applications on this host, and throttling them is not this project's call.

## Moomoo OpenD on the droplet (Bursa execution)

**Do not build this. It would achieve nothing.**

Moomoo's Malaysian paper trading is an app product and is **not exposed to the OpenAPI** --
established by elimination on 2026-09-23 and recorded in full in CLAUDE.md limitation 5.
The MY paper account was opened in the app, OpenD was fully restarted, and a real Bursa
paper order was placed and left resting; `get_acc_list` still shows only an HK and a US
simulate account, across 8 SecurityFirm values and 6 TrdMarket values.

So OpenD on this droplet would connect successfully and then refuse every Malaysian order
with `active_my_simulate_account_not_found`, exactly as it does on the laptop. The daemon
was never the blocker. **This section is kept as a record of why, not as work to do.**

**Verified 2026-09-23 against a live OpenD gateway on the laptop: there is no Malaysian
simulate account on the owner's moomoo login.** `get_acc_list()` was enumerated under
every `TrdMarket` filter and returned three accounts: a REAL MARGIN account authorised for
HK/US/SG/MY, a SIMULATE CASH account authorised for **HK only**, and a SIMULATE MARGIN
account authorised for **US only**. Filtering by `TrdMarket.MY` returns the REAL account
and nothing else.

Moomoo provisions a separate simulated account per market, and Malaysia is not provisioned
here. The RM1,000,000 Malaysian paper trading in the moomoo app is evidently a different
object from an OpenAPI simulate account. `unlock_trade` does not change it — that governs
order placement, not enumeration, and the list was identical before and after unlocking.

So **putting OpenD on this droplet would achieve nothing today**: the adapter would connect,
find no MY simulate account, and refuse with `active_my_simulate_account_not_found` exactly
as it does on the laptop. The prerequisite is an account question for moomoo — can a
Malaysian simulate account be exposed to OpenAPI at all? — not a server build.

**The unblock condition is exact**: a simulate account with `MY` in `trdmarket_auth`.
Run this with OpenD up, and it reports the moment one exists:

```powershell
.\.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'backend');from moomoo_status import check_moomoo_status;print(check_moomoo_status('MY'))"
```

Today it returns `active_my_simulate_account_not_found (simulate accounts on this login:
HK/STOCK, US/STOCK_AND_OPTION)` -- which distinguishes "not provisioned" from
"misconfigured" without re-investigating anything.

Everything below stays as the plan for **if and when** that changes.



Alpaca has no Bursa access, so Moomoo is the only Malaysian route, and the Moomoo SDK
talks to OpenD -- a logged-in gateway process. It has to run somewhere the backend can
reach, and the backend runs here. Running OpenD on a laptop does not work: this droplet
cannot reach a machine behind NAT.

On this host it is a better fit than it first sounds. There is a **command-line OpenD**
supporting Ubuntu (this box is 24.04), it has a background-operation mode, and its default
listen address is `127.0.0.1:11111` -- already what `MOOMOO_HOST` / `MOOMOO_PORT` expect.
Nothing is exposed, ufw is unchanged, and the documented requirement that "if the listening
address is not local, you must configure a private key" does not apply, because it is local.

### Do the laptop first

Three things about the Moomoo path are transcribed from documentation, not observed: the
`MY.5225` code format, the MY account lookup, and whether `place_order` accepts
`session=Session.NONE` and `fill_outside_rth=False` for Bursa -- both US-market concepts.
None of those is a server question, and finding out here costs far more than finding out
on a laptop.

1. Install OpenD on the laptop, log in, confirm the MY paper account appears.
2. Run `backend/test_moomoo.py` by hand. It exists for exactly this -- it drives the SDK
   against a real gateway and is excluded from the census for that reason.
3. Run the backend locally with `PAPER_EXECUTION_ADAPTER_MY=moomoo` and put one Bursa
   order through preview -> approval -> execute -> reconcile. **Expect the first attempt
   to fail**, and record what OpenD actually rejects.
4. Fix it in `moomoo_paper_adapter.py`, with tests asserting the request that gets built.

### Then the droplet

5. Install command-line OpenD, listening on `127.0.0.1:11111`.
6. Configure a non-interactive login: from v10.10 "by default, starting OpenD directly
   enters interactive login mode", so credentials go in the config file instead. **Expect
   a one-time device verification** on first login from a new server -- undocumented
   either way, and entirely plausible for a real brokerage. Do it by hand, once.
7. A systemd unit beside `amanah-trader.service`, with the app unit ordered `After=` it.
   `check_moomoo_status` already fails in ~1.5s against a closed port, so the app starting
   first is survivable rather than a hang.
8. **Add swap.** This box is 2 GB with no swap, already running nginx, uvicorn and SQLite.
   OpenD's footprint is undocumented; measure its RSS before choosing a size.
9. Only then set `PAPER_EXECUTION_ADAPTER_MY=moomoo` in the droplet `.env` and restart.
10. Run the first Bursa order **through https://amanahtrader.uk**, not locally. A
    locally-run trade writes to a SQLite file this instance never sees.

### The credential is the uncomfortable part

An Alpaca paper key is worthless if leaked. A moomoo login is a real brokerage account,
and it will be sitting in a config file on a public-facing host. Keep it `0600` and owned
by `amanah`, never in git, and leave `MOOMOO_MODE=paper` -- `config.py` raises at startup
on anything else, and `moomoo_paper_adapter` hardcodes `TrdEnv.SIMULATE` at every call
site. Note the honest limit recorded in that module: paper and live share one OpenD socket
and one logged-in account, separated by a hardcoded enum. That is a well-guarded flag, not
the wall Alpaca's separate paper domain gives you.

## Stale files in the repo

Flagged rather than deleted, since they carry history:

| File | Why stale |
|---|---|
| `backend/LOCAL_DEPLOYMENT.md` | Moomoo-era, references a OneDrive path |
| `replit.md`, `.replit` | Replit hosting, superseded by this VPS |
| `backend/replit_start.sh` | Binds `0.0.0.0:8080`; the systemd unit above is authoritative |

`backend/replit_app.py` is **not** stale despite the name — it is the live entrypoint.
