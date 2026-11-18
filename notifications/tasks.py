from celery import shared_task

from .models import Notification
from .services import notify


@shared_task
def broadcast_notification(recipient_ids, verb, message, actor_id=None):
    """Gui thong bao (vd thong bao chinh thuc moi) cho nhieu nguoi dung o background qua Celery."""
    from accounts.models import User

    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    for recipient in User.objects.filter(pk__in=recipient_ids, is_active=True):
        notify(recipient=recipient, verb=verb, actor=actor, message=message)


@shared_task
def send_deadline_reminders():
    """Moi gio kiem tra han chot va gui toi da 1 thong bao cho moi moc nhac."""
    from datetime import timedelta

    from django.db import transaction
    from django.utils import timezone

    from accounts.models import User
    from announcements.models import Announcement
    from config.mixins import ContentStatus

    from .models import DeadlineReminderLog

    now = timezone.now()
    announcements = Announcement.objects.filter(
        status=ContentStatus.PUBLISHED,
        deadline_at__gt=now,
    ).exclude(deadline_at__isnull=True).select_related("author")

    sent_count = 0
    for announcement in announcements:
        remaining = announcement.deadline_at - now

        if timedelta(hours=24) < remaining <= timedelta(hours=48):
            stage = DeadlineReminderLog.Stage.TWO_DAYS
            message = f"Còn khoảng 2 ngày để hoàn thành: {announcement.title}"
        elif timedelta(hours=3) < remaining <= timedelta(hours=24):
            stage = DeadlineReminderLog.Stage.ONE_DAY
            message = f"Còn khoảng 1 ngày để hoàn thành: {announcement.title}"
        elif timedelta(0) < remaining <= timedelta(hours=3):
            stage = DeadlineReminderLog.Stage.THREE_HOURS
            message = f"Chỉ còn khoảng 3 giờ: {announcement.title}"
        else:
            continue

        recipients = User.objects.filter(
            is_active=True,
            role=User.Role.STUDENT,
        ).exclude(deadline_completions__announcement=announcement).exclude(
            event_registrations__announcement=announcement
        )

        for recipient in recipients.iterator():
            with transaction.atomic():
                log, created = DeadlineReminderLog.objects.get_or_create(
                    announcement=announcement,
                    recipient=recipient,
                    stage=stage,
                )
                if not created:
                    continue

                notify(
                    recipient=recipient,
                    verb=Notification.Verb.DEADLINE_REMINDER,
                    message=message,
                    content_object=announcement,
                )
                sent_count += 1

    return sent_count
