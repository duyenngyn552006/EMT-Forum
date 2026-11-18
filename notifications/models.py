from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Notification(models.Model):
    class Verb(models.TextChoices):
        COMMENT = "comment", "Có bình luận mới"
        LIKE = "like", "Có lượt thích mới"
        NEW_ANNOUNCEMENT = "new_announcement", "Thông báo mới"
        GROUP_APPROVED = "group_approved", "Yêu cầu tạo nhóm được duyệt"
        GROUP_REJECTED = "group_rejected", "Yêu cầu tạo nhóm bị từ chối"
        GROUP_INVITATION = "group_invitation", "Lời mời tham gia nhóm"
        GROUP_JOIN_REQUEST = "group_join_request", "Có yêu cầu xin tham gia nhóm"
        GROUP_JOIN_APPROVED = "group_join_approved", "Yêu cầu tham gia nhóm được duyệt"
        GROUP_JOIN_REJECTED = "group_join_rejected", "Yêu cầu tham gia nhóm bị từ chối"
        GROUP_LEADERSHIP_TRANSFERRED = "group_leadership_transferred", "Trở thành trưởng nhóm mới"
        MODERATION = "moderation", "Cập nhật kiểm duyệt"
        APPEAL_RESULT = "appeal_result", "Kết quả kháng nghị"
        MENTION = "mention", "Được gắn thẻ"
        REPLY = "reply", "Trả lời bình luận"
        ANSWER_ACCEPTED = "answer_accepted", "Câu trả lời được chọn"
        FOLLOW_UPDATE = "follow_update", "Cập nhật theo dõi"
        DEADLINE_REMINDER = "deadline_reminder", "Nhắc hạn chót"
        EVENT_REGISTRATION = "event_registration", "Đăng ký sự kiện mới"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    verb = models.CharField(max_length=30, choices=Verb.choices)
    message = models.CharField(max_length=255, blank=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.PositiveIntegerField(null=True, blank=True)
    content_object = GenericForeignKey("content_type", "object_id")

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_verb_display()} -> {self.recipient}"


class NotificationPreference(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_preference")
    notify_on_comment = models.BooleanField(default=True)
    notify_on_reply = models.BooleanField(default=True)
    notify_on_like = models.BooleanField(default=True)
    notify_on_follow = models.BooleanField(default=True)
    notify_on_announcement = models.BooleanField(default=True)
    notify_on_deadline = models.BooleanField(default=True)
    notify_on_group_activity = models.BooleanField(default=True)
    notify_on_moderation = models.BooleanField(default=True)
    email_digest = models.BooleanField(default=False)

    def __str__(self):
        return f"Cài đặt thông báo của {self.user}"


class DeadlineReminderLog(models.Model):
    """Ghi nhan moi lan da xu ly nhac han cho tung sinh vien, tranh gui trung."""

    class Stage(models.TextChoices):
        TWO_DAYS = "two_days", "Trước 2 ngày"
        ONE_DAY = "one_day", "Trước 1 ngày"
        THREE_HOURS = "three_hours", "Trước 3 giờ"

    announcement = models.ForeignKey(
        "announcements.Announcement", on_delete=models.CASCADE, related_name="deadline_reminder_logs"
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deadline_reminder_logs"
    )
    stage = models.CharField(max_length=20, choices=Stage.choices)
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "recipient", "stage"],
                name="unique_announcement_deadline_reminder",
            )
        ]
        ordering = ["-sent_at"]

    def __str__(self):
        return f"{self.announcement_id} - {self.recipient_id} - {self.stage}"


class DeadlineCompletion(models.Model):
    """Sinh vien danh dau da hoan thanh cong viec lien quan den mot han chot."""

    announcement = models.ForeignKey(
        "announcements.Announcement", on_delete=models.CASCADE, related_name="deadline_completions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="deadline_completions"
    )
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["announcement", "user"],
                name="unique_announcement_deadline_completion",
            )
        ]

    def __str__(self):
        return f"{self.user_id} completed {self.announcement_id}"
