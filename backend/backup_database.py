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
import os
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
        manifest = path.with_name(path.stem + ".manifest.json")
        path.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        deleted.append(path.name)
    return deleted


def main() -> None:
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
