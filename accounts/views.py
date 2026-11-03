from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, UpdateView

from moderation.models import ModerationLog
from notifications.models import Notification
from notifications.services import notify

from .models import User


class ProfileDetailView(LoginRequiredMixin, DetailView):
    # Dung pk (khong dung username) lam khoa URL: username luon trung voi email
    # (xem User.save()), neu dung username thi URL trang ca nhan se lo email cua
    # nguoi khac ngay tren thanh dia chi, pha vo yeu cau an email voi nguoi xem khac.
    model = User
    template_name = "accounts/profile_detail.html"
    context_object_name = "profile_user"

    def get_context_data(self, **kwargs):
        from config.mixins import ContentStatus

        ctx = super().get_context_data(**kwargs)
        profile_user = self.object

        # Nguoi xem khong phai chu tai khoan khong duoc thay email - nhung __str__ cua
        # User lai fallback ve email khi chua dat ten (vd tai khoan tao qua Django admin),
        # nen can 1 ten hien thi rieng tranh lo email qua tieu de/trang.
        is_owner = self.request.user == profile_user
        if is_owner:
            ctx["display_name"] = str(profile_user)
        else:
            ctx["display_name"] = profile_user.get_full_name() or "Người dùng ẩn danh"

        ctx["stats"] = {
            "forum_post_count": profile_user.forum_posts.filter(status=ContentStatus.PUBLISHED).count(),
            "group_post_count": profile_user.group_posts.filter(status=ContentStatus.PUBLISHED).count(),
            "comment_count": profile_user.comments.filter(status="published").count(),
            "group_count": profile_user.group_memberships.count(),
        }
        # "Da theo doi" chi chinh chu moi thay (giong email/da luu/dang theo doi) - muc
        # theo doi la lua chon ca nhan, khong phai thong tin cong khai cho nguoi khac xem.
        if is_owner:
            follows_qs = profile_user.follows.select_related("content_type")
            ctx["stats"]["followed_post_count"] = follows_qs.filter(content_type__model="forumpost").count()
            ctx["stats"]["followed_category_count"] = follows_qs.filter(content_type__model="category").count()

        # Lay toan bo hoat dong (khong gioi han) - danh sach dai thi phan trang o template,
        # khong cat cung mot con so co dinh nua.
        activity = []
        for post in profile_user.forum_posts.filter(status=ContentStatus.PUBLISHED).select_related("category"):
            activity.append({
                "type_label": "Đăng bài diễn đàn", "title": post.title,
                "url": post.get_absolute_url(), "created_at": post.created_at,
            })
        for post in profile_user.group_posts.filter(status=ContentStatus.PUBLISHED).select_related("group"):
            activity.append({
                "type_label": f"Đăng bài trong nhóm {post.group.name}", "title": post.title,
                "url": post.get_absolute_url(), "created_at": post.created_at,
            })
        for comment in profile_user.comments.filter(status="published").select_related("content_type"):
            target = comment.content_object
            if target is None or not hasattr(target, "get_absolute_url"):
                continue
            activity.append({
                "type_label": "Bình luận", "title": str(target),
                "url": target.get_absolute_url(), "created_at": comment.created_at,
            })
        activity.sort(key=lambda a: a["created_at"], reverse=True)
        ctx["page_obj"] = Paginator(activity, 5).get_page(self.request.GET.get("page"))

        return ctx


class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = User
    fields = ["first_name", "last_name", "class_name", "phone", "avatar", "cover_image", "bio"]
    template_name = "accounts/profile_form.html"
    success_url = reverse_lazy("accounts:my_profile")

    def get_object(self, queryset=None):
        return self.request.user

    # Nhan tieng Viet cho cac field model chua co verbose_name (tranh hien "Phone", "Avatar", "Bio")
    FIELD_LABELS = {"phone": "Số điện thoại", "avatar": "Ảnh đại diện", "bio": "Giới thiệu bản thân"}

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        for name, label in self.FIELD_LABELS.items():
            if name in form.fields:
                form.fields[name].label = label
        # "Lop" chi danh cho Sinh vien/BCH Khoa (xem User.has_class_name_field)
        if not self.request.user.has_class_name_field:
            form.fields.pop("class_name", None)
        return form


@login_required
def my_profile(request):
    return redirect("accounts:profile_detail", pk=request.user.pk)


