"""Prove the backup/restore path actually works, not just that it runs.

A backup nobody has restored from is not a backup. This drill seeds a scratch
SQLite file with known rows, backs it up, destroys the scratch "live" copy the
way a disk failure or a bad migration would, restores it, and checks the
restored file is byte-identical to the backup and has the same row counts as
the original. It also proves a tampered backup is refused rather than silently
restored. Nothing here touches the real backend/paper_trading.db -- every path
is inside a tempfile.TemporaryDirectory().
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
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

        import hashlib

        checksum = backup_database.sha256_of_file(db_path)
        assert checksum == hashlib.sha256(db_path.read_bytes()).hexdigest(), checksum


def test_backup_then_restore_matches_original() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        live_path = Path(tmp) / "paper_trading.db"
        backup_dir = Path(tmp) / "backups"
        make_seed_db(live_path)

        report = backup_database.backup_database(live_path, backup_dir)

        # ---- simulate disaster: the "live" file is corrupted beyond use ----
        live_path.write_bytes(b"not a sqlite file at all")

        restore_report = restore_database.restore_database(Path(report["backup_path"]), live_path)
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


def test_prune_old_backups_keeps_only_the_newest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        live_path = Path(tmp) / "paper_trading.db"
        backup_dir = Path(tmp) / "backups"
        make_seed_db(live_path)

        base = datetime(2026, 8, 20, tzinfo=timezone.utc)
        for i in range(5):
            backup_database.backup_database(
                live_path, backup_dir, now=lambda i=i: base + timedelta(hours=i)
            )

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
