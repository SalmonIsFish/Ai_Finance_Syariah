# Amanah Trader — dashboard

The operator dashboard for Amanah Trader's paper-trading gate chain. React 19, Vite,
Tailwind v4, react-router 7. Production serves the built `dist/` at `/dashboard/` behind
owner authentication (`backend/local_api.py`).

The backend decides; this app displays what it decided. It never computes a verdict,
and it shows only what the backend actually said. A value it does not have is rendered
"—", never a reassuring default. See the root `CLAUDE.md` for how to describe the
system: it applies an authority's Shariah classification of a *security*; it is not
"Shariah-compliant".

## Run

```bash
npm ci
npm run dev      # Vite dev server; proxies the API to 127.0.0.1:8000 (vite.config.js)
npm run build    # writes dist/ — gitignored, so the server builds it on deploy
npm run lint     # oxlint
npm test         # node --test, no extra dependencies
```

Start the API first, from the repo root:
`.venv\Scripts\python.exe -m uvicorn local_api:app --app-dir backend --port 8000`.

## Pages

| Route | Page | What it is for |
|---|---|---|
| `/` | The Desk | Ticket → server preview → review → approve. Approving queues the order; it never sends one. Execution is done by the operator relay with `EXECUTE PAPER`. |
| `/risk` | Portfolio & Risk | Live balances and positions (30 s refresh), equity history, holdings re-screened against the current authority, risk limits |
| `/market` | Market & Screening | Watchlist scan, ready opportunities, stock profile, news |
| `/securities` | Shariah Universe | The SC Malaysia list in force, with provenance; US symbols screened so far |
| `/ledger` | The Ledger | Approval queue, execution audit, audit history — searchable, with recorded payloads |

## Conventions

- **Formatting:** `src/format.js` only (`fmtMoney`, `fmtPct`, `fmtQty`, `fmtDateTime`, …).
  Money uses the market's currency (`market.js`). Times are shown in MYT and labelled.
- **Data:** `src/useResource.js` for anything that should refresh or show its age.
  A failed refresh keeps the last good data and marks it stale.
- **Verdict colours** (`src/verdict.js`) mean permitted / refused / unknown. Do not use
  them for anything else; operational state uses neutral chips (`StatusBar.jsx`).
  UNKNOWN never shares REJECT's colour.
- **Shared components:** `DataTable`, `Segmented`, `Panel`, `ErrorNote`, `OfficerCard`,
  `EvidenceTrail`.
- **Fonts** are self-hosted through `@fontsource-variable/*`, with no third-party font CDN.
