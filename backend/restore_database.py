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

import json
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
