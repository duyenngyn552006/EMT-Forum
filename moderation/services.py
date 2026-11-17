"""Logic nghiep vu kiem duyet dung chung: loc tu khoa nhay cam + ghi nhat ky.

Cac app forum/announcements/groups/interactions goi ham o day khi tao/sua
noi dung de bao dam luon di qua buoc tien kiem duyet, khong duplicate logic.
"""
from django.contrib.contenttypes.models import ContentType

from .models import ModerationLog, SensitiveKeyword


def find_sensitive_keyword(*texts):
    """Quet cac doan text qua danh sach tu khoa cam dang bat.

    Tra ve SensitiveKeyword dau tien khop duoc, hoac None neu sach.
    """
    active_keywords = list(SensitiveKeyword.objects.filter(is_active=True))
    if not active_keywords:
        return None
    combined = " ".join(t or "" for t in texts).lower()
    for kw in active_keywords:
        if kw.keyword.lower() in combined:
            return kw
    return None


def log_action(actor, action, reason, target_user=None, content_object=None):
    content_type = None
    object_id = None
    if content_object is not None:
        content_type = ContentType.objects.get_for_model(content_object)
        object_id = content_object.pk
    return ModerationLog.objects.create(
        actor=actor,
        target_user=target_user,
        content_type=content_type,
        object_id=object_id,
        action=action,
        reason=reason,
    )


def pending_counts():
    """So viec dang cho Giao vu xu ly - dung cho thanh dieu huong Trung tam kiem duyet
    va khoi "Can xu ly ngay" tren Dashboard. Import trong ham de tranh vong import
    (forum/announcements/interactions deu import moderation.services)."""
    from announcements.models import Announcement
    from config.mixins import ContentStatus
    from forum.models import ForumPost
    from interactions.models import Comment

    from .models import AccountAppeal, Report

    content = (
        ForumPost.objects.filter(status=ContentStatus.PENDING_REVIEW).count()
        + Announcement.objects.filter(status=ContentStatus.PENDING_REVIEW).count()
        + Comment.objects.filter(status=Comment.Status.PENDING_REVIEW).count()
    )
    return {
        "reports": Report.objects.filter(status__in=Report.OPEN_STATUSES).count(),
        "content": content,
        "appeals": AccountAppeal.objects.filter(status=AccountAppeal.Status.PENDING).count(),
    }


def hidden_count():
    """So noi dung dang bi an tam thoi (cho Giao vu xac minh de khoi phuc hoac giu nguyen)."""
    from django.apps import apps

    from config.mixins import ContentStatus
    from interactions.utils import ALLOWED_MODELS

    return sum(
        apps.get_model(app_label, model_name).objects.filter(status=ContentStatus.HIDDEN).count()
        for app_label, model_name in ALLOWED_MODELS.values()
    )
