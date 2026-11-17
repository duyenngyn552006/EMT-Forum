from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import DetailView, ListView
from django_ratelimit.decorators import ratelimit

from django.views.decorators.http import require_POST
from accounts.models import User
from config.mixins import ContentStatus
from interactions.documents import build_document_formset, get_documents, save_document_formset
from interactions.forms import PollForm
from interactions.polls import create_poll_from_form, validate_poll_fields
from interactions.utils import annotate_interaction_counts

from . import services
from .forms import (
    BulkInviteMemberForm,
    GroupCreateForm,
    GroupDisableForm,
    GroupEditForm,
    GroupJoinRequestForm,
    GroupPostForm,
    GroupPostRejectForm,
    GroupRejectForm,
    InviteMemberForm,
    JoinRequestRejectForm,
    MemberRoleForm,
    TransferLeadershipForm,
    GroupRecruitmentForm,
)
from .models import Group, GroupJoinRequest, GroupMembership, GroupPost


class GroupListView(ListView):
    model = Group
    template_name = "groups/group_list.html"
    context_object_name = "groups"
    paginate_by = 20

    def get_queryset(self):
        params = self.request.GET
        qs = Group.objects.filter(status=Group.Status.ACTIVE)

        # Tìm theo một phần tên nhóm.
        keyword = params.get("q", "").strip()
        if keyword:
            qs = qs.filter(name__icontains=keyword)

        # Lọc nhóm công khai hoặc riêng tư.
        visibility = params.get("visibility", "")
        if visibility in (Group.Visibility.PUBLIC, Group.Visibility.PRIVATE):
            qs = qs.filter(visibility=visibility)

        # Chỉ lấy nhóm đang tuyển và chưa quá hạn.
        if params.get("recruiting") == "1":
            qs = qs.filter(is_recruiting=True).filter(
                Q(recruit_deadline__isnull=True)
                | Q(recruit_deadline__gte=timezone.localdate())
            )

        # Nhóm của tôi: các nhóm người dùng hiện tại đã tham gia.
        if params.get("mine") == "1":
            if not self.request.user.is_authenticated:
                return qs.none()
            qs = qs.filter(memberships__user=self.request.user)

        return qs.order_by("-created_at", "-pk")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        params = self.request.GET

        ctx["keyword"] = params.get("q", "").strip()
        ctx["selected_visibility"] = params.get("visibility", "")
        ctx["recruiting_only"] = params.get("recruiting") == "1"
        ctx["mine_only"] = params.get("mine") == "1"

        # Giữ từ khóa và bộ lọc khi chuyển trang.
        filters = params.copy()
        filters.pop("page", None)
        ctx["filter_query"] = filters.urlencode()

        return ctx


