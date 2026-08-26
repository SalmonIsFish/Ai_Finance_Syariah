# Production Resilience (backup/restore, cutover pre-flight, audit export) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deployed system operationally survivable for the judging window without touching
the gate chain, execution path, or screening logic: a proven SQLite backup/restore path, a
pre-flight check before the hackathon-account cutover, and a judge/scholar-facing audit export.

**Architecture:** Three independent, plain-script deliverables added to `backend/`, following the
repo's existing "plain script with `main()`, tests are plain scripts with `main()` printing `PASS:`"
convention (see `provision_cash_account.py` / `test_provision_cash_account.py`). Each reads existing
state (the SQLite file, the Alpaca REST API via the existing `alpaca_request`/`check_alpaca_status`
seams, the `shariah_screens` table) and produces new output (a backup file, a go/no-go report, a
Markdown document). None of them modify `shariah_gate.py`, `option_structure_gate.py`,
`account_shariah_gate.py`, `paper_execution.py`, `sec_edgar_screen.py`, or `sec_edgar_cache.py`.

**Tech Stack:** Python 3 stdlib only (`sqlite3`, `hashlib`, `shutil`, `pathlib`, `datetime`, `json`,
`urllib` via the existing seams). No new dependencies.

**Spec:** `hackathon/alpaca-2026/council_output_round3_synthesis.md` (the council consult that set
"operational resilience over new features" as the pre-kickoff priority) and the task message that
opened this branch (reproduced in Global Constraints below).

## Global Constraints

- Do not implement time-varying re-screening.
- Do not modify `check_us_symbol` or `sec_edgar_cache.py` (read `shariah_screens` only; never touch
  the raw EDGAR cache).
- Do not switch databases or test frameworks (SQLite stays; tests stay plain `main()` + `PASS:`
  scripts, no pytest).
- Do not touch the gate chain (`shariah_gate.py`, `option_structure_gate.py`,
  `account_shariah_gate.py`) or the execution path (`paper_execution.py`, `alpaca_paper_adapter.py`,
  `approval_workflow.py`).
- All three deliverables are read-only with respect to the gate chain and the broker's write
  surface: they may read `GET /v2/account` and `GET /v2/account/configurations` (already-used
  seams), and may read/copy the local SQLite file. None of them submit an order or patch the
  account.
- Every new script gets `.venv\Scripts\ruff.exe check --fix .` / `ruff.exe format .` run on it (the
  `PostToolUse` hook does this automatically per file) and a `test_*.py` companion in the same style
  as `test_provision_cash_account.py`.
- Run the full existing 42-script suite before starting (done — all pass, see below) and again
  before declaring each part finished.
- No secrets printed or committed. None of these scripts touch `ALPACA_API_KEY_ID` /
  `ALPACA_SECRET_KEY` values directly — `alpaca_credentials()` / `alpaca_request()` already keep
  those out of return values and logs.
- Report back to the user per-item (Part A, then B, then C) as each is finished, not all three at
  once at the end.

**Pre-flight already done for this plan:** worktree created at `.worktrees/prod-resilience` on
`feature/prod-resilience`; `backend/.env` copied from the main checkout so config-dependent tests
match; full 42-script suite run and passing (`test_option_strategy_api.py` needs `backend/.env`
present — it read `adapter_not_alpaca` in a bare worktree with no `.env`, which is expected and not
a regression, confirmed by running the same script against the main checkout).

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/backup_database.py` | Take a timestamped, checksummed, safe-while-live backup of `paper_trading.db` into a separate directory; prune old backups. |
| `backend/restore_database.py` | Restore a target SQLite file from a backup, verifying the backup's checksum first and backing up whatever was at the target before overwriting it. |
| `backend/test_backup_restore.py` | The drill: seed a scratch DB, back it up, corrupt/delete the scratch copy, restore, verify row counts and checksums match. Also proves a tampered backup is refused. |
| `backend/cutover_preflight.py` | Read-only go/no-go report before `provision_cash_account.py --apply` against the new hackathon account: paper-ness, current margin/multiplier state, local ledger state. |
| `backend/test_cutover_preflight.py` | Pure-function tests over the report-building logic, network and DB seams swapped exactly like `test_provision_cash_account.py`. |
| `backend/audit_export.py` | Bundle `paper_positions`, `paper_fills`, `shariah_screens` verdicts (incl. the EDGAR ratio evidence already persisted in each verdict's payload) into one timestamped Markdown document. |
| `backend/test_audit_export.py` | Seed a scratch DB with sample ledger + screen rows, render the Markdown, assert the evidence actually appears in it. |
| `docs/deployment/VPS_RUNBOOK.md` | Modified: add a "Backups" section documenting the cron line and restore procedure; move the backup line out of "Known gaps, not yet closed". |
| `backend/.env.example` | Modified: document `PAPER_DB_BACKUP_DIR` and `PAPER_DB_BACKUP_RETENTION`. |

No existing file in the gate/execution/screening path is modified. `local_api.py` is not touched at
all — none of these three deliverables need an HTTP endpoint; they are operator-run CLI scripts, same
as `provision_cash_account.py`.

---

## Part A — Automated SQLite backup + restore drill

### Task A1: `backup_database.py` — safe, checksummed, timestamped backup

**Files:**
- Create: `backend/backup_database.py`
- Test: `backend/test_backup_restore.py` (shared with Task A2/A3 — one drill script covers backup,
  restore, and the corruption scenario, since they are one property: "a backup that has been
  restored from and verified")

**Interfaces:**
- Produces:
  - `table_row_counts(connection: sqlite3.Connection) -> dict[str, int]` — introspects
    `sqlite_master` for every user table (`SELECT name FROM sqlite_master WHERE type='table' AND
    name NOT LIKE 'sqlite_%'`) and returns `{table_name: COUNT(*)}`. Dynamic on purpose: it must
    keep working as other engineers add tables (`watchlist_store.py`, `shariah_screen_store.py`,
    etc.) without this file needing an edit.
  - `sha256_of_file(path: Path) -> str`
  - `backup_database(db_path: Path, backup_dir: Path, *, now: Callable[[], datetime] | None = None) -> dict`
    — returns `{"status": "OK", "backup_path": str, "manifest_path": str, "checksum": str,
    "row_counts": dict, "created_at": iso str}`. Raises `SystemExit` if `db_path` does not exist.
  - `prune_old_backups(backup_dir: Path, *, keep: int) -> list[str]` — deletes all but the `keep`
    newest `*.db` backups (and their matching `*.manifest.json`), returns the deleted filenames.
  - `DEFAULT_BACKUP_DIR = BACKEND_DIR.parent / "backups" / "paper_trading_db"`
  - `DEFAULT_RETENTION = 90`

- [ ] **Step 1: Write the failing test for `table_row_counts` and `sha256_of_file`**

```python
# backend/test_backup_restore.py (new file — top of it)
"""Prove the backup/restore path actually works, not just that it runs.

A backup nobody has restored from is not a backup. This drill seeds a scratch
SQLite file with known rows, backs it up, destroys the scratch "live" copy the
way a disk failure or a bad migration would, restores it, and checks the
restored file is byte-identical to the backup and has the same row counts as
the original. It also proves a tampered backup is refused rather than silently
restored. Nothing here touches the real backend/paper_trading.db -- every path
is inside a tempfile.TemporaryDirectory().
"""

import hashlib
import sqlite3
import tempfile
from pathlib import Path

