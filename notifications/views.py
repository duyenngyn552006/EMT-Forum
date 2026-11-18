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
                verb__in=["new_announcement", "event_registration"]
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

    unread_count = request.user.notifications.filter(is_read=False).count()

    return render(
        request,
        "notifications/notification_list.html",
        {
            "notifications": notifications,
            "current_filter": current_filter,
            "current_type": current_type,
            "unread_count": unread_count,
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


def _is_admin_user(user):
    from accounts.models import User

    return user.is_superuser or getattr(user, "role", None) in (
        User.Role.STAFF,
        User.Role.BCH,
        User.Role.LECTURER,
    )


def _format_remaining(delta):
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "Đã quá hạn"
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = max(1, rem // 60)
    if days >= 1:
        return f"Còn {days} ngày" if hours == 0 or days >= 3 else f"Còn {days} ngày {hours} giờ"
    if hours >= 1:
        return f"Còn {hours} giờ" if hours >= 6 else f"Còn {hours} giờ {minutes} phút"
    return f"Còn {minutes} phút"


@login_required
def deadline_list(request):
    """Danh sach han chot danh cho nguoi dung (an voi Quan tri vien/Giao vu Khoa)."""
    from datetime import timedelta

    from announcements.models import Announcement, EventRegistration
    from config.mixins import ContentStatus

    if _is_admin_user(request.user):
        return redirect("notifications:list")

    now = timezone.now()
    recent_cutoff = now - timedelta(days=14)
    current_tab = request.GET.get("tab", "all")
    if current_tab not in ("all", "pending", "done", "expired"):
        current_tab = "all"

    all_deadlines = list(
        Announcement.objects.filter(
            status=ContentStatus.PUBLISHED,
            deadline_at__gte=recent_cutoff,
        ).select_related("author").order_by("deadline_at")
    )

    announcement_ids = [a.pk for a in all_deadlines]
    completed_ids = set(
        DeadlineCompletion.objects.filter(
            user=request.user, announcement_id__in=announcement_ids
        ).values_list("announcement_id", flat=True)
    )
    registered_ids = set(
        EventRegistration.objects.filter(
            user=request.user, announcement_id__in=announcement_ids
        ).values_list("announcement_id", flat=True)
    )

    pending_items = []
    done_items = []
    expired_items = []

    for a in all_deadlines:
        is_registered = a.pk in registered_ids
        is_done = is_registered or (a.pk in completed_ids)
        is_expired = a.deadline_at <= now
        remaining = a.deadline_at - now

        if is_done:
            urgency = "done"
            remaining_label = "Đã đăng ký" if is_registered else "Đã hoàn thành"
        elif is_expired:
            urgency = "expired"
            remaining_label = "Đã quá hạn"
        elif remaining <= timedelta(hours=24):
            urgency = "urgent"
            remaining_label = _format_remaining(remaining)
        elif remaining <= timedelta(days=3):
            urgency = "soon"
            remaining_label = _format_remaining(remaining)
        else:
            urgency = "normal"
            remaining_label = _format_remaining(remaining)

        entry = {
            "announcement": a,
            "is_registered": is_registered,
            "is_done": is_done,
            "is_expired": is_expired,
            "urgency": urgency,
            "remaining_label": remaining_label,
        }

        if is_expired:
            if is_done:
                done_items.append(entry)
            else:
                expired_items.append(entry)
        else:
            if is_done:
                done_items.append(entry)
            else:
                pending_items.append(entry)

    # Mac dinh "all" hien viec can lam truoc (gap nhat len dau), roi den muc da xong chua qua han
    upcoming_done = [e for e in done_items if not e["is_expired"]]
    if current_tab == "pending":
        visible_items = pending_items
    elif current_tab == "done":
        visible_items = done_items
    elif current_tab == "expired":
        visible_items = list(reversed(expired_items))
    else:
        visible_items = pending_items + upcoming_done

    return render(
        request,
        "notifications/deadline_list.html",
        {
            "deadlines": [e["announcement"] for e in visible_items],
            "deadline_items": visible_items,
            "completed_ids": completed_ids | registered_ids,
            "current_tab": current_tab,
            "pending_count": len(pending_items),
            "upcoming_count": len(pending_items) + len(upcoming_done),
            "done_count": len(done_items),
            "expired_count": len(expired_items),
        },
    )


@login_required
@require_POST
def complete_deadline(request, announcement_pk):
    from announcements.models import Announcement

    if _is_admin_user(request.user):
        return redirect("notifications:list")

    announcement = get_object_or_404(
        Announcement, pk=announcement_pk, deadline_at__isnull=False
    )
    DeadlineCompletion.objects.get_or_create(announcement=announcement, user=request.user)
    return redirect(_safe_next_url(request, fallback=reverse("notifications:deadlines")))


@login_required
@require_POST
def uncomplete_deadline(request, announcement_pk):
    if _is_admin_user(request.user):
        return redirect("notifications:list")

    DeadlineCompletion.objects.filter(
        announcement_id=announcement_pk, user=request.user
    ).delete()
    return redirect(_safe_next_url(request, fallback=reverse("notifications:deadlines")))


@login_required
def preferences(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    is_admin = _is_admin_user(request.user)
    if request.method == "POST":
        fields = [
            "notify_on_comment", "notify_on_reply", "notify_on_like", "notify_on_follow",
            "notify_on_announcement", "notify_on_group_activity",
            "notify_on_moderation", "email_digest",
        ]
        if not is_admin:
            fields.append("notify_on_deadline")
        for field in fields:
            setattr(prefs, field, field in request.POST)
        prefs.save()
        return redirect("notifications:preferences")
    return render(
        request,
        "notifications/preferences.html",
        {"prefs": prefs, "is_admin_user": is_admin},
    )
