from allauth.account.adapter import DefaultAccountAdapter

LOCKED_ACCOUNT_SESSION_KEY = "locked_account_id"


class LockedAccountAdapter(DefaultAccountAdapter):
    """Mac dinh allauth chan dang nhap tai khoan is_active=False bang 1 trang chung
    chung (khong biet dang nhap boi ai). Luu tam user.pk vao session o day de trang
    "Tai khoan bi khoa" (accounts.views.account_inactive_view) hien duoc ly do/thoi
    diem khoa + cho phep gui khang nghi ngay (nguoi dung khong the dang nhap binh
    thuong de vao duoc @login_required cua moderation.views.appeal_create)."""

    def respond_user_inactive(self, request, user):
        request.session[LOCKED_ACCOUNT_SESSION_KEY] = user.pk
        return super().respond_user_inactive(request, user)
