import os
import tempfile
import zipfile
from pathlib import Path

from django.test import TestCase, override_settings

from .backup import BACKUP_KEEP_COUNT, _cleanup_old_backups, backup_database


class BackupTaskTests(TestCase):
    """Task backup dinh ky qua Celery Beat."""

    def test_backup_creates_zip_containing_database(self):
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as tmp_db:
            tmp_db.write(b"fake db content")
            tmp_db_path = tmp_db.name
        try:
            with override_settings(
                DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": tmp_db_path}},
            ):
                archive_path = backup_database()
            try:
                self.assertTrue(os.path.exists(archive_path))
                with zipfile.ZipFile(archive_path) as zf:
                    self.assertIn("db.sqlite3", zf.namelist())
                    self.assertIsNone(zf.testzip())
            finally:
                os.remove(archive_path)
        finally:
            os.remove(tmp_db_path)

    def test_cleanup_keeps_only_recent_n_backups(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_dir = Path(tmp_dir)
            for i in range(BACKUP_KEEP_COUNT + 3):
                (backup_dir / f"backup_2026010{i:02d}_000000.zip").write_bytes(b"x")
            _cleanup_old_backups(backup_dir)
            remaining = sorted(backup_dir.glob("backup_*.zip"))
            self.assertEqual(len(remaining), BACKUP_KEEP_COUNT)
            # Phai giu lai dung cac ban MOI NHAT (xoa ban cu truoc)
            self.assertNotIn("backup_20260100_000000.zip", [p.name for p in remaining])
