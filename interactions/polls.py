"""Quy tac nghiep vu cho binh chon (Poll) - gan vao ForumPost/GroupPost.

Chi tac gia bai viet tao duoc poll, va chi luc dang bai (xem forum/views.py
PostCreateView, groups/views.py group_post_create) - khong con thao tac "them
binh chon" rieng sau khi bai da ton tai. Sinh vien thuong chi duoc binh chon.
Bai trong nhom chi thanh vien nhom moi duoc binh chon.
"""
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count

from .models import Poll, PollOption


def get_poll(obj):
    content_type = ContentType.objects.get_for_model(obj)
    return Poll.objects.filter(content_type=content_type, object_id=obj.pk).first()


def _is_group_manager(user, group):
    from groups.models import GroupMembership

    return group.memberships.filter(
        user=user, role__in=[GroupMembership.Role.LEADER, GroupMembership.Role.MODERATOR],
    ).exists()


def can_vote(user, poll):
    """Dang nhap + poll chua dong + (bai nhom -> phai la thanh vien nhom)."""
    if not user.is_authenticated or poll.is_closed:
        return False
    target = poll.content_object
    if target is not None and target._meta.model_name == "grouppost":
        return user.role == "staff" or target.group.memberships.filter(user=user).exists()
    return True


def can_delete_poll(user, poll):
    return user.is_authenticated and (user == poll.created_by or user.role == "staff")


def validate_poll_fields(poll_form):
    """Goi SAU KHI poll_form.is_valid(). Tra ve SensitiveKeyword neu cau hoi/lua chon dinh
    tu khoa nhay cam (khong co trang thai cho duyet rieng cho poll - vi pham la tu choi
    luon ca bai dang kem theo), None neu sach."""
    from moderation.services import find_sensitive_keyword

    return find_sensitive_keyword(poll_form.cleaned_data["question"], *poll_form.cleaned_data["options"])


def create_poll_from_form(poll_form, obj, user):
    """Tao Poll+PollOption cho 1 bai vua dang - goi SAU KHI validate_poll_fields() tra ve None."""
    poll = Poll.objects.create(
        content_type=ContentType.objects.get_for_model(obj), object_id=obj.pk,
        question=poll_form.cleaned_data["question"], closes_at=poll_form.cleaned_data["closes_at"],
        created_by=user,
    )
    PollOption.objects.bulk_create(
        [PollOption(poll=poll, text=text, position=i) for i, text in enumerate(poll_form.cleaned_data["options"])]
    )
    return poll


def build_poll_context(user, obj):
    """Du lieu hien thi poll cho partial _poll.html (poll=None neu bai chua co)."""
    poll = get_poll(obj)
    context = {"poll": None}
    if poll is None:
        return context

    options = list(poll.options.annotate(vote_count=Count("votes")))
    total = sum(o.vote_count for o in options)
    my_vote = poll.votes.filter(user=user).first() if user.is_authenticated else None
    for option in options:
        option.percent = round(option.vote_count * 100 / total) if total else 0
        option.is_mine = my_vote is not None and my_vote.option_id == option.pk
    context["poll"] = poll
    context["poll_options"] = options
    context["poll_total_votes"] = total
    context["poll_my_option_id"] = my_vote.option_id if my_vote else None
    context["poll_can_vote"] = can_vote(user, poll)
    context["poll_can_delete"] = can_delete_poll(user, poll)
    return context