class GroupDetailView(DetailView):
    model = Group
    template_name = "groups/group_detail.html"
    context_object_name = "group"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        my_membership = self.object.memberships.filter(user=user).first() if user.is_authenticated else None
        ctx["is_member"] = my_membership is not None
        ctx["my_role"] = my_membership.role if my_membership else None
        ctx["is_manager"] = my_membership is not None and my_membership.role in (
            GroupMembership.Role.LEADER, GroupMembership.Role.MODERATOR,
        )
        ctx["is_staff_user"] = user.is_authenticated and user.role == User.Role.STAFF

        # Nhom rieng tu: nguoi khong phai thanh vien (va khong phai Giao vu Khoa) khong
        # xem duoc danh sach bai viet ben trong, nhung van thay thong tin/thanh vien nhom.
        can_view_posts = not self.object.is_private or ctx["is_member"] or ctx["is_staff_user"]
        ctx["can_view_posts"] = can_view_posts
        current_sort = self.request.GET.get("sort", "new")
        if can_view_posts:
            # Bai pending_review cua CHINH tac gia cung hien trong tab thuong (khong can tab
            # rieng, nhom nho) - tranh ho tuong dang loi roi dang lai nhieu lan; nguoi khac
            # van chi thay bai da published nhu cu.
            visible = Q(status=ContentStatus.PUBLISHED)
            if user.is_authenticated:
                visible |= Q(status=ContentStatus.PENDING_REVIEW, author=user)
            posts = annotate_interaction_counts(self.object.posts.filter(visible), GroupPost)
            sort_field = "-last_activity_at" if current_sort == "activity" else "-created_at"
            posts = posts.order_by("-is_pinned", sort_field, "-pk")
        else:
            posts = GroupPost.objects.none()
        # Lấy danh sách từ model để nhóm chưa có bài vẫn hiện đủ 5 mục.
        ctx["group_categories"] = GroupPost.Category.choices

        selected_category = self.request.GET.get("category", "")
        if not ctx["is_member"] or selected_category not in GroupPost.Category.values:
            selected_category = ""

        if selected_category:
            posts = posts.filter(category=selected_category)

        ctx["selected_category"] = selected_category
        ctx["posts"] = posts
        post_query = self.request.GET.get("q", "").strip()

        if post_query and ctx["can_view_posts"]:
            posts = posts.filter(
                Q(title__icontains=post_query) |
                Q(body__icontains=post_query)
            )

        ctx["post_query"] = post_query
        ctx["posts"] = posts
        ctx["current_sort"] = current_sort
        ctx["memberships"] = self.object.memberships.select_related("user").order_by(
            "role", "user__last_name", "user__first_name",
        )
        ctx["invite_member_form"] = InviteMemberForm()
        ctx["bulk_invite_form"] = BulkInviteMemberForm()
        if ctx["is_manager"]:
            ctx["pending_join_requests"] = self.object.join_requests.filter(
                status=GroupJoinRequest.Status.PENDING
            ).select_related("user")
            pending_posts = self.object.posts.filter(status=ContentStatus.PENDING_REVIEW).select_related("author")
            for post in pending_posts:
                post.sensitive_keyword_note = services.get_sensitive_keyword_note(post)
            ctx["pending_posts"] = pending_posts
        ctx["has_pending_join_request"] = (
            user.is_authenticated and not ctx["is_member"]
            and self.object.join_requests.filter(user=user, status=GroupJoinRequest.Status.PENDING).exists()
        )
        # Chọn tab trên trang nhóm; mặc định là Thảo luận.
        tab = self.request.GET.get("tab", "discussion")
        ctx["active_tab"] = tab if tab in ("discussion", "members", "about") else "discussion"

        # Tính số ngày còn lại nếu nhóm đang tuyển và có đặt hạn.
        group = self.object
        ctx["recruit_days_left"] = None
        if group.is_recruiting_open and group.recruit_deadline is not None:
            ctx["recruit_days_left"] = (group.recruit_deadline - timezone.localdate()).days
        ctx["group_categories"] = GroupPost.Category.choices
        return ctx


@login_required
def group_create(request):
    if request.method == "POST":
        form = GroupCreateForm(request.POST, request.FILES)
        if form.is_valid():
            group = services.request_create_group(
                creator=request.user, name=form.cleaned_data["name"], description=form.cleaned_data["description"],
                logo=form.cleaned_data.get("logo"), cover_image=form.cleaned_data.get("cover_image"),
                visibility=form.cleaned_data["visibility"],
            )
            if group.status == Group.Status.PENDING:
                messages.info(request, "Yêu cầu tạo nhóm đã được gửi, chờ Giáo vụ Khoa duyệt.")
            else:
                messages.success(request, "Đã tạo nhóm thành công.")
            return redirect("groups:list")
    else:
        form = GroupCreateForm()
    return render(request, "groups/group_form.html", {"form": form})


def _is_staff(user):
    return user.is_authenticated and user.role == User.Role.STAFF


@user_passes_test(_is_staff, login_url="home")
def pending_group_list(request):
    pending = Group.objects.filter(status=Group.Status.PENDING)
    return render(request, "groups/pending_group_list.html", {"groups": pending})


@user_passes_test(_is_staff, login_url="home")
def group_approve(request, pk):
    group = get_object_or_404(Group, pk=pk, status=Group.Status.PENDING)
    services.approve_group(group, request.user)
    messages.success(request, f"Đã duyệt nhóm '{group.name}'.")
    return redirect("groups:pending_list")