def account_inactive_view(request):
    """Ghi de trang 'Tai khoan bi khoa' mac dinh cua allauth (account/account_inactive.html,
    URL accounts/inactive/) de hien ly do/thoi diem khoa + nut gui khang nghi, thay vi
    thong bao chung chung khong biet dang nhap boi ai. pk duoc accounts.adapter luu tam
    vao session ngay khi dang nhap bi chan do khoa (khong pop o day - de con dung tiep
    o trang gui khang nghi, xem moderation.views.appeal_create)."""
    from .adapter import LOCKED_ACCOUNT_SESSION_KEY

    locked_user_id = request.session.get(LOCKED_ACCOUNT_SESSION_KEY)
    locked_user = User.objects.filter(pk=locked_user_id, is_active=False).first() if locked_user_id else None
    return render(request, "account/account_inactive.html", {"locked_user": locked_user})


def _is_staff_role(user):
    return user.is_authenticated and user.role == User.Role.STAFF


class UserListView(LoginRequiredMixin, ListView):
    """Danh sach tai khoan - chi Giao vu Khoa duoc xem de gan/thu hoi vai tro."""

    model = User
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 30

    def dispatch(self, request, *args, **kwargs):
        if not _is_staff_role(request.user):
            messages.error(request, "Chỉ Giáo vụ Khoa mới có quyền quản lý tài khoản.")
            return redirect("home")
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs = User.objects.all().order_by("-date_joined")
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(email__icontains=query)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["roles"] = User.Role.choices
        return ctx


@user_passes_test(_is_staff_role, login_url="home")
def assign_role(request, pk):
    """Gan/thu hoi vai tro cho tai khoan - chi Giao vu Khoa."""
    target = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        new_role = request.POST.get("role")
        if new_role in User.Role.values:
            old_role = target.role
            target.role = new_role
            update_fields = ["role"]
            # "Lop" chi danh cho Sinh vien/BCH Khoa - xoa neu doi sang vai tro khac
            if new_role not in User.ROLES_WITH_CLASS_NAME and target.class_name:
                target.class_name = ""
                update_fields.append("class_name")
            target.save(update_fields=update_fields)
            ModerationLog.objects.create(
                actor=request.user,
                target_user=target,
                action=ModerationLog.Action.ROLE_CHANGE,
                reason=f"Đổi vai trò từ '{old_role}' sang '{new_role}'",
            )
            notify(
                recipient=target, verb=Notification.Verb.MODERATION, actor=request.user,
                message=f"Vai trò của bạn đã được đổi thành {target.get_role_display()}.",
                content_object=target,
            )
            messages.success(request, f"Đã cập nhật vai trò của {target} thành {target.get_role_display()}.")
        else:
            messages.error(request, "Vai trò không hợp lệ.")
        return redirect("accounts:user_list")
    return render(request, "accounts/assign_role_confirm.html", {"target": target, "roles": User.Role.choices})


@user_passes_test(_is_staff_role, login_url="home")
def toggle_lock_account(request, pk):
    """Khoa/mo khoa tai khoan - ghi ModerationLog, yeu cau nhap ly do khi khoa."""
    target = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        if target.is_active:
            reason = request.POST.get("reason", "").strip()
            if not reason:
                messages.error(request, "Phải nhập lý do khi khóa tài khoản.")
                return redirect("accounts:user_list")
            target.is_active = False
            target.locked_reason = reason
            from django.utils import timezone

            target.locked_at = timezone.now()
            target.save(update_fields=["is_active", "locked_reason", "locked_at"])
            ModerationLog.objects.create(
                actor=request.user, target_user=target,
                action=ModerationLog.Action.LOCK_ACCOUNT, reason=reason,
            )
            messages.success(request, f"Đã khóa tài khoản {target}.")
        else:
            target.is_active = True
            target.locked_reason = ""
            target.locked_at = None
            target.save(update_fields=["is_active", "locked_reason", "locked_at"])
            ModerationLog.objects.create(
                actor=request.user, target_user=target,
                action=ModerationLog.Action.UNLOCK_ACCOUNT, reason="Mở khóa tài khoản",
            )
            messages.success(request, f"Đã mở khóa tài khoản {target}.")
    return redirect("accounts:user_list")
