from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import DeadlineCompletion, Notification, NotificationPreference


def _safe_next_url(request, fallback):
    """Dung chung 1 cach voi interactions.views._safe_next_url - danh dau tat ca da doc
    co the bam tu dropdown chuong o BAT KY trang nao, phai o lai trang do thay vi bi
    chuyen thang sang /notifications/ (giong nhu bam "Xem tat ca")."""
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return next_url
    return fallback


@login_required
def notification_list(request):
    notifications = request.user.notifications.all()

    current_filter = request.GET.get("filter", "all")
    current_type = request.GET.get("type", "all")

    if current_filter == "unread":
        notifications = notifications.filter(is_read=False)

    if current_type != "all":
        if current_type == "announcement":
            notifications = notifications.filter(
                verb="new_announcement"
            )
        elif current_type == "deadline":
            notifications = notifications.filter(
                verb="deadline_reminder"
            )
        elif current_type == "comment":
            notifications = notifications.filter(
                verb__in=["comment", "reply"]
            )
        elif current_type == "group":
            notifications = notifications.filter(
                verb__startswith="group_"
            )

    return render(
        request,
        "notifications/notification_list.html",
        {
            "notifications": notifications,
            "current_filter": current_filter,
            "current_type": current_type,
        },
    )
@require_POST
def mark_read(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    notification.is_read = True
    notification.save(update_fields=["is_read"])
    return redirect("notifications:list")


@login_required
def click_notification(request, pk):
    """Bam vao 1 thong bao trong dropdown chuong -> danh dau da doc roi chuyen den noi dung lien quan."""
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])

    target = notification.content_object
    if target is not None and hasattr(target, "get_absolute_url"):
        return redirect(target.get_absolute_url())
    return redirect("notifications:list")


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect(_safe_next_url(request, fallback=reverse("notifications:list")))


@login_required
def deadline_list(request):
    """Danh sach han chot chua qua han, kem trang thai hoan thanh cua nguoi dang nhap."""
    from announcements.models import Announcement
    from config.mixins import ContentStatus

    deadlines = Announcement.objects.filter(
        status=ContentStatus.PUBLISHED,
        deadline_at__gt=timezone.now(),
    ).select_related("author").order_by("deadline_at")

    completed_ids = set(
        DeadlineCompletion.objects.filter(
            user=request.user, announcement__in=deadlines
        ).values_list("announcement_id", flat=True)
    )
    return render(
        request,
        "notifications/deadline_list.html",
        {"deadlines": deadlines, "completed_ids": completed_ids},
    )


@login_required
@require_POST
def complete_deadline(request, announcement_pk):
    from announcements.models import Announcement

    announcement = get_object_or_404(
        Announcement, pk=announcement_pk, deadline_at__isnull=False
    )
    DeadlineCompletion.objects.get_or_create(announcement=announcement, user=request.user)
    return redirect("notifications:deadlines")


@login_required
def preferences(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    if request.method == "POST":
        for field in (
            "notify_on_comment", "notify_on_reply", "notify_on_like", "notify_on_follow",
            "notify_on_announcement", "notify_on_deadline", "notify_on_group_activity",
            "notify_on_moderation", "email_digest",
        ):
            setattr(prefs, field, field in request.POST)
        prefs.save()
        return redirect("notifications:preferences")
    return render(request, "notifications/preferences.html", {"prefs": prefs})