@user_passes_test(_is_staff, login_url="home")
def group_reject(request, pk):
    group = get_object_or_404(Group, pk=pk, status=Group.Status.PENDING)
    if request.method == "POST":
        form = GroupRejectForm(request.POST)
        if form.is_valid():
            services.reject_group(group, request.user, form.cleaned_data["reason"])
            messages.success(request, f"Đã từ chối nhóm '{group.name}'.")
            return redirect("groups:pending_list")
    else:
        form = GroupRejectForm()
    return render(request, "groups/group_reject_form.html", {"form": form, "group": group})


def _is_group_member(user, group):
    return user.is_authenticated and group.memberships.filter(user=user).exists()


def _apply_mention_feed(form, group):
    """Gan danh sach thanh vien nhom vao CKEditor Mention feed cua field 'body' (@gan the)."""
    from .mentions import build_mention_feed

    form.fields["body"].widget.config = dict(form.fields["body"].widget.config)
    form.fields["body"].widget.config["mention"] = {
        "feeds": [{"marker": "@", "feed": build_mention_feed(group), "minimumCharacters": 0}],
    }


@login_required
@ratelimit(key="user", rate="10/m", block=True)
def group_post_create(request, slug):
    from .mentions import extract_mentioned_user_ids, notify_mentions

    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_member(request.user, group):
        messages.error(request, "Bạn phải là thành viên nhóm mới được đăng bài.")
        return redirect(group.get_absolute_url())
    document_formset = build_document_formset(request, GroupPost())
    poll_form = PollForm(request.POST or None, prefix="poll")
    poll_requested = bool(request.POST.get("poll-question", "").strip())
    if request.method == "POST":
        form = GroupPostForm(request.POST, request.FILES)
        _apply_mention_feed(form, group)
        poll_ok = not poll_requested or poll_form.is_valid()
        if poll_ok and poll_requested:
            keyword_in_poll = validate_poll_fields(poll_form)
            if keyword_in_poll:
                poll_form.add_error(
                    None, f"Nội dung bình chọn chứa từ khóa nhạy cảm ('{keyword_in_poll.keyword}'). Vui lòng sửa hoặc bỏ phần bình chọn.",
                )
                poll_ok = False
        if form.is_valid() and document_formset.is_valid() and poll_ok:
            post, keyword = services.create_group_post(
                group, request.user, form.cleaned_data["title"], form.cleaned_data["body"],
                featured_image=form.cleaned_data.get("featured_image"),
                category=form.cleaned_data["category"],
            )
            save_document_formset(document_formset, post)
            notify_mentions(request.user, extract_mentioned_user_ids(post.body), post)
            if poll_requested:
                create_poll_from_form(poll_form, post, request.user)
            if keyword:
                messages.warning(
                    request,
                    "Bài viết chứa từ khóa nhạy cảm, đang chờ trưởng/phó nhóm duyệt.",
                )
            elif post.status == ContentStatus.PUBLISHED:
                messages.success(request, "Đã đăng bài viết vào nhóm.")
            else:
                messages.success(
                    request,
                    "Đã gửi bài viết, đang chờ trưởng/phó nhóm duyệt.",
                )
            return redirect(post.get_absolute_url())
    else:
        form = GroupPostForm()
        _apply_mention_feed(form, group)
    return render(
        request, "groups/group_post_form.html",
        {"form": form, "group": group, "document_formset": document_formset, "poll_form": poll_form},
    )


def _can_manage_group_post(user, post):
    return user.is_authenticated and (
        user == post.author or user.role == User.Role.STAFF or _is_group_manager(user, post.group)
    )


@login_required
def group_post_edit(request, slug, pk):
    from .mentions import extract_mentioned_user_ids, notify_mentions

    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    post = get_object_or_404(GroupPost, pk=pk, group=group)
    if not _can_manage_group_post(request.user, post):
        messages.error(request, "Bạn không có quyền sửa bài viết này.")
        return redirect(post.get_absolute_url())
    document_formset = build_document_formset(request, post)
    if request.method == "POST":
        form = GroupPostForm(request.POST, request.FILES, instance=post)
        _apply_mention_feed(form, group)
        if form.is_valid() and document_formset.is_valid():
            post, keyword = services.update_group_post(
                post=post, editor=request.user,
                title=form.cleaned_data["title"], body=form.cleaned_data["body"],
                featured_image=form.cleaned_data.get("featured_image"),
                category=form.cleaned_data["category"],
            )
            save_document_formset(document_formset, post)
            notify_mentions(request.user, extract_mentioned_user_ids(post.body), post)
            if keyword:
                messages.warning(request, "Bài viết chứa từ khóa nhạy cảm, đang chờ trưởng/phó nhóm duyệt lại.")
            else:
                messages.success(request, "Đã cập nhật bài viết, đang chờ trưởng/phó nhóm duyệt lại.")
            return redirect(post.get_absolute_url())
    else:
        form = GroupPostForm(instance=post)
        _apply_mention_feed(form, group)
    return render(
        request, "groups/group_post_form.html",
        {"form": form, "group": group, "object": post, "document_formset": document_formset},
    )


