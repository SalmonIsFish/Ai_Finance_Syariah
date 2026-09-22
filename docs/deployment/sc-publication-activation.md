# Activating an SC Malaysia publication in production

Written 2026-09-22, and **executed the same day** — this is no longer a proposal.
`sc-sac-my-2026-05-29` was activated at `2026-09-22T09:56:46Z` by `project_owner`,
and Malaysian screening has worked in production since. Keep this document for
the **next** SC release: the lists come out on the last Friday of May and
November, so the next run is due around 2026-11-27.

Run by the owner — every step needs credentials or writes to the live database,
and neither belongs to an agent.

## Why this exists

Before this was first run, the live droplet had **zero SC publications**. Every
Malaysian ticker returned `UNKNOWN / no_approved_publication`: trading was
blocked, which is the safe direction, but Malaysian screening did not actually
work in production, and the publication existed only on the owner's laptop.

It had been that way for weeks without anyone noticing, which is the lesson
worth keeping: **it failed safe, so nothing looked broken.** A gate that blocks
correctly and a gate that has no data to check against are indistinguishable
from the outside. Verify the positive case, not just the absence of errors.

The procedure is deliberately not automated — activation changes the screening
authority for a whole market.

## Your username

`project_owner`, role `admin` (the only configured admin). It comes from
`SC_ADMIN_AUTH_USERS` in the droplet's `backend/.env`. Wherever a command below
shows `<something>` in angle brackets, substitute it — never type it literally.

## Before you start

| | |
|---|---|
| The PDF | `Shariah-compliant-May 2026_final.pdf`, the official SC list. It is **not** in the repo and must not be committed. It stays on your machine. |
| `pdfplumber` | Needed only where you parse — your machine. **Do not install a PDF parser next to the live service**; the export route below exists so you don't have to. |
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
ssh -i ~/.ssh/amanahtrader_vps amanah@159.65.220.83
mkdir -p /home/amanah/backups
cp /home/amanah/amanah-trader/backend/paper_trading.db \
  /home/amanah/backups/paper_trading.pre-sc-$(date +%F).db
sha256sum /home/amanah/backups/paper_trading.pre-sc-$(date +%F).db
```

No `sudo` — you log in *as* `amanah`, which already owns the file. Note that
every command in this document runs from `/home/amanah/amanah-trader`; the
relative `.venv/bin/python` fails with "No such file or directory" anywhere else.

Keep that hash. It is how you prove afterwards what the database looked like
before, and how you verify a restore if one is ever needed. Hourly snapshots run
too, but retention is 90 files — under four days — so do not rely on them for a
change you are making deliberately.

## 2. Parse on your own machine, where the PDF already is

```powershell
.\.venv\Scripts\python.exe backend\sc_ingest_cli.py `
  --pdf "<path to>\Shariah-compliant-May 2026_final.pdf" `
  --publication-date 2026-05-29 --export-json sc-2026-05-29.json
```

This opens no database. It writes the exact rows that would be staged, plus the
`source_document_hash` of the PDF itself, and prints the file's own sha256.

Expect, and check every line:

```
  human_review_status: pending
  securities:          905
  official total:      886
  unique tickers:      886
  unresolved discrep.: 0
```

`905 = 887 compliant + 18 non-compliant`; the 886 figures are the main list's
own stated total, which the parser reconciles against exactly.

**If `human_review_status` reads `needs_reconciliation`, stop.** The parse did
not agree with the document's own totals. Such a publication cannot be activated
at all — the store refuses — and the right response is to find out why, not to
work around it. Carrying the payload to another machine will not change it:
`ingest_payload` recomputes nothing, so a failed reconciliation cannot be
laundered into a stageable one by moving it.

## 3. Rehearse against a copy — recommended

`--db` points the ingest at any SQLite file, so the staging can be proven before
the live database is touched at all:

```bash
.venv/bin/python backend/sc_ingest_cli.py --from-export sc-2026-05-29.json \
  --publication-date 2026-05-29 --db /tmp/sc-rehearsal.db --apply
```

## 4. Carry the payload over and ingest into the live database

```bash
scp sc-2026-05-29.json amanah@<droplet>:/tmp/
ssh amanah@<droplet>
sha256sum /tmp/sc-2026-05-29.json   # must match what step 2 printed