import backup_database
import restore_database


def make_seed_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE paper_positions (id INTEGER PRIMARY KEY, symbol TEXT)")
        connection.execute("CREATE TABLE paper_fills (id INTEGER PRIMARY KEY, symbol TEXT)")
        connection.executemany(
            "INSERT INTO paper_positions (symbol) VALUES (?)", [("CVX",), ("AAPL",)]
        )
        connection.executemany(
            "INSERT INTO paper_fills (symbol) VALUES (?)", [("CVX",), ("AAPL",), ("AAPL",)]
        )
        connection.commit()
    finally:
        connection.close()


def test_table_row_counts_and_checksum() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        make_seed_db(db_path)

        connection = sqlite3.connect(db_path)
        try:
            counts = backup_database.table_row_counts(connection)
        finally:
            connection.close()
        assert counts == {"paper_positions": 2, "paper_fills": 3}, counts

        checksum = backup_database.sha256_of_file(db_path)
        assert checksum == hashlib.sha256(db_path.read_bytes()).hexdigest(), checksum
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe backend\test_backup_restore.py` (invoked as a script for now — `main()`
comes in the final step; for this intermediate step run it via
`python -c "import test_backup_restore as t; t.test_table_row_counts_and_checksum()"` from
`backend/`)
Expected: `ModuleNotFoundError: No module named 'backup_database'`

- [ ] **Step 3: Write `backend/backup_database.py`**

```python
"""Timestamped, checksummed backup of backend/paper_trading.db to separate storage.

Uses sqlite3's own online backup API (Connection.backup()), which is safe to run
against a database a live process still has open -- unlike a raw file copy, it
cannot capture a half-written page. The result is one self-contained .db file
plus a sidecar .manifest.json recording its SHA-256 and a per-table row count,
so a later restore (restore_database.py) can verify the backup before trusting
it, without having to re-open and re-scan the .db file to do so.

"Separate storage" here means a directory outside backend/ (not backend/*.db,
which is gitignored and lives on the same disk as the live file). On the VPS,
point PAPER_DB_BACKUP_DIR at a path outside the app checkout
(docs/deployment/VPS_RUNBOOK.md's Backups section) -- true off-box replication
(S3, a second host) is future work, not something a 1-2 day pre-kickoff window
can add safely; a separate directory is the floor this script guarantees.

Run on a schedule (cron on the VPS; Task Scheduler locally) via `main()`, or
call backup_database() directly from another script/test.
"""

import hashlib
import json
import sqlite3
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from config import BACKEND_DIR

DB_PATH = BACKEND_DIR / "paper_trading.db"
DEFAULT_BACKUP_DIR = BACKEND_DIR.parent / "backups" / "paper_trading_db"
DEFAULT_RETENTION = 90

TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def table_row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Row count per user table, discovered dynamically.

    Dynamic rather than a hardcoded table list so this keeps working as new
    tables are added elsewhere in the repo without this file needing an edit.
    """
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]
    counts = {}
    for table in tables:
        counts[table] = connection.execute(f"SELECT COUNT(*) FROM '{table}'").fetchone()[0]
    return counts


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_backup(source_path: Path, dest_path: Path) -> None:
    """Copy source_path into dest_path using sqlite3's online backup API."""
    source = sqlite3.connect(source_path)
    dest = sqlite3.connect(dest_path)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()


def backup_database(
    db_path: Path,
    backup_dir: Path,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"cannot back up: {db_path} does not exist")

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = (now or utc_now)().strftime(TIMESTAMP_FORMAT)
    backup_path = backup_dir / f"paper_trading-{timestamp}.db"
    manifest_path = backup_dir / f"paper_trading-{timestamp}.manifest.json"

    _sqlite_backup(db_path, backup_path)

    source_connection = sqlite3.connect(db_path)
    try:
        row_counts = table_row_counts(source_connection)
    finally:
        source_connection.close()

    checksum = sha256_of_file(backup_path)
    manifest = {
        "created_at": (now or utc_now)().isoformat(),
        "source_path": str(db_path),
        "backup_path": str(backup_path),
        "checksum_sha256": checksum,
        "row_counts": row_counts,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "OK",
        "backup_path": str(backup_path),
        "manifest_path": str(manifest_path),
        "checksum": checksum,
        "row_counts": row_counts,
        "created_at": manifest["created_at"],
    }


def prune_old_backups(backup_dir: Path, *, keep: int) -> list[str]:
    """Delete all but the `keep` most recent backups (by filename, which sorts
    chronologically because the timestamp is zero-padded and UTC)."""
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []
    backups = sorted(backup_dir.glob("paper_trading-*.db"))
    to_delete = backups[:-keep] if keep > 0 else backups
    deleted = []
    for path in to_delete:
        manifest = path.with_suffix("").with_suffix(".manifest.json")
        path.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        deleted.append(path.name)
    return deleted


def main() -> None:
    import os

    backup_dir = Path(os.getenv("PAPER_DB_BACKUP_DIR", str(DEFAULT_BACKUP_DIR)))
    retention = int(os.getenv("PAPER_DB_BACKUP_RETENTION", str(DEFAULT_RETENTION)))

    report = backup_database(DB_PATH, backup_dir)
    print(f"backed up: {report['backup_path']}")
    print(f"checksum:  {report['checksum']}")
    print(f"rows:      {json.dumps(report['row_counts'])}")

    deleted = prune_old_backups(backup_dir, keep=retention)
    if deleted:
        print(f"pruned:    {len(deleted)} backup(s) beyond retention ({retention}): {deleted}")


