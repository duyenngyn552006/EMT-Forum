from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification, NotificationPreference


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
    if current_filter == "unread":
        notifications = notifications.filter(is_read=False)
    return render(
        request, "notifications/notification_list.html",
        {"notifications": notifications, "current_filter": current_filter},
    )


@login_required
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
def preferences(request):
    prefs, _ = NotificationPreference.objects.get_or_create(user=request.user)
    if request.method == "POST":
        for field in (
            "notify_on_comment", "notify_on_reply", "notify_on_like", "notify_on_follow",
            "notify_on_announcement", "notify_on_group_activity", "notify_on_moderation", "email_digest",
        ):
            setattr(prefs, field, field in request.POST)
        prefs.save()
        return redirect("notifications:preferences")
    return render(request, "notifications/preferences.html", {"prefs": prefs})
