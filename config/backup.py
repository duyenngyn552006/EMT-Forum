"""Backup dinh ky toan he thong - nen db.sqlite3 + media/ + private_media/ thanh 1 file
.zip trong thu muc backups/, tu dong xoa bot ban cu khi vuot so luong gioi han.

Chay ngam qua Celery Beat (django_celery_beat.DatabaseScheduler da cau hinh san trong
settings.py) - lich chay dang ky truc tiep trong Django Admin > Periodic Tasks, KHONG
phai nguoi dung tu bam nut.

Khong doc settings.* o module-level: module nay duoc import tu config/celery.py, tuc la
tu ben trong config/__init__.py, TRUOC KHI config/settings.py chay xong lan dau - doc
settings ngay luc import de dam bao khong vuong vong lap nay.
"""
import os
import zipfile
from datetime import datetime

from celery import shared_task

BACKUP_KEEP_COUNT = 7


@shared_task
def backup_database():
    """Tao 1 file .zip moi gom db.sqlite3 + media/ + private_media/, roi don bot ban cu."""
    from django.conf import settings

    backup_dir = settings.BASE_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = backup_dir / f"backup_{timestamp}.zip"

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        db_path = settings.DATABASES["default"]["NAME"]
        if os.path.exists(db_path):
            zf.write(db_path, arcname="db.sqlite3")

        for root_dir, arc_prefix in (
            (settings.MEDIA_ROOT, "media"),
            (settings.PRIVATE_MEDIA_ROOT, "private_media"),
        ):
            root_dir = str(root_dir)
            if not os.path.isdir(root_dir):
                continue
            for dirpath, _dirnames, filenames in os.walk(root_dir):
                for filename in filenames:
                    file_path = os.path.join(dirpath, filename)
                    arcname = os.path.join(arc_prefix, os.path.relpath(file_path, root_dir))
                    zf.write(file_path, arcname=arcname)

    _cleanup_old_backups(backup_dir)
    return str(archive_path)


def _cleanup_old_backups(backup_dir):
    backups = sorted(f for f in os.listdir(backup_dir) if f.startswith("backup_") and f.endswith(".zip"))
    while len(backups) > BACKUP_KEEP_COUNT:
        os.remove(backup_dir / backups.pop(0))
