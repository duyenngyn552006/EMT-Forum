from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from config.mixins import ContentStatus, PinLimitExceeded
from config.sanitize import sanitize_html
from moderation.services import find_sensitive_keyword, log_action
from notifications.models import Notification
from notifications.services import notify
from notifications.tasks import broadcast_notification

from .models import Announcement, AnnouncementEditHistory, EventRegistration


def pin_announcement(announcement: Announcement, actor):
    """Ghim trong pham vi cung `kind` (chinh thuc / su kien) - khong tu unpin bai cu."""
    if announcement.is_pinned:
        return announcement
    max_pinned = settings.MAX_PINNED_PER_CATEGORY
    current_pinned = Announcement.objects.filter(kind=announcement.kind, is_pinned=True).count()
    if current_pinned >= max_pinned:
        raise PinLimitExceeded(max_pinned)
    announcement.is_pinned = True
    announcement.pinned_at = timezone.now()
    announcement.pinned_by = actor
    announcement.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
    return announcement


def unpin_announcement(announcement: Announcement, actor):
    announcement.is_pinned = False
    announcement.pinned_at = None
    announcement.pinned_by = None
    announcement.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
    return announcement


def create_announcement(
    author, kind, title, body, event_datetime=None, featured_image=None,
    event_end_datetime=None, deadline_at=None,
):
    body = sanitize_html(body)
    keyword = find_sensitive_keyword(title, body)
    status = ContentStatus.PENDING_REVIEW if keyword else ContentStatus.PUBLISHED
    announcement = Announcement.objects.create(
        author=author, kind=kind, title=title, body=body, status=status, event_datetime=event_datetime,
        event_end_datetime=event_end_datetime,
        deadline_at=deadline_at,
        featured_image=featured_image,
    )
    if keyword:
        log_action(
            actor=None, action="warn",
            reason=f"Tự động chuyển chờ kiểm duyệt do chứa từ khóa nhạy cảm: '{keyword.keyword}'",
            content_object=announcement,
        )
    elif status == ContentStatus.PUBLISHED:
        from accounts.models import User

        recipient_ids = list(User.objects.filter(is_active=True).values_list("id", flat=True))
        broadcast_notification.delay(
            recipient_ids=recipient_ids, verb=Notification.Verb.NEW_ANNOUNCEMENT,
            message=f"Thông báo mới: {title}", actor_id=author.id,
        )
    return announcement, keyword


def update_announcement(
    announcement: Announcement, editor, title, body, event_datetime=None,
    featured_image=None, event_end_datetime=None, deadline_at=None,
):
    AnnouncementEditHistory.objects.create(
        announcement=announcement, editor=editor, title=announcement.title, body=announcement.body
    )
    body = sanitize_html(body)
    keyword = find_sensitive_keyword(title, body)
    if announcement.deadline_at != deadline_at:
        # Han chot thay doi -> cho phep he thong tinh lai cac moc nhac theo han moi.
        from notifications.models import DeadlineReminderLog

        DeadlineReminderLog.objects.filter(announcement=announcement).delete()

    announcement.title = title
    announcement.body = body
    announcement.event_datetime = event_datetime
    announcement.event_end_datetime = event_end_datetime
    announcement.deadline_at = deadline_at
    if featured_image is False:
        announcement.featured_image = None
    elif featured_image is not None:
        announcement.featured_image = featured_image
    announcement.edit_count += 1
    if keyword:
        announcement.status = ContentStatus.PENDING_REVIEW
    announcement.save()
    if keyword:
        log_action(
            actor=None, action="warn",
            reason=f"Tự động chuyển chờ kiểm duyệt do chứa từ khóa nhạy cảm: '{keyword.keyword}'",
            content_object=announcement,
        )
    return announcement, keyword


def notify_admins_event_registration(registration: EventRegistration):
    """Gui thong bao cho nhom Quan tri vien (Giao vu Khoa, BCH Khoa, Giang vien, Superuser)
    khi co sinh vien dang ky tham gia su kien."""
    from accounts.models import User

    announcement = registration.announcement
    recipients = list(
        User.objects.filter(is_active=True).filter(
            Q(role__in=[User.Role.STAFF, User.Role.BCH, User.Role.LECTURER])
            | Q(is_superuser=True)
        )
    )
    recipient_ids = {u.pk for u in recipients}
    if announcement.author and announcement.author.is_active and announcement.author_id not in recipient_ids:
        recipients.append(announcement.author)

    registrant_name = registration.full_name or str(registration.user)
    prefix = f'{registrant_name} đã đăng ký tham gia "'
    suffix = '"'
    max_title_len = max(10, 255 - len(prefix) - len(suffix))
    event_title = announcement.title
    if len(event_title) > max_title_len:
        event_title = event_title[: max_title_len - 1].rstrip() + "…"
    message = f"{prefix}{event_title}{suffix}"[:255]

    for admin_user in recipients:
        notify(
            recipient=admin_user,
            verb=Notification.Verb.EVENT_REGISTRATION,
            actor=registration.user,
            message=message,
            content_object=registration,
        )
