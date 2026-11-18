from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, ListView
from django_ratelimit.decorators import ratelimit

from accounts.models import User
from config.mixins import ContentStatus, PinLimitExceeded
from interactions.utils import annotate_interaction_counts

from . import services
from .forms import AnnouncementAttachmentFormSet, AnnouncementForm, EventRegistrationForm
from .models import Announcement, EventRegistration


def _is_admin(user):
    """Nhom Quan tri vien muc Thong bao: Giao vu Khoa (staff), BCH Khoa (bch), Giang vien (lecturer) va Superuser."""
    return user.is_authenticated and (
        user.is_superuser or user.role in (User.Role.STAFF, User.Role.BCH, User.Role.LECTURER)
    )


def _can_post_announcement(user):
    return _is_admin(user)


def _can_edit_announcement(user, announcement):
    return _is_admin(user)


def _can_pin(user):
    return _is_admin(user)


def _can_view_event_registrations(user, announcement):
    return _is_admin(user)


def _can_register_event(user):
    """Chi Nguoi dung (Sinh vien) dang ky tham gia su kien; Quan tri vien chi quan ly."""
    return user.is_authenticated and not _is_admin(user)


class AnnouncementListView(ListView):
    model = Announcement
    template_name = "announcements/announcement_list.html"
    context_object_name = "announcements"
    paginate_by = 20

    def get_queryset(self):
        can_post = _can_post_announcement(self.request.user)
        can_register = _can_register_event(self.request.user)

        if self.request.GET.get("mine") == "1" and can_post:
            qs = Announcement.objects.filter(author=self.request.user).select_related("author")
        else:
            qs = Announcement.objects.filter(status=ContentStatus.PUBLISHED).select_related("author")

        if self.request.GET.get("registered") == "1" and can_register:
            qs = qs.filter(registrations__user=self.request.user).distinct()

        kind = self.request.GET.get("kind")
        if kind in (Announcement.Kind.OFFICIAL, Announcement.Kind.EVENT):
            qs = qs.filter(kind=kind)

        has_deadline = self.request.GET.get("has_deadline") == "1"
        if has_deadline:
            qs = qs.filter(deadline_at__gt=timezone.now())

        qs = annotate_interaction_counts(qs, Announcement).annotate(
            registration_count=Count("registrations", distinct=True)
        )
        if self.request.GET.get("sort") == "activity":
            qs = qs.order_by("-is_pinned", "-last_activity_at")
        elif has_deadline:
            qs = qs.order_by("-is_pinned", "deadline_at")
        else:
            qs = qs.order_by("-is_pinned", "-created_at")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        can_post = _can_post_announcement(self.request.user)
        can_register = _can_register_event(self.request.user)
        ctx["current_sort"] = self.request.GET.get("sort", "new")
        ctx["current_kind"] = self.request.GET.get("kind", "")
        ctx["current_mine"] = self.request.GET.get("mine") == "1" and can_post
        ctx["current_registered"] = self.request.GET.get("registered") == "1" and can_register
        ctx["current_has_deadline"] = self.request.GET.get("has_deadline") == "1"
        ctx["can_post_announcement"] = can_post
        ctx["is_admin_user"] = _is_admin(self.request.user)
        return ctx


class AnnouncementDetailView(DetailView):
    model = Announcement
    template_name = "announcements/announcement_detail.html"
    context_object_name = "announcement"

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if obj.status != ContentStatus.PUBLISHED and not _can_edit_announcement(request.user, obj):
            messages.error(request, "Thông báo này hiện không khả dụng.")
            return redirect("announcements:list")
        return super().dispatch(request, *args, **kwargs)

    def get_object(self, queryset=None):
        if hasattr(self, "_cached_object"):
            return self._cached_object
        obj = super().get_object(queryset)
        self._cached_object = obj
        return obj

    def get(self, request, *args, **kwargs):
        from interactions.utils import increment_view_count

        self.object = self.get_object()
        increment_view_count(self.object)
        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

    def get_context_data(self, **kwargs):
        from django.contrib.contenttypes.models import ContentType

        from interactions.utils import get_interaction_context, get_top_level_comments

        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        content_type = ContentType.objects.get_for_model(Announcement)
        ctx["model_key"] = "announcement"
        ctx["comments"] = get_top_level_comments(content_type, self.object.pk, viewer=user)
        ctx.update(get_interaction_context(user, self.object))

        ctx["now"] = timezone.now()
        is_admin = _is_admin(user)
        can_register = _can_register_event(user)
        user_registration = (
            self.object.registrations.filter(user=user).first()
            if can_register
            else None
        )
        ctx["is_admin_user"] = is_admin
        ctx["can_edit"] = _can_edit_announcement(user, self.object)
        ctx["can_pin"] = _can_pin(user)
        ctx["can_register"] = can_register
        ctx["can_report"] = user.is_authenticated and not is_admin and user != self.object.author
        ctx["can_view_history"] = is_admin
        ctx["user_registration"] = user_registration
        ctx["user_registered"] = user_registration is not None

        if (
            self.object.kind == Announcement.Kind.EVENT
            and can_register
            and not user_registration
        ):
            ctx["registration_form"] = EventRegistrationForm(user=user)

        can_view_regs = (
            self.object.kind == Announcement.Kind.EVENT
            and _can_view_event_registrations(user, self.object)
        )
        ctx["can_view_registrations"] = can_view_regs
        if can_view_regs:
            ctx["registrations"] = self.object.registrations.select_related("user").all()

        return ctx