@login_required
def group_post_delete(request, slug, pk):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    post = get_object_or_404(GroupPost, pk=pk, group=group)
    if not _can_manage_group_post(request.user, post):
        messages.error(request, "Bạn không có quyền xóa bài viết này.")
        return redirect(post.get_absolute_url())
    if request.method == "POST":
        post.delete()
        messages.success(request, "Đã xóa bài viết.")
        return redirect(group.get_absolute_url())
    return render(request, "groups/group_post_confirm_delete.html", {"post": post, "group": group})


@login_required
def group_post_approve(request, slug, pk):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    post = get_object_or_404(GroupPost, pk=pk, group=group, status=ContentStatus.PENDING_REVIEW)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền duyệt bài viết trong nhóm.")
        return redirect(group.get_absolute_url())
    if request.method == "POST":
        services.approve_group_post(post, request.user)
        messages.success(request, "Đã duyệt và đăng bài viết.")
    return redirect(group.get_absolute_url())


@login_required
def group_post_reject(request, slug, pk):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    post = get_object_or_404(GroupPost, pk=pk, group=group, status=ContentStatus.PENDING_REVIEW)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền từ chối bài viết trong nhóm.")
        return redirect(group.get_absolute_url())
    if request.method == "POST":
        form = GroupPostRejectForm(request.POST)
        if form.is_valid():
            try:
                services.reject_group_post(post, request.user, form.cleaned_data["reason"])
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Đã từ chối bài viết.")
                return redirect(group.get_absolute_url())
    else:
        form = GroupPostRejectForm()
    return render(
        request, "groups/group_post_reject_form.html",
        {"form": form, "group": group, "post": post, "sensitive_keyword_note": services.get_sensitive_keyword_note(post)},
    )


def _is_group_manager(user, group):
    return user.is_authenticated and group.memberships.filter(
        user=user, role__in=[GroupMembership.Role.LEADER, GroupMembership.Role.MODERATOR],
    ).exists()
@login_required
@require_POST
def group_post_pin(request, slug, pk):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)

    if not _is_group_manager(request.user, group):
        messages.error(request, "Chỉ trưởng nhóm hoặc phó nhóm được ghim bài.")
        return redirect(group.get_absolute_url())

    # Chỉ xử lý bài đã xuất bản và thuộc đúng nhóm trên URL.
    post = get_object_or_404(
        GroupPost, pk=pk, group=group, status=ContentStatus.PUBLISHED
    )

    action = request.POST.get("action")
    if action not in ("pin", "unpin"):
        messages.error(request, "Thao tác ghim bài không hợp lệ.")
        return redirect(group.get_absolute_url())

    post.is_pinned = action == "pin"
    post.save(update_fields=["is_pinned"])

    if post.is_pinned:
        messages.success(request, "Đã ghim bài lên đầu tab Thảo luận.")
    else:
        messages.success(request, "Đã bỏ ghim bài viết.")

    return redirect(group.get_absolute_url())

@login_required
def group_edit(request, slug):
    group = get_object_or_404(Group, slug=slug)
    is_leader = group.memberships.filter(user=request.user, role=GroupMembership.Role.LEADER).exists()
    if not (is_leader or request.user.role == User.Role.STAFF):
        messages.error(request, "Bạn không có quyền sửa thông tin nhóm này.")
        return redirect(group.get_absolute_url())
    if request.method == "POST":
        form = GroupEditForm(request.POST, request.FILES, instance=group)
        if form.is_valid():
            services.update_group_info(
                group, form.cleaned_data["name"], form.cleaned_data["description"],
                logo=form.cleaned_data.get("logo"), cover_image=form.cleaned_data.get("cover_image"),
                visibility=form.cleaned_data["visibility"],
            )
            messages.success(request, "Đã cập nhật thông tin nhóm.")
            return redirect(group.get_absolute_url())
    else:
        form = GroupEditForm(instance=group)
    return render(request, "groups/group_edit_form.html", {"form": form, "group": group})