if __name__ == "__main__":
    sys.exit(main() or 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ../.venv/Scripts/python.exe -c "import test_backup_restore as t; t.test_table_row_counts_and_checksum()"`
Expected: no output, no exception (exits 0)

- [ ] **Step 5: Commit**

```bash
git add backend/backup_database.py backend/test_backup_restore.py
git commit -m "Add checksummed SQLite backup with dynamic row-count manifest"
```

### Task A2: `restore_database.py` — checksum-verified restore

**Files:**
- Create: `backend/restore_database.py`
- Test: append to `backend/test_backup_restore.py`

**Interfaces:**
- Consumes: `backup_database.sha256_of_file`, the manifest shape from Task A1
  (`{"checksum_sha256": str, "row_counts": dict, "backup_path": str}`).
- Produces:
  - `restore_database(backup_path: Path, target_path: Path, *, force: bool = False, now: Callable[[], datetime] | None = None) -> dict`
    — returns `{"status": "OK", "target_path": str, "pre_restore_backup": str | None,
    "checksum": str}`. Raises `SystemExit` if the manifest is missing, or if the backup file's
    live checksum does not match the manifest's `checksum_sha256` and `force` is not `True`.
  - If `target_path` already exists, it is copied aside to
    `{target_path}.pre-restore-{timestamp}.bak` before being overwritten — so a restore run against
    the wrong target is itself recoverable.

- [ ] **Step 1: Write the failing tests**

```python
# backend/test_backup_restore.py (append)


def test_backup_then_restore_matches_original() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        live_path = Path(tmp) / "paper_trading.db"
        backup_dir = Path(tmp) / "backups"
        make_seed_db(live_path)

        original_checksum = backup_database.sha256_of_file(live_path)
        report = backup_database.backup_database(live_path, backup_dir)

        # ---- simulate disaster: the "live" file is corrupted beyond use ----
        live_path.write_bytes(b"not a sqlite file at all")

        restore_report = restore_database.restore_database(
            Path(report["backup_path"]), live_path
        )
        assert restore_report["status"] == "OK", restore_report

        restored_checksum = backup_database.sha256_of_file(live_path)
        assert restored_checksum == report["checksum"], "restored file must match the backup"

        connection = sqlite3.connect(live_path)
        try:
            restored_counts = backup_database.table_row_counts(connection)
        finally:
            connection.close()
        assert restored_counts == {"paper_positions": 2, "paper_fills": 3}, restored_counts

        # the pre-restore corrupted file must have been preserved, not just discarded
        pre_restore = Path(restore_report["pre_restore_backup"])
        assert pre_restore.exists(), "the corrupted file that was overwritten must be kept"
        assert pre_restore.read_bytes() == b"not a sqlite file at all"


def test_restore_refuses_a_tampered_backup() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        live_path = Path(tmp) / "paper_trading.db"
        backup_dir = Path(tmp) / "backups"
        make_seed_db(live_path)
        report = backup_database.backup_database(live_path, backup_dir)

        # tamper with the backup after the manifest was written
        backup_path = Path(report["backup_path"])
        backup_path.write_bytes(backup_path.read_bytes() + b"\x00extra-bytes")

        before = live_path.read_bytes()
        try:
            restore_database.restore_database(backup_path, live_path)
        except SystemExit as exc:
            assert "checksum" in str(exc).lower(), exc
        else:
            raise AssertionError("a tampered backup must be refused without --force")
        assert live_path.read_bytes() == before, "a refused restore must not touch the target"

        # --force overrides the refusal explicitly
        forced = restore_database.restore_database(backup_path, live_path, force=True)
        assert forced["status"] == "OK", forced
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/Scripts/python.exe -c "import test_backup_restore as t; t.test_backup_then_restore_matches_original()"`
Expected: `ModuleNotFoundError: No module named 'restore_database'`

- [ ] **Step 3: Write `backend/restore_database.py`**

```python
"""Restore backend/paper_trading.db from a backup_database.py backup.

Verifies the backup's own checksum (recorded in its .manifest.json at backup
time) before touching anything -- a bit-rotted or tampered backup file must be
refused, not silently restored, since a "successful" restore of corrupted data
is worse than an obvious failure. Whatever currently sits at the target path is
copied aside first, so a restore aimed at the wrong target is itself
recoverable rather than a second disaster on top of the first.

This never restores INTO a path a running server still has open for writes --
stop the systemd unit (or the local uvicorn process) before restoring in
production; this script does not attempt to detect or stop it for you.
"""

import shutil
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from backup_database import TIMESTAMP_FORMAT, sha256_of_file


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _manifest_path_for(backup_path: Path) -> Path:
    # paper_trading-<ts>.db -> paper_trading-<ts>.manifest.json
    return backup_path.with_name(backup_path.stem + ".manifest.json")


def restore_database(
    backup_path: Path,
    target_path: Path,
    *,
    force: bool = False,
    now: Callable[[], datetime] | None = None,
) -> dict:
    import json

    backup_path = Path(backup_path)
    target_path = Path(target_path)

    if not backup_path.exists():
        raise SystemExit(f"backup file does not exist: {backup_path}")

    manifest_path = _manifest_path_for(backup_path)
    if not manifest_path.exists():
        if not force:
            raise SystemExit(
                f"no manifest found at {manifest_path}; refusing to restore an "
                "unverifiable backup without --force"
            )
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_checksum = manifest.get("checksum_sha256")
        actual_checksum = sha256_of_file(backup_path)
        if expected_checksum and actual_checksum != expected_checksum and not force:
            raise SystemExit(
                f"backup checksum mismatch: manifest says {expected_checksum}, file is "
                f"{actual_checksum}. The backup may be corrupted or tampered with. "
                "Pass force=True / --force only if you have independently verified it."
            )

    pre_restore_backup = None
    if target_path.exists():
        timestamp = (now or utc_now)().strftime(TIMESTAMP_FORMAT)
        pre_restore_backup = target_path.with_name(
            f"{target_path.name}.pre-restore-{timestamp}.bak"
        )
        shutil.copy2(target_path, pre_restore_backup)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, target_path)

    return {
        "status": "OK",
        "target_path": str(target_path),
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "checksum": sha256_of_file(target_path),
    }


def main() -> None:
    import argparse

    from backup_database import DB_PATH

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, help="path to the .db backup file to restore")
    parser.add_argument("--target", default=str(DB_PATH), help="path to restore into")
    parser.add_argument(
        "--force", action="store_true", help="restore even if the checksum cannot be verified"
    )
    args = parser.parse_args()

    report = restore_database(Path(args.backup), Path(args.target), force=args.force)
    print(f"restored:  {report['target_path']}")
    print(f"checksum:  {report['checksum']}")
    if report["pre_restore_backup"]:
        print(f"preserved: {report['pre_restore_backup']} (whatever was there before)")