cd /home/amanah/amanah-trader
.venv/bin/python backend/sc_ingest_cli.py \
  --from-export /tmp/sc-2026-05-29.json --publication-date 2026-05-29
# read the dry run, then:
.venv/bin/python backend/sc_ingest_cli.py \
  --from-export /tmp/sc-2026-05-29.json --publication-date 2026-05-29 --apply
```

Carrying the parse rather than re-parsing is not only about the missing
dependency. **The parse you reviewed in step 2 is then byte-for-byte the one
that gets staged.** Re-parsing on the droplet would mean activating something
nobody inspected, and a different `pdfplumber` version could extract
differently with nothing downstream able to tell. Provenance is unaffected: the
publication row records the hash of the *PDF*, not of this intermediate file.
Verified — both routes produce identical `sc_publications` and
`sc_security_status` rows.

Ingesting is safe to do before you are ready to activate. A `pending`
publication screens nothing: `check_eligibility` reads the *active* publication,
not the newest one, and `test_sc_ingest_cli.py` holds that guarantee. This is
also why ingestion does not require admin credentials and approval does.

## 5. Inspect before approving

```bash
.venv/bin/python backend/sc_admin_cli.py show <publication-id>
.venv/bin/python backend/sc_admin_cli.py securities <publication-id> --ticker 5225
```

`5225` (IHH Healthcare) should read `COMPLIANT`. **Also check a Table 2 ticker**
— a list where nothing is ever rejected is the exact failure mode the committed
JSON would have produced silently, and it is invisible unless you look for it.
In the May 2026 publication, `0026` (Nova MSC) and `0156` (ManagePay) are
NON_COMPLIANT.

By approving you attest the parse faithfully represents the SC's document, so
this is the moment to spot-check against the PDF — not after.

## 6. Approve, then activate

Two separate commands by design. Approval is a human saying the parse is
faithful; activation is a human making it the screening authority. **Approve
first** — activate refuses an unapproved publication.

```bash
.venv/bin/python backend/sc_admin_cli.py \
  approve <publication-id> --username project_owner --apply
.venv/bin/python backend/sc_admin_cli.py \
  activate <publication-id> --username project_owner --apply
```

Each prompts `Password for project_owner:` (no echo), or reads
`SC_ADMIN_CLI_PASSWORD`. Expect `Authenticated as 'project_owner' (role=admin).`
before each result; on `AUTHENTICATION FAILED` nothing was written and you can
simply retry.

Activation runs a Malaysia-scoped holdings sweep afterwards and prints anything
newly non-compliant. It never raises: a sweep failure must not undo an
activation that succeeded.

## 7. Verify from outside

The CLI telling you it worked is not evidence. The API is:

```bash
curl -s https://amanahtrader.uk/api/shariah/5225   # expect PASS
curl -s https://amanahtrader.uk/api/shariah/1155   # expect UNKNOWN, never REJECT
curl -s https://amanahtrader.uk/api/shariah/0026   # expect REJECT
```

**Check all three states.** Each is a different property, and passing one says
nothing about the others:

| Ticker | Expected | What it proves |
|---|---|---|
| `5225` IHH | `PASS` / `authoritative_compliant` | Screening works at all |
| `1155` Maybank, absent from the list | `UNKNOWN` / `not_present_in_approved_publication` | Absence is **not** treated as an SC ruling of ineligibility |
| `0026` Nova MSC, Table 2 | `REJECT` / `authoritative_non_compliant` | The reclassifications loaded — **the divestment path works** |

Every verdict must also carry `publication_id` and `source_document_hash`; that
is what makes a verdict traceable to the document it came from.

If `5225` still says `no_approved_publication`, activation did not take — check
`sc_admin_cli.py list`.

**Observed on 2026-09-22** after the first real run: all three as expected, hash
`d6592a55f54fd113d90945fd1d0b534a4a1b6451a7877ca4103ec7846799db85`, 887
COMPLIANT + 18 NON_COMPLIANT.

## What this does and does not establish

It makes Malaysian screening real in production: the system now applies the SC
SAC list and can prove which document it applied, by hash and as-of date.

It does not make the system Shariah-compliant. Being on the SC list settles the
status of a *security*. It certifies neither a trading strategy nor an automated
system — see `docs/shariah-policy/reviewer-packet.md` for what still needs a
scholar's ruling.