@login_required
def group_recruitment_edit(request, slug):
    group = get_object_or_404(Group, slug=slug)

    is_leader = group.memberships.filter(
        user=request.user,
        role=GroupMembership.Role.LEADER,
    ).exists()

    if not (is_leader or request.user.role == User.Role.STAFF):
        messages.error(
            request,
            "Bạn không có quyền quản lý tuyển thành viên của nhóm này.",
        )
        return redirect(group.get_absolute_url())

    if request.method == "POST":
        form = GroupRecruitmentForm(request.POST, instance=group)

        if form.is_valid():
            recruitment = form.save(commit=False)
            recruitment.save(update_fields=[
                "is_recruiting",
                "recruit_positions",
                "recruit_deadline",
                "updated_at",
            ])

            messages.success(
                request,
                "Đã cập nhật thông tin tuyển thành viên.",
            )
            return redirect(group.get_absolute_url())
    else:
        form = GroupRecruitmentForm(instance=group)

    return render(
        request,
        "groups/group_recruitment_form.html",
        {"form": form, "group": group},
    )

@user_passes_test(_is_staff, login_url="home")
def group_disable(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if request.method == "POST":
        form = GroupDisableForm(request.POST)
        if form.is_valid():
            try:
                services.disable_group(group, request.user, form.cleaned_data["reason"])
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Đã vô hiệu hóa nhóm '{group.name}'.")
            return redirect(group.get_absolute_url())
    else:
        form = GroupDisableForm()
    return render(request, "groups/group_disable_form.html", {"form": form, "group": group})


@user_passes_test(_is_staff, login_url="home")
def group_enable(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.DISABLED)
    services.enable_group(group, request.user)
    messages.success(request, f"Đã kích hoạt lại nhóm '{group.name}'.")
    return redirect(group.get_absolute_url())


@login_required
def group_member_add(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền mời thành viên.")
        return redirect(group.get_absolute_url())
    if request.method == "POST":
        form = InviteMemberForm(request.POST)
        if form.is_valid():
            try:
                services.invite_member(group, form.cleaned_data["email"], request.user)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Đã thêm thành viên vào nhóm.")
    return redirect(group.get_absolute_url())


@login_required
def group_member_bulk_invite(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền mời thành viên.")
        return redirect(group.get_absolute_url())
    if request.method == "POST":
        form = BulkInviteMemberForm(request.POST, request.FILES)
        if form.is_valid():
            added, skipped = services.invite_members_bulk(group, form.cleaned_data["csv_file"], request.user)
            if added:
                messages.success(request, f"Đã thêm {len(added)} thành viên: {', '.join(added)}.")
            if skipped:
                detail = "; ".join(f"{email} ({reason})" for email, reason in skipped)
                messages.warning(request, f"Bỏ qua {len(skipped)} dòng: {detail}")
            if not added and not skipped:
                messages.warning(request, "Tệp CSV không có dòng email nào hợp lệ.")
        else:
            for error in form.errors.get("csv_file", []):
                messages.error(request, error)
    return redirect(group.get_absolute_url())


@login_required
def group_join_request_create(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if request.method == "POST":
        form = GroupJoinRequestForm(request.POST)
        if form.is_valid():
            try:
                services.request_to_join_group(group, request.user, form.cleaned_data["message"])
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Đã gửi yêu cầu tham gia nhóm, chờ trưởng/phó nhóm xét duyệt.")
    return redirect(group.get_absolute_url())


@login_required
def group_join_request_approve(request, slug, request_id):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền duyệt yêu cầu tham gia nhóm.")
        return redirect(group.get_absolute_url())
    join_request = get_object_or_404(GroupJoinRequest, pk=request_id, group=group)
    if request.method == "POST":
        try:
            services.approve_join_request(join_request, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Đã chấp nhận {join_request.user} vào nhóm.")
    return redirect(group.get_absolute_url())


@login_required
def group_join_request_reject(request, slug, request_id):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền từ chối yêu cầu tham gia nhóm.")
        return redirect(group.get_absolute_url())
    join_request = get_object_or_404(GroupJoinRequest, pk=request_id, group=group)
    if request.method == "POST":
        form = JoinRequestRejectForm(request.POST)
        if form.is_valid():
            try:
                services.reject_join_request(join_request, request.user, form.cleaned_data["reason"])
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Đã từ chối yêu cầu tham gia nhóm của {join_request.user}.")
                return redirect(group.get_absolute_url())
    else:
        form = JoinRequestRejectForm()
    return render(
        request, "groups/join_request_reject_form.html", {"form": form, "group": group, "join_request": join_request},
    )


@login_required
def group_member_remove(request, slug, user_id):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền xóa thành viên.")
        return redirect(group.get_absolute_url())
    target = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        try:
            services.remove_member(group, target)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Đã xóa {target} khỏi nhóm.")
    return redirect(group.get_absolute_url())


@login_required
def group_member_role(request, slug, user_id):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if not _is_group_manager(request.user, group):
        messages.error(request, "Bạn không có quyền đổi vai trò thành viên.")
        return redirect(group.get_absolute_url())
    target = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        form = MemberRoleForm(request.POST)
        if form.is_valid():
            try:
                services.change_member_role(group, target, form.cleaned_data["role"])
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Đã đổi vai trò của {target}.")
    return redirect(group.get_absolute_url())


@login_required
def group_leave(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    if request.method == "POST":
        try:
            services.leave_group(group, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Bạn đã rời nhóm.")
            return redirect("groups:list")
    return redirect(group.get_absolute_url())


@login_required
def group_transfer_leadership(request, slug):
    group = get_object_or_404(Group, slug=slug, status=Group.Status.ACTIVE)
    is_leader = group.memberships.filter(user=request.user, role=GroupMembership.Role.LEADER).exists()
    if not is_leader:
        messages.error(request, "Chỉ trưởng nhóm mới có quyền chuyển quyền trưởng nhóm.")
        return redirect(group.get_absolute_url())
    if request.method == "POST":
        form = TransferLeadershipForm(request.POST)
        if form.is_valid():
            target = get_object_or_404(User, pk=form.cleaned_data["new_leader_id"])
            try:
                services.transfer_leadership(group, target)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Đã chuyển quyền trưởng nhóm cho {target}.")
    return redirect(group.get_absolute_url())


class GroupPostDetailView(DetailView):
    model = GroupPost
    template_name = "groups/group_post_detail.html"
    context_object_name = "post"
    pk_url_kwarg = "pk"

    def get_queryset(self):
        return GroupPost.objects.filter(group__slug=self.kwargs["slug"])

    def get(self, request, *args, **kwargs):
        group = get_object_or_404(Group, slug=self.kwargs["slug"])
        if group.is_private and not _is_group_member(request.user, group) and not (
            request.user.is_authenticated and request.user.role == User.Role.STAFF
        ):
            messages.error(request, "Đây là nhóm riêng tư, chỉ thành viên mới xem được bài viết.")
            return redirect(group.get_absolute_url())
        return super().get(request, *args, **kwargs)

    def get_object(self, queryset=None):
        from interactions.utils import increment_view_count

        obj = super().get_object(queryset)
        increment_view_count(obj)
        return obj

    def get_context_data(self, **kwargs):
        from django.contrib.contenttypes.models import ContentType

        from interactions.utils import get_interaction_context, get_top_level_comments

        ctx = super().get_context_data(**kwargs)
        content_type = ContentType.objects.get_for_model(GroupPost)
        ctx["model_key"] = "grouppost"
        ctx["comments"] = get_top_level_comments(content_type, self.object.pk, viewer=self.request.user)
        ctx.update(get_interaction_context(self.request.user, self.object))

        from .mentions import build_mention_feed

        ctx["mention_feed"] = build_mention_feed(self.object.group)
        ctx["documents"] = get_documents(self.object)
        return ctx