class _StaffOrBchRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        return _can_post_announcement(self.request.user)


@ratelimit(key="user", rate="10/m", block=True)
def announcement_create(request):
    if not _can_post_announcement(request.user):
        messages.error(request, "Chỉ Giáo vụ Khoa, BCH Khoa hoặc Giảng viên mới được đăng thông tin/thông báo.")
        return redirect("announcements:list")

    if request.method == "POST":
        form = AnnouncementForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            announcement, keyword = services.create_announcement(
                author=request.user,
                kind=form.cleaned_data["kind"],
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                event_datetime=form.cleaned_data.get("event_datetime"),
                event_end_datetime=form.cleaned_data.get("event_end_datetime"),
                deadline_at=form.cleaned_data.get("deadline_at"),
                featured_image=form.cleaned_data.get("featured_image"),
            )
            formset = AnnouncementAttachmentFormSet(request.POST, request.FILES, instance=announcement)
            if formset.is_valid():
                formset.save()
            if keyword:
                messages.warning(request, "Nội dung chứa từ khóa nhạy cảm nên đã chuyển sang trạng thái chờ kiểm duyệt.")
            else:
                messages.success(request, "Đã đăng thông báo.")
            return redirect(announcement.get_absolute_url())
    else:
        form = AnnouncementForm(user=request.user)
    formset = AnnouncementAttachmentFormSet()
    return render(request, "announcements/announcement_form.html", {"form": form, "formset": formset})


def announcement_edit(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    if not _can_edit_announcement(request.user, announcement):
        messages.error(request, "Bạn không có quyền sửa thông báo này.")
        return redirect(announcement.get_absolute_url())

    if request.method == "POST":
        form = AnnouncementForm(request.POST, request.FILES, instance=announcement, user=request.user)
        formset = AnnouncementAttachmentFormSet(request.POST, request.FILES, instance=announcement)
        if form.is_valid() and formset.is_valid():
            announcement, keyword = services.update_announcement(
                announcement=announcement,
                editor=request.user,
                title=form.cleaned_data["title"],
                body=form.cleaned_data["body"],
                event_datetime=form.cleaned_data.get("event_datetime"),
                event_end_datetime=form.cleaned_data.get("event_end_datetime"),
                deadline_at=form.cleaned_data.get("deadline_at"),
                featured_image=form.cleaned_data.get("featured_image"),
            )
            formset.save()
            if keyword:
                messages.warning(request, "Nội dung chứa từ khóa nhạy cảm nên đã chuyển sang trạng thái chờ kiểm duyệt.")
            else:
                messages.success(request, "Đã cập nhật thông báo.")
            return redirect(announcement.get_absolute_url())
    else:
        form = AnnouncementForm(instance=announcement, user=request.user)
        formset = AnnouncementAttachmentFormSet(instance=announcement)
    return render(
        request,
        "announcements/announcement_form.html",
        {"form": form, "formset": formset, "object": announcement},
    )


@require_POST
def announcement_pin(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    if not _can_pin(request.user):
        messages.error(request, "Bạn không có quyền ghim thông báo này.")
        return redirect(announcement.get_absolute_url())
    try:
        services.pin_announcement(announcement, request.user)
        messages.success(request, "Đã ghim thông báo.")
    except PinLimitExceeded as exc:
        messages.error(request, str(exc))
    return redirect(announcement.get_absolute_url())


@require_POST
def announcement_unpin(request, pk):
    announcement = get_object_or_404(Announcement, pk=pk)
    if not _can_pin(request.user):
        messages.error(request, "Bạn không có quyền bỏ ghim thông báo này.")
        return redirect(announcement.get_absolute_url())
    services.unpin_announcement(announcement, request.user)
    messages.success(request, "Đã bỏ ghim thông báo.")
    return redirect(announcement.get_absolute_url())


@login_required
def register_event(request, pk):
    announcement = get_object_or_404(
        Announcement,
        pk=pk,
        kind=Announcement.Kind.EVENT,
        status=ContentStatus.PUBLISHED,
    )

    if not _can_register_event(request.user):
        messages.info(request, "Tài khoản Quản trị viên chỉ quản lý sự kiện, không đăng ký tham gia.")
        return redirect(announcement.get_absolute_url())

    if announcement.deadline_at and announcement.deadline_at <= timezone.now():
        messages.error(request, "Sự kiện đã hết hạn đăng ký.")
        return redirect(announcement.get_absolute_url())

    if EventRegistration.objects.filter(announcement=announcement, user=request.user).exists():
        messages.info(request, "Bạn đã đăng ký tham gia sự kiện này rồi.")
        return redirect(announcement.get_absolute_url())

    if request.method == "POST":
        form = EventRegistrationForm(request.POST, user=request.user)
        if form.is_valid():
            registration = form.save(commit=False)
            registration.announcement = announcement
            registration.user = request.user
            registration.save()
            if announcement.deadline_at:
                from notifications.models import DeadlineCompletion

                DeadlineCompletion.objects.get_or_create(
                    announcement=announcement,
                    user=request.user,
                )
            services.notify_admins_event_registration(registration)
            messages.success(request, "Bạn đã đăng ký tham gia sự kiện thành công.")
            return redirect(announcement.get_absolute_url())
    else:
        form = EventRegistrationForm(user=request.user)

    return render(
        request,
        "announcements/event_register_form.html",
        {"announcement": announcement, "form": form},
    )