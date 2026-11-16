from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DeleteView, ListView, UpdateView
from django_ratelimit.decorators import ratelimit

from accounts.models import User
from config.mixins import ContentStatus
from interactions.utils import resolve_target
from notifications.models import Notification
from notifications.services import notify

from .forms import (
    AccountAppealForm,
    AppealReviewForm,
    ContentRejectForm,
    ReportForm,
    ReportResolveForm,
    SensitiveKeywordForm,
)
from .models import AccountAppeal, ModerationLog, Report, SensitiveKeyword
from .services import log_action


def _is_staff(user):
    return user.is_authenticated and user.role == User.Role.STAFF


class _StaffRequiredMixin:
    """Chi Giao vu Khoa duoc vao - dung chung cach lam voi accounts.views.UserListView
    (redirect ve home + bao loi) thay vi de UserPassesTestMixin tra ve 403 mac dinh."""

    def dispatch(self, request, *args, **kwargs):
        if not _is_staff(request.user):
            messages.error(request, "Chỉ Giáo vụ Khoa mới có quyền quản lý từ khóa nhạy cảm.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)


class SensitiveKeywordListView(_StaffRequiredMixin, ListView):
    """Quan ly tu khoa nhay cam ngay trong giao dien EMT Forum - thay the cho viec phai
    vao Django Admin (/admin/) de lam viec nay."""

    model = SensitiveKeyword
    template_name = "moderation/keyword_list.html"
    context_object_name = "keywords"


class ModerationLogListView(_StaffRequiredMixin, ListView):
    """Xem toan bo nhat ky xu ly vi pham - truoc day chi xem duoc theo tung user rieng
    le luc duyet khang nghi, gio co them trang duyet tu do cho ca he thong."""

    model = ModerationLog
    template_name = "moderation/moderation_log_list.html"
    context_object_name = "logs"
    paginate_by = 30

    def get_queryset(self):
        qs = ModerationLog.objects.select_related("actor", "target_user", "content_type").order_by("-created_at")
        action = self.request.GET.get("action")
        if action:
            qs = qs.filter(action=action)
        user_id = self.request.GET.get("user")
        if user_id:
            qs = qs.filter(target_user_id=user_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["action_choices"] = ModerationLog.Action.choices
        ctx["current_action"] = self.request.GET.get("action", "")
        user_id = self.request.GET.get("user")
        # Xem lich su vi pham rieng cua 1 nguoi dung - bam tu trang Quan ly tai khoan
        # (accounts/user_list.html) de xem truoc khi quyet dinh khoa/mo khoa/duyet khang nghi.
        ctx["filtered_user"] = User.objects.filter(pk=user_id).first() if user_id else None
        return ctx


class SensitiveKeywordCreateView(_StaffRequiredMixin, CreateView):
    model = SensitiveKeyword
    form_class = SensitiveKeywordForm
    template_name = "moderation/keyword_form.html"
    success_url = reverse_lazy("moderation:keyword_list")

    def form_valid(self, form):
        messages.success(self.request, "Đã thêm từ khóa nhạy cảm.")
        return super().form_valid(form)


class SensitiveKeywordUpdateView(_StaffRequiredMixin, UpdateView):
    model = SensitiveKeyword
    form_class = SensitiveKeywordForm
    template_name = "moderation/keyword_form.html"
    success_url = reverse_lazy("moderation:keyword_list")

    def form_valid(self, form):
        messages.success(self.request, "Đã cập nhật từ khóa nhạy cảm.")
        return super().form_valid(form)


class SensitiveKeywordDeleteView(_StaffRequiredMixin, DeleteView):
    model = SensitiveKeyword
    template_name = "moderation/keyword_confirm_delete.html"
    success_url = reverse_lazy("moderation:keyword_list")

    def form_valid(self, form):
        messages.success(self.request, "Đã xóa từ khóa nhạy cảm.")
        return super().form_valid(form)


def _content_url(obj):
    """Duong dan toi noi dung bi bao cao. Comment khong co trang rieng -> tro ve noi dung
    cha va nhay toi dung binh luan (id comment-bubble-<pk> trong _interactions.html)."""
    if obj is None:
        return ""
    if hasattr(obj, "get_absolute_url"):
        return obj.get_absolute_url()
    parent = getattr(obj, "content_object", None)
    if parent is not None and hasattr(parent, "get_absolute_url"):
        return f"{parent.get_absolute_url()}#comment-bubble-{obj.pk}"
    return ""


# Ten tieng Viet cua loai noi dung (content_type.name mac dinh la tieng Anh, vd "forum post")
_TYPE_NAMES = {
    "forumpost": "Bài viết diễn đàn",
    "announcement": "Thông báo",
    "grouppost": "Bài viết trong nhóm",
    "comment": "Bình luận",
}


def _type_name(content_type):
    return _TYPE_NAMES.get(content_type.model, content_type.name)


def _content_label(obj, limit=80):
    """Ten ngan gon cua noi dung de hien thi/gui thong bao: tieu de neu co, khong thi trich noi dung."""
    if obj is None:
        return "(nội dung đã bị xóa)"
    text = getattr(obj, "title", "") or getattr(obj, "body", "") or str(obj)
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@login_required
def create_report(request, model_key, object_id):
    content_type, obj = resolve_target(model_key, object_id)
    back_url = _content_url(obj) or "/"

    # M2 - chong bao cao trung: moi nguoi chi co 1 bao cao dang mo cho cung 1 noi dung
    already_reported = Report.objects.filter(
        reporter=request.user, content_type=content_type, object_id=obj.pk, status__in=Report.OPEN_STATUSES,
    ).exists()
    if already_reported:
        messages.info(request, "Bạn đã báo cáo nội dung này, Giáo vụ Khoa đang xem xét.")
        return redirect(back_url)

    if request.method == "POST":
        form = ReportForm(request.POST)
        if form.is_valid():
            Report.objects.create(
                reporter=request.user, content_type=content_type, object_id=obj.pk,
                category=form.cleaned_data["category"],
                reason=form.cleaned_data["reason"],
            )
            messages.success(request, "Đã gửi báo cáo vi phạm, Giáo vụ Khoa sẽ xem xét.")
            return redirect(back_url)
    else:
        form = ReportForm()
    return render(
        request, "moderation/report_form.html",
        {"form": form, "object": obj, "object_label": _content_label(obj), "back_url": back_url},
    )


def _open_report_groups(category=""):
    """M2 - gop cac bao cao dang mo theo noi dung: moi noi dung 1 nhom, kem so nguoi bao cao
    va cac loai vi pham duoc chon. Nhom co bao cao moi nhat dung truoc."""
    reports = (
        Report.objects.filter(status__in=Report.OPEN_STATUSES)
        .select_related("reporter", "content_type")
        .order_by("-created_at")
    )
    groups = {}
    for report in reports:
        key = (report.content_type_id, report.object_id)
        if key not in groups:
            groups[key] = {"reports": [], "categories": {}}
        group = groups[key]
        group["reports"].append(report)
        label = report.get_category_display()
        group["categories"][label] = group["categories"].get(label, 0) + 1

    result = []
    for group in groups.values():
        if category and not any(r.category == category for r in group["reports"]):
            continue
        latest = group["reports"][0]
        obj = latest.content_object
        result.append({
            "latest": latest,
            "reports": group["reports"],
            "reporter_count": len({r.reporter_id for r in group["reports"]}),
            "categories": sorted(group["categories"].items(), key=lambda item: -item[1]),
            "object": obj,
            "label": _content_label(obj),
            "url": _content_url(obj),
            "type_name": _type_name(latest.content_type),
        })
    # Noi dung bi nhieu nguoi bao cao len dau de Giao vu xu ly truoc
    result.sort(key=lambda g: (-g["reporter_count"], -g["latest"].created_at.timestamp()))
    return result


@user_passes_test(_is_staff, login_url="home")
def report_list(request):
    category = request.GET.get("category", "")
    if category not in Report.Category.values:
        category = ""
    groups = _open_report_groups(category)
    return render(
        request, "moderation/report_list.html",
        {
            "groups": groups,
            "category_choices": Report.Category.choices,
            "current_category": category,
            "open_count": Report.objects.filter(status__in=Report.OPEN_STATUSES).count(),
        },
    )


_RESULT_TEXT = {
    "dismiss": "không phát hiện vi phạm, nội dung được giữ nguyên",
    "hide": "nội dung đã bị ẩn",
    "delete": "nội dung đã bị xóa",
}


def _notify_report_result(actor, obj, reports, action, note):
    """M3 - bao ket qua xu ly cho tung nguoi bao cao va cho tac gia noi dung (neu bi an/xoa)."""
    label = _content_label(obj, limit=60)
    # Noi dung bi an/xoa thi nguoi khac khong xem duoc nua -> khong gan lien ket
    link_target = obj if action == "dismiss" else None
    if link_target is not None and not hasattr(link_target, "get_absolute_url"):
        link_target = getattr(link_target, "content_object", None)

    notified = set()
    for report in reports:
        if report.reporter_id in notified:
            continue
        notified.add(report.reporter_id)
        notify(
            recipient=report.reporter, verb=Notification.Verb.MODERATION, actor=actor,
            message=f"Báo cáo của bạn về \"{label}\" đã được xử lý: {_RESULT_TEXT[action]}."[:255],
            content_object=link_target,
        )

    author = getattr(obj, "author", None)
    if action != "dismiss" and author is not None:
        verb_text = "bị ẩn" if action == "hide" else "bị xóa"
        categories = ", ".join(sorted({r.get_category_display() for r in reports}))
        notify(
            recipient=author, verb=Notification.Verb.MODERATION, actor=actor,
            message=f"Nội dung \"{label}\" của bạn đã {verb_text} do vi phạm ({categories}). Ghi chú: {note}"[:255],
        )


@user_passes_test(_is_staff, login_url="home")
def report_resolve(request, pk):
    report = get_object_or_404(Report, pk=pk)
    # M2 - xu ly 1 lan cho moi bao cao dang mo cua cung noi dung
    related_reports = list(
        Report.objects.filter(
            content_type=report.content_type, object_id=report.object_id, status__in=Report.OPEN_STATUSES,
        ).select_related("reporter").order_by("-created_at")
    ) or [report]
    obj = report.content_object

    if request.method == "POST":
        form = ReportResolveForm(request.POST)
        if form.is_valid():
            action = form.cleaned_data["action"]
            note = form.cleaned_data["resolution_note"]

            if action == "hide" and obj is not None and hasattr(obj, "status"):
                obj.status = ContentStatus.HIDDEN
                obj.save(update_fields=["status"])
                log_action(actor=request.user, action=ModerationLog.Action.HIDE, reason=note,
                           target_user=getattr(obj, "author", None), content_object=obj)
            elif action == "delete" and obj is not None and hasattr(obj, "status"):
                obj.status = ContentStatus.REMOVED
                obj.save(update_fields=["status"])
                log_action(actor=request.user, action=ModerationLog.Action.DELETE, reason=note,
                           target_user=getattr(obj, "author", None), content_object=obj)

            now = timezone.now()
            new_status = Report.Status.RESOLVED if action != "dismiss" else Report.Status.DISMISSED
            for item in related_reports:
                item.status = new_status
                item.handled_by = request.user
                item.resolution_note = note
                item.handled_at = now
                item.save(update_fields=["status", "handled_by", "resolution_note", "handled_at"])

            _notify_report_result(request.user, obj, related_reports, action, note)
            count = len(related_reports)
            messages.success(
                request, "Đã xử lý báo cáo." if count == 1 else f"Đã xử lý {count} báo cáo của cùng nội dung."
            )
            return redirect("moderation:report_list")
    else:
        form = ReportResolveForm()
    return render(
        request, "moderation/report_resolve_form.html",
        {
            "form": form, "report": report, "related_reports": related_reports,
            "type_name": _type_name(report.content_type),
            "object": obj, "object_label": _content_label(obj), "object_url": _content_url(obj),
            "reporter_count": len({r.reporter_id for r in related_reports}),
        },
    )


@user_passes_test(_is_staff, login_url="home")
def pending_content_list(request):
    from announcements.models import Announcement
    from forum.models import ForumPost
    from interactions.models import Comment

    # GroupPost KHONG nam trong hang doi nay - bai viet trong nhom la pham vi noi bo,
    # do truong nhom/pho nhom tu duyet (xem groups:post_approve/post_reject), khong
    # lien quan Giao vu Khoa.
    context = {
        "announcements": Announcement.objects.filter(status=ContentStatus.PENDING_REVIEW),
        "forum_posts": ForumPost.objects.filter(status=ContentStatus.PENDING_REVIEW),
        "comments": Comment.objects.filter(status=Comment.Status.PENDING_REVIEW),
    }
    return render(request, "moderation/pending_content_list.html", context)


@user_passes_test(_is_staff, login_url="home")
def approve_content(request, model_key, object_id):
    if model_key == "grouppost":
        messages.error(request, "Bài viết trong nhóm do trưởng/phó nhóm duyệt, không thuộc phạm vi Giáo vụ Khoa.")
        return redirect("moderation:pending_content_list")
    content_type, obj = resolve_target(model_key, object_id)
    obj.status = "published"
    obj.save(update_fields=["status"])
    log_action(actor=request.user, action=ModerationLog.Action.APPROVE_CONTENT, reason="Duyệt nội dung chờ kiểm duyệt", content_object=obj)
    _notify_review_result(request.user, obj, approved=True)
    if model_key == "forumpost":
        # Bai vua duoc duyet moi hien thi cong khai -> luc nay moi bao nguoi theo doi chuyen muc/the
        from forum.services import notify_followers_of_post

        notify_followers_of_post(obj)
    messages.success(request, "Đã duyệt và đăng nội dung.")
    return redirect("moderation:pending_content_list")


@user_passes_test(_is_staff, login_url="home")
def reject_content(request, model_key, object_id):
    if model_key == "grouppost":
        messages.error(request, "Bài viết trong nhóm do trưởng/phó nhóm duyệt, không thuộc phạm vi Giáo vụ Khoa.")
        return redirect("moderation:pending_content_list")
    content_type, obj = resolve_target(model_key, object_id)
    if request.method == "POST":
        form = ContentRejectForm(request.POST)
        if form.is_valid():
            reason = form.cleaned_data["reason"]
            obj.status = "removed"
            obj.save(update_fields=["status"])
            log_action(actor=request.user, action=ModerationLog.Action.REJECT_CONTENT, reason=reason, content_object=obj)
            _notify_review_result(request.user, obj, approved=False, reason=reason)
            messages.success(request, "Đã từ chối nội dung.")
            return redirect("moderation:pending_content_list")
    else:
        form = ContentRejectForm()
    return render(
        request, "moderation/reject_content_form.html",
        {"form": form, "object": obj, "is_rich_text": model_key != "comment"},
    )


def _notify_review_result(actor, obj, approved, reason=""):
    """Bao cho nguoi tao biet ket qua duyet/tu choi noi dung cho kiem duyet cua ho."""
    author = getattr(obj, "author", None)
    if not author:
        return
    if approved:
        message = "Nội dung của bạn đã được duyệt và đăng."
    else:
        message = f"Nội dung của bạn đã bị từ chối sau kiểm duyệt. Lý do: {reason}"
    # Comment khong co get_absolute_url rieng - tro ve noi dung cha (content_object cua Comment)
    link_target = obj if hasattr(obj, "get_absolute_url") else getattr(obj, "content_object", None)
    notify(
        recipient=author, verb=Notification.Verb.MODERATION, actor=actor,
        message=message, content_object=link_target,
    )


def _appealing_user(request):
    """Nguoi bi khoa tai khoan KHONG BAO GIO co request.user: Django xac thuc lai
    is_active moi request qua ModelBackend.get_user() (khong chi luc dang nhap), nen
    ngay request KE TIEP sau khi bi khoa (du session cu/force_login van con) la
    request.user da thanh AnonymousUser roi. Phai doc pk da luu tam trong session luc
    dang nhap bi chan (xem accounts/adapter.py::LockedAccountAdapter)."""
    from accounts.adapter import LOCKED_ACCOUNT_SESSION_KEY

    locked_user_id = request.session.get(LOCKED_ACCOUNT_SESSION_KEY)
    if locked_user_id:
        return User.objects.filter(pk=locked_user_id, is_active=False).first()
    return None


@ratelimit(key="ip", rate="5/m", block=True)
def appeal_create(request):
    # Khong con @login_required: nguoi bi khoa KHONG dang nhap duoc (xem _appealing_user
    # ben duoi), nen chan spam bang rate-limit theo IP thay vi theo user.
    from accounts.adapter import LOCKED_ACCOUNT_SESSION_KEY

    target_user = _appealing_user(request)
    if target_user is None:
        messages.info(request, "Không tìm thấy tài khoản bị khóa cần kháng nghị.")
        return redirect("home")
    if request.method == "POST":
        form = AccountAppealForm(request.POST)
        if form.is_valid():
            appeal = form.save(commit=False)
            appeal.user = target_user
            appeal.save()
            request.session.pop(LOCKED_ACCOUNT_SESSION_KEY, None)
            messages.success(request, "Đã gửi kháng nghị, Giáo vụ Khoa sẽ xem xét lịch sử vi phạm trước khi phản hồi.")
            return redirect("home")
    else:
        form = AccountAppealForm()
    return render(request, "moderation/appeal_form.html", {"form": form, "target_user": target_user})


@user_passes_test(_is_staff, login_url="home")
def appeal_list(request):
    appeals = AccountAppeal.objects.filter(status=AccountAppeal.Status.PENDING).select_related("user")
    return render(request, "moderation/appeal_list.html", {"appeals": appeals})


@user_passes_test(_is_staff, login_url="home")
def appeal_review(request, pk):
    appeal = get_object_or_404(AccountAppeal, pk=pk)
    violation_history = ModerationLog.objects.filter(target_user=appeal.user)
    if request.method == "POST":
        form = AppealReviewForm(request.POST)
        if form.is_valid():
            decision = form.cleaned_data["decision"]
            note = form.cleaned_data["review_note"]
            appeal.status = AccountAppeal.Status.ACCEPTED if decision == "accept" else AccountAppeal.Status.REJECTED
            appeal.review_note = note
            appeal.reviewed_by = request.user
            appeal.reviewed_at = timezone.now()
            appeal.save()

            if decision == "accept":
                appeal.user.is_active = True
                appeal.user.locked_reason = ""
                appeal.user.locked_at = None
                appeal.user.save(update_fields=["is_active", "locked_reason", "locked_at"])
                log_action(actor=request.user, action=ModerationLog.Action.APPEAL_ACCEPTED, reason=note, target_user=appeal.user)
            else:
                log_action(actor=request.user, action=ModerationLog.Action.APPEAL_REJECTED, reason=note, target_user=appeal.user)

            notify(
                recipient=appeal.user, verb=Notification.Verb.APPEAL_RESULT, actor=request.user,
                message=f"Kháng nghị của bạn đã được {appeal.get_status_display().lower()}: {note}",
            )
            messages.success(request, "Đã xử lý kháng nghị.")
            return redirect("moderation:appeal_list")
    else:
        form = AppealReviewForm()
    return render(request, "moderation/appeal_review.html", {"form": form, "appeal": appeal, "violation_history": violation_history})
