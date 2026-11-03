import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("khoa_forum")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# config/ khong phai 1 Django app (khong nam trong INSTALLED_APPS) nen autodiscover_tasks()
# khong tim thay task backup dinh ky o day - phai import truc tiep de Celery dang ky.
import config.backup  # noqa: E402,F401