if __name__ == "__main__":
    sys.exit(main() or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/Scripts/python.exe -c "import test_backup_restore as t; t.test_backup_then_restore_matches_original(); t.test_restore_refuses_a_tampered_backup()"`
Expected: no output, no exception

- [ ] **Step 5: Commit**

```bash
git add backend/restore_database.py backend/test_backup_restore.py
git commit -m "Add checksum-verified restore with pre-restore safety copy"
```

### Task A3: retention test + `main()` wrapper for the drill, wire up VPS docs

**Files:**
- Modify: `backend/test_backup_restore.py` (add `main()`, retention test, final PASS prints)
- Modify: `docs/deployment/VPS_RUNBOOK.md` (add Backups section; update Known gaps)
- Modify: `backend/.env.example` (document the two new env vars)

**Interfaces:** none new — this wires up what A1/A2 produced.

- [ ] **Step 1: Add the retention test and `main()` to `test_backup_restore.py`**

```python
# backend/test_backup_restore.py (append)


def test_prune_old_backups_keeps_only_the_newest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        live_path = Path(tmp) / "paper_trading.db"
        backup_dir = Path(tmp) / "backups"
        make_seed_db(live_path)

        from datetime import datetime, timedelta, timezone

        base = datetime(2026, 8, 20, tzinfo=timezone.utc)
        for i in range(5):
            backup_database.backup_database(live_path, backup_dir, now=lambda i=i: base + timedelta(hours=i))

        remaining_before = sorted(backup_dir.glob("paper_trading-*.db"))
        assert len(remaining_before) == 5, remaining_before

        deleted = backup_database.prune_old_backups(backup_dir, keep=2)
        assert len(deleted) == 3, deleted

        remaining_after = sorted(backup_dir.glob("paper_trading-*.db"))
        assert len(remaining_after) == 2, remaining_after
        # base is midnight; the two newest are hour offsets 3 and 4 (03:00, 04:00)
        assert "T030000Z" in remaining_after[0].name, remaining_after
        assert "T040000Z" in remaining_after[1].name, remaining_after

        # every surviving .db must still have its manifest -- pruning must not
        # orphan a backup from its checksum record
        for db_file in remaining_after:
            manifest = db_file.with_name(db_file.stem + ".manifest.json")
            assert manifest.exists(), f"{db_file} lost its manifest"


def main() -> None:
    test_table_row_counts_and_checksum()
    test_backup_then_restore_matches_original()
    test_restore_refuses_a_tampered_backup()
    test_prune_old_backups_keeps_only_the_newest()
    print("PASS: backup_database produces a checksummed, row-counted, restorable backup.")
    print("PASS: restore_database restores byte-for-byte and refuses a tampered backup.")
    print("PASS: prune_old_backups keeps only the newest N backups and their manifests.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the full drill**

Run: `cd backend && ../.venv/Scripts/python.exe test_backup_restore.py`
Expected: the three `PASS:` lines, nothing else

- [ ] **Step 3: Add a "Backups" section to `docs/deployment/VPS_RUNBOOK.md`**

Insert a new `## Backups` section after the `## State` section (before `## Audit results,
2026-08-22`), and remove the first bullet of `## Known gaps, not yet closed`
(`No backup of paper_trading.db...`), replacing it with a line pointing at the new section:

```markdown
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

**Off-box replication is still a known gap.** This backup lives on the same droplet as the live
file, on a separate directory rather than a separate disk or host. A droplet-level failure (not just
a bad file) would lose both. Out of scope for this pre-kickoff window; worth revisiting post-submission.
```

Then in `## Known gaps, not yet closed`, replace:

```markdown
- No backup of `paper_trading.db`. Losing the droplet loses the demo trade history.
```

with:

```markdown
- Backups exist (see `## Backups`) but are not off-box — a droplet-level failure still loses both
  the live file and its backups, since they sit on the same disk.
```

- [ ] **Step 4: Document the new env vars in `backend/.env.example`**

Add near the other `PAPER_*` variables:

```
# Directory backup_database.py writes timestamped backups into. Defaults to
# backups/paper_trading_db one level above the backend/ checkout if unset.
PAPER_DB_BACKUP_DIR=

# How many of the newest backups backup_database.py keeps before pruning older
# ones. Defaults to 90 if unset.
PAPER_DB_BACKUP_RETENTION=
```

- [ ] **Step 5: Run the full 42-script suite plus the new drill, then commit**

Run each `test_*.py` from `backend/` (see CLAUDE.md's list) plus `test_backup_restore.py`.
Expected: all pass, no regressions.

```bash
git add docs/deployment/VPS_RUNBOOK.md backend/.env.example backend/test_backup_restore.py
git commit -m "Document backup/restore cron and retention in the VPS runbook"
```

- [ ] **Step 6: Report Part A to the user**

Summarize: backup script, restore script, drill proving round-trip + tampered-backup refusal +
retention pruning, VPS runbook wired up with the actual cron line to add at kickoff. Wait for
acknowledgment before starting Part B (per the user's "report back per-item" instruction) — but keep
working if none arrives promptly; this is advisory, not a hard gate.

---

## Part B — Pre-flight / cutover script for `provision_cash_account.py`

### Task B1: pure report-building logic

**Files:**
- Create: `backend/cutover_preflight.py`
- Test: `backend/test_cutover_preflight.py`

**Interfaces:**
- Consumes: `alpaca_paper_adapter.check_alpaca_status() -> dict` (already returns `status`,
  `environment`, `account_status`, `account_suffix`, `account_type`, `paper_account_ready`, etc. —
  see `alpaca_paper_adapter.py:1249`); `provision_cash_account.current_configuration(credentials) ->
  dict` (returns `{"max_margin_multiplier": ..., "no_shorting": ...}`); `backup_database.table_row_counts`
  from Part A (reused, not reimplemented) for the local ledger counts.
- Produces:
  - `KNOWN_TEST_ACCOUNT_SUFFIX = "0TCX"` — the test account CLAUDE.md documents; cutover means
    moving away from it, so seeing it here is a signal, not a hardcoded secret.
  - `evaluate_account_readiness(status: dict) -> dict` — pure function, returns
    `{"ok": bool, "checks": [{"name": str, "ok": bool, "detail": str}, ...]}` checking:
    `status["environment"] == "PAPER"`, `status["paper_account_ready"] is True`,
    `status["account_status"] == "ACTIVE"`, and `status["account_suffix"] !=
    KNOWN_TEST_ACCOUNT_SUFFIX` (this one is a warning, not a hard failure — flagged but does not flip
    `ok` to False, since re-running against the test account on purpose is legitimate).
  - `evaluate_margin_state(config: dict) -> dict` — pure function reporting (not gating — this is
    informational, `provision_cash_account.py --apply` is what actually tightens it)
    `{"max_margin_multiplier": ..., "no_shorting": ..., "already_cash_equivalent": bool}`.
  - `evaluate_ledger_state(row_counts: dict, expectation: str | None) -> dict` — pure function.
    `expectation` is `None`, `"empty"`, or `"seeded"`. Returns
    `{"ok": bool, "detail": str, "row_counts": row_counts, "decision_required": bool}`.
    - `expectation is None` → `{"ok": False, "decision_required": True, "detail": "pass
      --expect-empty-ledger or --expect-seeded-ledger; counts are ..."}` (never guesses — this is
      the literal implementation of "don't guess, ask if unclear").
    - `expectation == "empty"` → `ok` is `True` only if every ledger-relevant table
      (`paper_positions`, `paper_fills`) has count `0`; otherwise `False` with the offending table
      named.
    - `expectation == "seeded"` → `ok` is always `True` (the operator has affirmed this is
      intentional); `detail` names the counts for the record.
  - `build_preflight_report(*, status: dict, config: dict, row_counts: dict, expectation: str | None) -> dict`
    — combines the three above into `{"go": bool, "account": {...}, "margin": {...}, "ledger":
    {...}}`. `go` is `True` only if `account["ok"]` and `ledger["ok"]` are both `True` (margin state
    is informational only, per above).

- [ ] **Step 1: Write the failing tests**

```python
"""Pure-function tests for cutover_preflight's report-building logic.

Network and disk are never touched here -- check_alpaca_status() and
current_configuration()'s *shapes* are captured as plain dicts, exactly the way
test_provision_cash_account.py tests tighten() as a pure function over a
configuration dict rather than hitting the network. table_row_counts() is
reused from backup_database, already covered by test_backup_restore.py.
"""

import cutover_preflight


def test_account_readiness_passes_on_a_healthy_paper_account() -> None:
    status = {
        "environment": "PAPER",
        "paper_account_ready": True,
        "account_status": "ACTIVE",
        "account_suffix": "XY9Z",
    }
    result = cutover_preflight.evaluate_account_readiness(status)
    assert result["ok"] is True, result


def test_account_readiness_fails_closed_on_bad_status() -> None:
    for bad in (
        {"environment": "PAPER", "paper_account_ready": False, "account_status": "ACTIVE", "account_suffix": "X"},
        {"environment": "PAPER", "paper_account_ready": True, "account_status": "SUBMITTED", "account_suffix": "X"},
        {"environment": None, "paper_account_ready": True, "account_status": "ACTIVE", "account_suffix": "X"},
    ):
        result = cutover_preflight.evaluate_account_readiness(bad)
        assert result["ok"] is False, (bad, result)


def test_account_readiness_warns_but_does_not_fail_on_the_known_test_account() -> None:
    status = {
        "environment": "PAPER",
        "paper_account_ready": True,
        "account_status": "ACTIVE",
        "account_suffix": cutover_preflight.KNOWN_TEST_ACCOUNT_SUFFIX,
    }
    result = cutover_preflight.evaluate_account_readiness(status)
    assert result["ok"] is True, result
    assert any("test account" in c["detail"].lower() for c in result["checks"]), result


def test_margin_state_reports_cash_equivalent_correctly() -> None:
    tight = cutover_preflight.evaluate_margin_state({"max_margin_multiplier": "1", "no_shorting": True})
    assert tight["already_cash_equivalent"] is True, tight

    loose = cutover_preflight.evaluate_margin_state({"max_margin_multiplier": "4", "no_shorting": False})
    assert loose["already_cash_equivalent"] is False, loose


def test_ledger_state_refuses_to_guess_with_no_expectation() -> None:
    result = cutover_preflight.evaluate_ledger_state({"paper_positions": 0, "paper_fills": 0}, None)
    assert result["ok"] is False, result
    assert result["decision_required"] is True, result


def test_ledger_state_expect_empty() -> None:
    empty = cutover_preflight.evaluate_ledger_state({"paper_positions": 0, "paper_fills": 0}, "empty")
    assert empty["ok"] is True, empty

    not_empty = cutover_preflight.evaluate_ledger_state({"paper_positions": 1, "paper_fills": 0}, "empty")
    assert not_empty["ok"] is False, not_empty
    assert "paper_positions" in not_empty["detail"], not_empty


def test_ledger_state_expect_seeded_always_ok() -> None:
    result = cutover_preflight.evaluate_ledger_state({"paper_positions": 5, "paper_fills": 12}, "seeded")
    assert result["ok"] is True, result


def test_build_preflight_report_go_only_when_account_and_ledger_ok() -> None:
    status = {
        "environment": "PAPER",
        "paper_account_ready": True,
        "account_status": "ACTIVE",
        "account_suffix": "XY9Z",
    }
    config = {"max_margin_multiplier": "4", "no_shorting": False}
    row_counts = {"paper_positions": 0, "paper_fills": 0}

    go_report = cutover_preflight.build_preflight_report(
        status=status, config=config, row_counts=row_counts, expectation="empty"
    )
    assert go_report["go"] is True, go_report

    no_go_report = cutover_preflight.build_preflight_report(
        status=status, config=config, row_counts=row_counts, expectation=None
    )
    assert no_go_report["go"] is False, no_go_report


def main() -> None:
    test_account_readiness_passes_on_a_healthy_paper_account()
    test_account_readiness_fails_closed_on_bad_status()
    test_account_readiness_warns_but_does_not_fail_on_the_known_test_account()
    test_margin_state_reports_cash_equivalent_correctly()
    test_ledger_state_refuses_to_guess_with_no_expectation()
    test_ledger_state_expect_empty()
    test_ledger_state_expect_seeded_always_ok()
    test_build_preflight_report_go_only_when_account_and_ledger_ok()
    print("PASS: cutover_preflight evaluates account readiness, margin state, and ledger state as pure functions.")
    print("PASS: cutover_preflight refuses to guess the ledger's intended starting state.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/Scripts/python.exe test_cutover_preflight.py`
Expected: `ModuleNotFoundError: No module named 'cutover_preflight'`

- [ ] **Step 3: Write `backend/cutover_preflight.py`**

```python
"""Read-only go/no-go check before running provision_cash_account.py --apply
against the dedicated hackathon account at kickoff.

This never patches the account and never writes to backend/.env or the local
database -- it only reads GET /v2/account, GET /v2/account/configurations (the
same two calls provision_cash_account.py already makes), and the local
paper_trading.db's row counts. The decision to actually run
`provision_cash_account.py --no-shorting --apply` afterwards is still a human
action; this script exists so that decision is made from a clear report instead
of from memory under time pressure.

The ledger question deliberately has three answers, not two: "empty",
"seeded", or "I don't know yet". This script refuses the third one rather than
guessing -- pass --expect-empty-ledger or --expect-seeded-ledger explicitly.
"""

import sys
from pathlib import Path

from alpaca_paper_adapter import alpaca_credentials, check_alpaca_status
from backup_database import DB_PATH, table_row_counts
from config import load_settings
from provision_cash_account import current_configuration

# The test account CLAUDE.md documents. Cutover means moving away from it, so
# seeing this suffix during a preflight is a signal worth surfacing loudly --
# not a secret, and not a hard failure, since re-running this against the test
# account on purpose (e.g. to sanity-check the script itself) is legitimate.
KNOWN_TEST_ACCOUNT_SUFFIX = "0TCX"

LEDGER_TABLES = ("paper_positions", "paper_fills")


def evaluate_account_readiness(status: dict) -> dict:
    checks = [
        {
            "name": "environment_is_paper",
            "ok": status.get("environment") == "PAPER",
            "detail": f"environment={status.get('environment')!r}",
        },
        {
            "name": "paper_account_ready",
            "ok": status.get("paper_account_ready") is True,
            "detail": f"paper_account_ready={status.get('paper_account_ready')!r}",
        },
        {
            "name": "account_active",
            "ok": status.get("account_status") == "ACTIVE",
            "detail": f"account_status={status.get('account_status')!r}",
        },
        {
            "name": "not_the_known_test_account",
            "ok": status.get("account_suffix") != KNOWN_TEST_ACCOUNT_SUFFIX,
            "detail": (
                f"account_suffix={status.get('account_suffix')!r} matches the documented test "
                f"account ({KNOWN_TEST_ACCOUNT_SUFFIX}) -- confirm this is intentional"
                if status.get("account_suffix") == KNOWN_TEST_ACCOUNT_SUFFIX
                else f"account_suffix={status.get('account_suffix')!r}"
            ),
        },
    ]
    # The test-account check is informational, not a hard gate -- everything
    # else must pass for "ok".
    hard_checks = [c for c in checks if c["name"] != "not_the_known_test_account"]
    return {"ok": all(c["ok"] for c in hard_checks), "checks": checks}


def evaluate_margin_state(config: dict) -> dict:
    try:
        multiplier = float(config.get("max_margin_multiplier"))
    except (TypeError, ValueError):
        multiplier = None
    no_shorting = bool(config.get("no_shorting"))
    return {
        "max_margin_multiplier": config.get("max_margin_multiplier"),
        "no_shorting": no_shorting,
        "already_cash_equivalent": multiplier is not None and multiplier <= 1 and no_shorting,
    }


def evaluate_ledger_state(row_counts: dict, expectation: str | None) -> dict:
    relevant = {table: row_counts.get(table, 0) for table in LEDGER_TABLES}
    if expectation is None:
        return {
            "ok": False,
            "decision_required": True,
            "row_counts": relevant,
            "detail": (
                "the local ledger's intended starting state was not specified; pass "
                "--expect-empty-ledger or --expect-seeded-ledger. Current counts: "
                f"{relevant}"
            ),
        }
    if expectation == "empty":
        nonzero = {table: count for table, count in relevant.items() if count}
        return {
            "ok": not nonzero,
            "decision_required": False,
            "row_counts": relevant,
            "detail": (
                "ledger is empty, as expected"
                if not nonzero
                else f"expected an empty ledger but found rows in: {nonzero}"
            ),
        }
    if expectation == "seeded":
        return {
            "ok": True,
            "decision_required": False,
            "row_counts": relevant,
            "detail": f"ledger is intentionally seeded: {relevant}",
        }
    raise SystemExit(f"unknown ledger expectation: {expectation!r}")


def build_preflight_report(*, status: dict, config: dict, row_counts: dict, expectation: str | None) -> dict:
    account = evaluate_account_readiness(status)
    margin = evaluate_margin_state(config)
    ledger = evaluate_ledger_state(row_counts, expectation)
    return {
        "go": account["ok"] and ledger["ok"],
        "account": account,
        "margin": margin,
        "ledger": ledger,
    }


def _local_row_counts(db_path: Path) -> dict:
    import sqlite3

    if not db_path.exists():
        return {table: 0 for table in LEDGER_TABLES}
    connection = sqlite3.connect(db_path)
    try:
        return table_row_counts(connection)
    finally:
        connection.close()


def main() -> None:
    settings = load_settings()
    if settings.alpaca_mode != "paper":
        raise SystemExit("refusing to run outside paper mode")

    expectation = None
    if "--expect-empty-ledger" in sys.argv:
        expectation = "empty"
    elif "--expect-seeded-ledger" in sys.argv:
        expectation = "seeded"

    credentials = alpaca_credentials()
    if credentials is None:
        raise SystemExit("ALPACA_API_KEY_ID / ALPACA_SECRET_KEY are not configured")

    status = check_alpaca_status()
    config = current_configuration(credentials)
    row_counts = _local_row_counts(DB_PATH)

    report = build_preflight_report(
        status=status, config=config, row_counts=row_counts, expectation=expectation
    )

    print(f"local ledger:  {DB_PATH}")
    print()
    print("account:")
    for check in report["account"]["checks"]:
        mark = "OK  " if check["ok"] else "WARN" if check["name"] == "not_the_known_test_account" else "FAIL"
        print(f"  [{mark}] {check['name']}: {check['detail']}")
    print()
    print("margin/multiplier (informational -- provision_cash_account.py --apply tightens this):")
    print(f"  max_margin_multiplier={report['margin']['max_margin_multiplier']} "
          f"no_shorting={report['margin']['no_shorting']} "
          f"already_cash_equivalent={report['margin']['already_cash_equivalent']}")
    print()
    print("ledger:")
    print(f"  {report['ledger']['detail']}")
    print()
    print("=" * 60)
    print(f"GO" if report["go"] else "NO-GO", "-- proceed with provision_cash_account.py --apply" if report["go"] else "-- resolve the above first")

    if not report["go"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/Scripts/python.exe test_cutover_preflight.py`
Expected: the two `PASS:` lines

- [ ] **Step 5: Manual smoke test against the real (test) account, read-only**

Run: `cd backend && ../.venv/Scripts/python.exe cutover_preflight.py --expect-seeded-ledger`
Expected: a report against the real `0TCX` test account — confirms the `not_the_known_test_account`
check fires as a `WARN`, not a `FAIL`, and that the script makes no writes (re-run
`check_alpaca_status` manually afterwards, or just note that `cutover_preflight.py` never calls
`alpaca_request` with a non-`GET` method — grep it to confirm).

- [ ] **Step 6: Commit**

```bash
git add backend/cutover_preflight.py backend/test_cutover_preflight.py
git commit -m "Add read-only cutover pre-flight check ahead of the hackathon account swap"
```

- [ ] **Step 7: Report Part B to the user**

Summarize: what it checks, that it's read-only, the two explicit ledger flags, and the exact
kickoff-day command sequence (`cutover_preflight.py --expect-empty-ledger` then, only if GO,
`provision_cash_account.py --no-shorting --apply`).

---

## Part C — Audit-export CLI

### Task C1: gather ledger + Shariah verdicts + EDGAR evidence, render Markdown

**Files:**
- Create: `backend/audit_export.py`
- Test: `backend/test_audit_export.py`

**Interfaces:**
- Consumes: `shariah_screen_store.list_shariah_screens(connection, *, symbol=None, limit=100) ->
  list[dict]` (each dict has `payload` already `json.loads`-ed, containing `company`, `sic`,
  `sic_description`, `screen`, `provider`, `report_date`, `reason`, and — when `screen ==
  "financial_ratios"` — `ratios: {total_assets, interest_bearing_debt, conventional_cash,
  debt_ratio_pct, cash_ratio_pct, limit_pct, debt_concepts_used, cash_concepts_used, form}`; see
  `sec_edgar_screen.py:278-290` and `sec_edgar_screen.py:414-425`).
- Produces:
  - `gather_ledger(connection: sqlite3.Connection) -> dict` — returns
    `{"positions": list[dict], "fills": list[dict]}`, each row from `paper_positions` /
    `paper_fills` as a plain dict (via `connection.row_factory = sqlite3.Row`), ordered by `id`.
  - `distinct_symbols(ledger: dict) -> list[str]` — every symbol appearing in either table, sorted,
    deduplicated. The OCC option symbols already recorded in `paper_fills` for option trades
    (`AAPL260828P00305000` etc., per limitation 2 in CLAUDE.md) are included as-is — no attempt to
    parse the underlying out of them here; a scholar reviewing the export sees the exact symbol the
    broker recorded.
  - `gather_shariah_evidence(connection: sqlite3.Connection, symbols: list[str]) -> list[dict]` —
    for each symbol, the **latest** recorded screen (via
    `shariah_screen_store.latest_shariah_screen`), or `{"symbol": symbol, "status": "NO_SCREEN_ON_RECORD"}`
    if none exists (e.g. an OCC option symbol was never itself screened — the underlying was).
  - `render_markdown(*, generated_at: str, db_path: str, ledger: dict, evidence: list[dict]) -> str`
    — the full document. Includes a fixed limitations paragraph (verbatim from CLAUDE.md's Known
    limitations §1) so a judge/scholar reading the export sees the same caveats as the engineering
    notes, not a cleaned-up version.
  - `DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "live-trade-evidence" / "audit-exports"`

- [ ] **Step 1: Write the failing tests**

```python
"""Prove the audit export actually surfaces the evidence, not just that it runs.

Everything here is against a scratch SQLite file seeded with sample rows --
never the real paper_trading.db. The assertions check for the presence of
specific numbers (a debt ratio, a row count) in the rendered Markdown, because
a judge reading the export needs those numbers verbatim, not a summary that
dropped them.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import audit_export


def seed_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE paper_positions (id INTEGER PRIMARY KEY, symbol TEXT, account_suffix TEXT, "
            "quantity REAL, average_cost REAL, cost_basis REAL, realized_pnl REAL, updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO paper_positions VALUES (1, 'CVX', '0TCX', 1.0, 206.89, 206.89, 0.0, '2026-08-19T00:00:00+00:00')"
        )
        connection.execute(
            "CREATE TABLE paper_fills (id INTEGER PRIMARY KEY, queue_id INTEGER, broker_order_id TEXT, "
            "symbol TEXT, side TEXT, quantity REAL, avg_price REAL, notional REAL, filled_at TEXT, "
            "account_suffix TEXT, account_type TEXT, payload TEXT)"
        )
        connection.execute(
            "INSERT INTO paper_fills VALUES (1, 11, 'order-1', 'AAPL260828P00305000', 'SELL', 1.0, 1.02, 102.0, "
            "'2026-08-20T00:00:00+00:00', '0TCX', 'CASH', '{}')"
        )
        connection.execute(
            """
            CREATE TABLE shariah_screens (
                id INTEGER PRIMARY KEY AUTOINCREMENT, screened_at TEXT NOT NULL, symbol TEXT NOT NULL,
                status TEXT NOT NULL, screen TEXT, report_date TEXT, debt_ratio_pct REAL,
                cash_ratio_pct REAL, limit_pct REAL, provider TEXT, reason TEXT, previous_status TEXT,
                payload TEXT NOT NULL
            )
            """
        )
        verdict = {
            "symbol": "CVX",
            "status": "COMPLIANT",
            "screen": "financial_ratios",
            "provider": "SEC_EDGAR",
            "company": "Chevron Corp",
            "sic": "2911",
            "sic_description": "Petroleum Refining",
            "report_date": "2025-12-31",
            "reason": "debt 12.3% and cash 4.1% of total assets, both under 33%",
            "ratios": {
                "total_assets": 250000000000.0,
                "interest_bearing_debt": 30750000000.0,
                "conventional_cash": 10250000000.0,
                "debt_ratio_pct": 12.3,
                "cash_ratio_pct": 4.1,
                "limit_pct": 33.0,
                "debt_concepts_used": ["LongTermDebt"],
                "cash_concepts_used": ["CashAndCashEquivalentsAtCarryingValue"],
                "form": "10-K",
            },
        }
        connection.execute(
            "INSERT INTO shariah_screens (screened_at, symbol, status, screen, report_date, "
            "debt_ratio_pct, cash_ratio_pct, limit_pct, provider, reason, previous_status, payload) "
            "VALUES ('2026-08-19T00:00:00+00:00', 'CVX', 'COMPLIANT', 'financial_ratios', '2025-12-31', "
            "12.3, 4.1, 33.0, 'SEC_EDGAR', ?, NULL, ?)",
            (verdict["reason"], json.dumps(verdict)),
        )
        connection.commit()
    finally:
        connection.close()


def test_gather_ledger_and_distinct_symbols() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        seed_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            ledger = audit_export.gather_ledger(connection)
            symbols = audit_export.distinct_symbols(ledger)
        finally:
            connection.close()

        assert len(ledger["positions"]) == 1, ledger
        assert ledger["positions"][0]["symbol"] == "CVX", ledger
        assert len(ledger["fills"]) == 1, ledger
        assert symbols == ["AAPL260828P00305000", "CVX"], symbols


def test_gather_shariah_evidence_includes_edgar_ratios() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        seed_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            evidence = audit_export.gather_shariah_evidence(
                connection, ["CVX", "AAPL260828P00305000"]
            )
        finally:
            connection.close()

        by_symbol = {row["symbol"]: row for row in evidence}
        assert by_symbol["CVX"]["status"] == "COMPLIANT", by_symbol
        assert by_symbol["CVX"]["payload"]["ratios"]["debt_ratio_pct"] == 12.3, by_symbol
        assert by_symbol["AAPL260828P00305000"]["status"] == "NO_SCREEN_ON_RECORD", by_symbol


def test_render_markdown_contains_the_actual_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "paper_trading.db"
        seed_db(db_path)
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
        try:
            ledger = audit_export.gather_ledger(connection)
            symbols = audit_export.distinct_symbols(ledger)
            evidence = audit_export.gather_shariah_evidence(connection, symbols)
        finally:
            connection.close()

        markdown = audit_export.render_markdown(
            generated_at="2026-08-26T12:00:00+00:00",
            db_path=str(db_path),
            ledger=ledger,
            evidence=evidence,
        )

        # the numbers a scholar needs must appear verbatim, not just a status
        assert "CVX" in markdown
        assert "COMPLIANT" in markdown
        assert "12.3" in markdown  # debt_ratio_pct
        assert "4.1" in markdown  # cash_ratio_pct
        assert "Chevron Corp" in markdown
        assert "10-K" in markdown  # the filing form the ratio was computed from
        assert "AAPL260828P00305000" in markdown
        assert "NO_SCREEN_ON_RECORD" in markdown
        # the known-limitations caveat must travel with the evidence, not be left implicit
        assert "SIC code" in markdown
        assert "XBRL" in markdown


def main() -> None:
    test_gather_ledger_and_distinct_symbols()
    test_gather_shariah_evidence_includes_edgar_ratios()
    test_render_markdown_contains_the_actual_evidence()
    print("PASS: audit_export gathers the ledger, the latest verdict per symbol, and the EDGAR ratio evidence.")
    print("PASS: audit_export's rendered Markdown carries the actual numbers and the documented caveats.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ../.venv/Scripts/python.exe test_audit_export.py`
Expected: `ModuleNotFoundError: No module named 'audit_export'`

- [ ] **Step 3: Write `backend/audit_export.py`**

```python
"""Bundle the ledger, Shariah verdicts, and their EDGAR evidence into one
timestamped Markdown document -- the artifact a judge or scholar would
actually review, not a green checkmark.

Reads three things, all already-persisted and read-only:

1. paper_positions / paper_fills -- the local ledger (portfolio_store.py).
2. shariah_screens -- the append-only verdict log (shariah_screen_store.py),
   read via list_shariah_screens/latest_shariah_screen, never via
   sec_edgar_screen or sec_edgar_cache directly. This module does not import
   either -- test_single_screening_path.py's AST check would fail it if it did,
   and there is no need to: the verdict's persisted payload already carries
   everything the screen computed (company, SIC, and -- for a COMPLIANT/
   NON_COMPLIANT ratio verdict -- total_assets, interest_bearing_debt,
   conventional_cash, and which XBRL concepts were summed to get there). That
   payload *is* the EDGAR evidence a scholar would want to check; there is no
   separate "snapshot" this script needs to go dig out of sec_edgar_cache.py's
   hashed, TTL-expiring, verdict-free response cache.
3. The Known limitations this repo documents about that screen (SIC-code
   business-activity approximation, XBRL's inability to separate Islamic from
   conventional instruments) -- reproduced here verbatim so the caveat travels
   with the evidence instead of living only in CLAUDE.md.

Purely a reporting tool: nothing here decides, screens, or executes anything.
"""

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import BACKEND_DIR, REPO_ROOT
from shariah_screen_store import latest_shariah_screen

DB_PATH = BACKEND_DIR / "paper_trading.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "live-trade-evidence" / "audit-exports"

LIMITATIONS_NOTE = (
    "**Screening methodology and its limits (reproduced from CLAUDE.md's Known "
    "limitations, verbatim):** business activity is approximated by SIC code, and "
    "XBRL cannot separate Islamic from conventional instruments, so both the debt "
    "ratio and the cash ratio are overstated. Both approximations err toward "
    "rejection -- a company can be wrongly flagged NON_COMPLIANT by this "
    "conservatism, but not wrongly cleared by it."
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def gather_ledger(connection: sqlite3.Connection) -> dict:
    connection.row_factory = sqlite3.Row
    positions = [
        dict(row) for row in connection.execute("SELECT * FROM paper_positions ORDER BY id").fetchall()
    ]
    fills = [dict(row) for row in connection.execute("SELECT * FROM paper_fills ORDER BY id").fetchall()]
    return {"positions": positions, "fills": fills}


def distinct_symbols(ledger: dict) -> list[str]:
    symbols = {row["symbol"] for row in ledger["positions"]} | {
        row["symbol"] for row in ledger["fills"]
    }
    return sorted(symbols)


def gather_shariah_evidence(connection: sqlite3.Connection, symbols: list[str]) -> list[dict]:
    evidence = []
    for symbol in symbols:
        screen = latest_shariah_screen(connection, symbol)
        if screen is None:
            evidence.append({"symbol": symbol, "status": "NO_SCREEN_ON_RECORD"})
            continue
        payload = screen.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (TypeError, json.JSONDecodeError):
                payload = {}
        evidence.append(
            {
                "symbol": symbol,
                "status": screen.get("status"),
                "screened_at": screen.get("screened_at"),
                "screen": screen.get("screen"),
                "provider": screen.get("provider"),
                "reason": screen.get("reason"),
                "payload": payload if isinstance(payload, dict) else {},
            }
        )
    return evidence


def _render_ledger_section(ledger: dict) -> str:
    lines = ["## Ledger", "", "### Positions (`paper_positions`)", ""]
    if not ledger["positions"]:
        lines.append("_No open positions recorded._")
    else:
        lines.append("| symbol | account | quantity | avg cost | cost basis | realized P&L | updated |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in ledger["positions"]:
            lines.append(
                f"| {row.get('symbol')} | {row.get('account_suffix')} | {row.get('quantity')} | "
                f"{row.get('average_cost')} | {row.get('cost_basis')} | {row.get('realized_pnl')} | "
                f"{row.get('updated_at')} |"
            )
    lines += ["", "### Fills (`paper_fills`)", ""]
    if not ledger["fills"]:
        lines.append("_No fills recorded._")
    else:
        lines.append("| symbol | side | quantity | avg price | notional | filled at | broker order id |")
        lines.append("|---|---|---|---|---|---|---|")
        for row in ledger["fills"]:
            lines.append(
                f"| {row.get('symbol')} | {row.get('side')} | {row.get('quantity')} | "
                f"{row.get('avg_price')} | {row.get('notional')} | {row.get('filled_at')} | "
                f"{row.get('broker_order_id')} |"
            )
    return "\n".join(lines)


def _render_evidence_section(evidence: list[dict]) -> str:
    lines = ["## Shariah Compliance Verdicts and EDGAR Evidence", ""]
    for row in evidence:
        lines.append(f"### {row['symbol']}")
        lines.append("")
        if row["status"] == "NO_SCREEN_ON_RECORD":
            lines.append(
                "_No Shariah screen is on record for this exact symbol._ "
                "(Expected for an OCC option symbol -- the underlying equity is what gets screened.)"
            )
            lines.append("")
            continue
        payload = row["payload"]
        lines.append(f"- **Status:** {row['status']}")
        lines.append(f"- **Screened at:** {row['screened_at']}")
        lines.append(f"- **Screen type:** {row['screen']}")
        lines.append(f"- **Provider:** {row['provider']}")
        lines.append(f"- **Company:** {payload.get('company', '—')}")
        lines.append(
            f"- **SIC:** {payload.get('sic', '—')} ({payload.get('sic_description', '—')})"
        )
        lines.append(f"- **Reason:** {row['reason']}")
        ratios = payload.get("ratios")
        if isinstance(ratios, dict):
            lines.append("- **EDGAR ratio evidence (from the filing named below):**")
            lines.append(f"  - Filing form: {ratios.get('form', '—')}, report date: {payload.get('report_date', '—')}")
            lines.append(f"  - Total assets: {ratios.get('total_assets', '—')}")
            lines.append(
                f"  - Interest-bearing debt: {ratios.get('interest_bearing_debt', '—')} "
                f"({ratios.get('debt_ratio_pct', '—')}% of total assets, limit {ratios.get('limit_pct', '—')}%)"
            )
            lines.append(
                f"  - Conventional cash: {ratios.get('conventional_cash', '—')} "
                f"({ratios.get('cash_ratio_pct', '—')}% of total assets)"
            )
            lines.append(f"  - Debt XBRL concepts summed: {ratios.get('debt_concepts_used', [])}")
            lines.append(f"  - Cash XBRL concepts summed: {ratios.get('cash_concepts_used', [])}")
        lines.append("")
    return "\n".join(lines)


def render_markdown(*, generated_at: str, db_path: str, ledger: dict, evidence: list[dict]) -> str:
    parts = [
        "# Amanah Trader Audit Export",
        "",
        f"Generated: {generated_at}",
        f"Source database: `{db_path}`",
        "",
        LIMITATIONS_NOTE,
        "",
        _render_ledger_section(ledger),
        "",
        _render_evidence_section(evidence),
    ]
    return "\n".join(parts) + "\n"


def export_audit(db_path: Path, output_dir: Path, *, now_iso: str | None = None) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        raise SystemExit(f"database does not exist: {db_path}")

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        ledger = gather_ledger(connection)
        symbols = distinct_symbols(ledger)
        evidence = gather_shariah_evidence(connection, symbols)
    finally:
        connection.close()

    generated_at = now_iso or utc_now_iso()
    markdown = render_markdown(
        generated_at=generated_at, db_path=str(db_path), ledger=ledger, evidence=evidence
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_timestamp = generated_at.replace(":", "").replace("+00:00", "Z")
    out_path = output_dir / f"audit-export-{safe_timestamp}.md"
    out_path.write_text(markdown, encoding="utf-8")

    return {"status": "OK", "output_path": str(out_path), "symbol_count": len(symbols)}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH), help="path to paper_trading.db")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR), help="directory to write the export into")
    args = parser.parse_args()

    report = export_audit(Path(args.db), Path(args.out_dir))
    print(f"exported: {report['output_path']}")
    print(f"symbols:  {report['symbol_count']}")


if __name__ == "__main__":
    sys.exit(main() or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ../.venv/Scripts/python.exe test_audit_export.py`
Expected: the two `PASS:` lines

- [ ] **Step 5: Commit**

```bash
git add backend/audit_export.py backend/test_audit_export.py
git commit -m "Add audit-export CLI bundling the ledger, verdicts, and EDGAR ratio evidence"
```

- [ ] **Step 6: Report Part C to the user**

Summarize: what it bundles, where it writes (`docs/live-trade-evidence/audit-exports/`, so it can be
committed as reviewable evidence like the existing JSON files in that directory), and that it never
touches `sec_edgar_screen.py` / `sec_edgar_cache.py` — it only reads the already-persisted verdict.

---

## Final Verification

- [ ] **Step 1: Run the full 42-script existing suite plus the three new test files**

Run every script in CLAUDE.md's Tests list, plus `test_backup_restore.py`,
`test_cutover_preflight.py`, `test_audit_export.py`, from `backend/`.
Expected: all pass, no regressions from the pre-work baseline run.

- [ ] **Step 2: Ruff format + lint check on everything touched**

Run: `.venv\Scripts\ruff.exe check backend/backup_database.py backend/restore_database.py
backend/cutover_preflight.py backend/audit_export.py backend/test_backup_restore.py
backend/test_cutover_preflight.py backend/test_audit_export.py`
Run: `.venv\Scripts\ruff.exe format --check backend/backup_database.py backend/restore_database.py
backend/cutover_preflight.py backend/audit_export.py backend/test_backup_restore.py
backend/test_cutover_preflight.py backend/test_audit_export.py`
Expected: no findings (the `PostToolUse` hook should already have handled this per-file as each was
written).

- [ ] **Step 3: Confirm no secrets in anything staged**

Run: `git diff --stat` and `git status`, then eyeball the diff for `.env` values, API keys, or the
nginx operator key. None of the new scripts read credential values into printed output, but confirm
before handing back.

- [ ] **Step 4: Final report to the user**

One message summarizing all three parts done, the exact commands to run at kickoff (preflight →
provision → backup verification), and the two known/deliberate gaps (off-box backup replication;
this plan does not add authentication to the deployed API, which was a separate item in the VPS
runbook's gap list, not part of this task).
