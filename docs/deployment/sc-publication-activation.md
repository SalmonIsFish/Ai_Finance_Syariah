# Activating an SC Malaysia publication in production

Written 2026-09-22. Run by the owner — every step here needs credentials or
writes to the live database, and neither belongs to an agent.

## Why this exists

The live droplet has **zero SC publications**. Every Malaysian ticker therefore
returns `UNKNOWN / no_approved_publication`: trading is blocked, which is the
safe direction, but Malaysian screening does not actually work in production.
The publication exists only in the local database on the owner's machine.

This is the procedure that closes that gap. It is four commands and two
verifications, and it is deliberately not automated — activation changes the
screening authority for a whole market.

## Before you start

| | |
|---|---|
| The PDF | `Shariah-compliant-May 2026_final.pdf`, the official SC list. It is **not** in the repo and must not be committed. |
| `pdfplumber` | **Not installed on the droplet.** Install it into the service venv first. |
| Admin credentials | `approve` and `activate` both authenticate. |
| A backup | Hourly snapshots run, but retention is 90 hourly files — under four days. Take one now anyway. |

**Do not substitute the committed JSON.** `data/shariah-universe/2026-05-29.json`
holds 688 records, all COMPLIANT, and the official PDF holds **887 COMPLIANT
plus 18 NON_COMPLIANT**. Ingesting the JSON would turn 18 securities the SC
explicitly ruled ineligible into "we could not confirm". Orders would still be
blocked either way — but a *holding* in one of those 18 would then report as
UNCONFIRMED rather than NON_COMPLIANT, and **the divestment duty would never
fire**. With a one-month disposal window, that is a real obligation missed, not
a cosmetic difference. `sc_ingest_cli.py` reads only the PDF for this reason.

## 1. Back up

```bash
ssh <droplet>
sudo -u amanah cp /home/amanah/amanah-trader/backend/paper_trading.db \
  /home/amanah/backups/paper_trading.pre-sc-activation.db
sha256sum /home/amanah/backups/paper_trading.pre-sc-activation.db
```

Keep that hash. It is how you prove afterwards what the database looked like
before, and how you verify a restore if one is ever needed.

## 2. Get the PDF and the parser onto the droplet

```bash
# from the owner's machine
scp "Shariah-compliant-May 2026_final.pdf" <droplet>:/tmp/sc-2026-05-29.pdf

# on the droplet
sudo -u amanah /home/amanah/amanah-trader/.venv/bin/pip install pdfplumber
```

The PDF goes to `/tmp` on purpose: it is source material for one ingest, not
application data, and the hash of what was parsed is recorded in the publication
row (`source_document_hash`) — so the record of *which document was used*
survives even after the file is deleted.

## 3. Rehearse against a copy — recommended

`--db` points the ingest at any SQLite file, so the parse can be proven before
the live database is touched at all:

```bash
sudo -u amanah /home/amanah/amanah-trader/.venv/bin/python \
  backend/sc_ingest_cli.py --pdf /tmp/sc-2026-05-29.pdf \
  --publication-date 2026-05-29 --db /tmp/sc-rehearsal.db --apply
```

Expect, and check every line:

```
  human_review_status: pending
  securities_inserted: 905
  official total:      886
  unique tickers:      886
  unresolved discrep.: 0
```

`905 = 887 compliant + 18 non-compliant`; the 886 figures are the main list's
own stated total, which the parser reconciles against exactly.

**If `human_review_status` reads `needs_reconciliation`, stop.** The parse did
not agree with the document's own totals. Such a publication cannot be activated
at all — the store refuses — and the right response is to find out why, not to
work around it.

## 4. Ingest into the live database

```bash
cd /home/amanah/amanah-trader
sudo -u amanah .venv/bin/python backend/sc_ingest_cli.py \
  --pdf /tmp/sc-2026-05-29.pdf --publication-date 2026-05-29
# read the dry run, then:
sudo -u amanah .venv/bin/python backend/sc_ingest_cli.py \
  --pdf /tmp/sc-2026-05-29.pdf --publication-date 2026-05-29 --apply
```

Ingesting is safe to do before you are ready to activate. A `pending`
publication screens nothing: `check_eligibility` reads the *active* publication,
not the newest one, and `test_sc_ingest_cli.py` holds that guarantee. This is
also why ingestion does not require admin credentials and approval does.

## 5. Inspect before approving

```bash
sudo -u amanah .venv/bin/python backend/sc_admin_cli.py show sc-sac-my-2026-05-29
sudo -u amanah .venv/bin/python backend/sc_admin_cli.py securities sc-sac-my-2026-05-29 --ticker 5225
```

`5225` (IHH Healthcare) should read `COMPLIANT`. Check a known non-compliant
ticker from Table 2 of the PDF too — a list where nothing is ever rejected is
the exact failure mode the JSON would have produced silently.

## 6. Approve, then activate

Two separate commands by design. Approval is a human saying the parse is
faithful; activation is a human making it the screening authority.

```bash
sudo -u amanah .venv/bin/python backend/sc_admin_cli.py \
  approve sc-sac-my-2026-05-29 --username <you> --apply
sudo -u amanah .venv/bin/python backend/sc_admin_cli.py \
  activate sc-sac-my-2026-05-29 --username <you> --apply
```

Activation runs a Malaysia-scoped holdings sweep afterwards and prints anything
newly non-compliant. It never raises: a sweep failure must not undo an
activation that succeeded.

## 7. Verify from outside

The real test is what the API tells a caller, not what the CLI told you:

```bash
curl -s https://amanahtrader.uk/api/shariah/5225
```

Must return `PASS` with a `publication_id` of `sc-sac-my-2026-05-29`. If it
still says `no_approved_publication`, activation did not take — check
`sc_admin_cli.py list`.

Then check a ticker that is genuinely absent from the list. It must return
`UNKNOWN`, **not** `REJECT`. Absence from the SC list is not an SC ruling of
ineligibility, and the gate must not invent one; only Table 2's explicit
reclassifications are REJECT.

## What this does and does not establish

It makes Malaysian screening real in production: the system now applies the SC
SAC list and can prove which document it applied, by hash and as-of date.

It does not make the system Shariah-compliant. Being on the SC list settles the
status of a *security*. It certifies neither a trading strategy nor an automated
system — see `docs/shariah-policy/reviewer-packet.md` for what still needs a
scholar's ruling.
