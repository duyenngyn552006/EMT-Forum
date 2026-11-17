"""Kiem thu UC-8.16 Kiem duyet noi dung vi pham: bien phap Canh bao nguoi dang, lich su vi pham
90 ngay, va ngoai le 3a (noi dung da bi nguoi dang xoa)."""
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from config.mixins import ContentStatus
from forum.models import Category, ForumPost
from notifications.models import Notification

from .models import ModerationLog, Report


class WarnMeasureTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="w-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.author = User.objects.create_user(email="w-author@due.udn.vn", password="Pass1234!")
        self.reporter = User.objects.create_user(email="w-rep@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chuyen muc test canh bao")
        self.post = ForumPost.objects.create(
            author=self.author, category=self.category, title="Cho minh xin de cuong on tap",
            body="x", status=ContentStatus.PUBLISHED,
        )
        self.ct = ContentType.objects.get_for_model(ForumPost)
        self.report = Report.objects.create(reporter=self.reporter, content_type=self.ct, object_id=self.post.pk,
                                            category=Report.Category.INAPPROPRIATE)
        self.url = reverse("moderation:report_resolve", args=[self.report.pk])
        self.client.force_login(self.staff)

    def _resolve(self, action, note="Bai hoi tai lieu nen dang o chuyen muc Hoc tap"):
        return self.client.post(self.url, {"action": action, "resolution_note": note})

    def test_warn_keeps_content_and_logs_warning(self):
        self._resolve("warn")
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ContentStatus.PUBLISHED)
        log = ModerationLog.objects.get(action=ModerationLog.Action.WARN)
        self.assertEqual(log.actor, self.staff)
        self.assertEqual(log.target_user, self.author)
        self.assertEqual(log.content_object, self.post)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Report.Status.RESOLVED)

    def test_warn_notifies_author_and_reporter(self):
        self._resolve("warn")
        author_note = Notification.objects.get(recipient=self.author)
        self.assertIn("Bạn bị cảnh báo", author_note.message)
        self.assertIn("Không phù hợp hoặc sai chuyên mục", author_note.message)
        self.assertIn("Nội dung vẫn được giữ lại", author_note.message)
        self.assertIn("đã gửi cảnh báo đến người đăng", Notification.objects.get(recipient=self.reporter).message)

    def test_resolve_page_shows_violation_history(self):
        response = self.client.get(self.url)
        self.assertContains(response, "chưa bị xử lý vi phạm lần nào")
        ModerationLog.objects.create(actor=self.staff, action=ModerationLog.Action.WARN,
                                     target_user=self.author, reason="lan truoc")
        ModerationLog.objects.create(actor=None, action=ModerationLog.Action.WARN,
                                     target_user=self.author, reason="bo loc tu dong khong tinh")
        response = self.client.get(self.url)
        self.assertContains(response, "đã bị cảnh báo <strong>1 lần</strong>", html=False)
        self.assertContains(response, f"?user={self.author.pk}")

    def test_all_four_choices_offered(self):
        response = self.client.get(self.url)
        for label in ("Bỏ qua báo cáo", "Cảnh báo người đăng", "Tạm ẩn nội dung", "Xóa nội dung"):
            self.assertContains(response, label)

    def test_dashboard_counts_staff_warnings(self):
        self._resolve("warn")
        response = self.client.get(reverse("dashboard:overview"), {"tab": "moderation"})
        mod = response.context["mod"]
        self.assertIn("Cảnh báo người đăng", mod["activity_headers"])
        self.assertEqual(mod["auto_held"], 0)  # canh bao cua Giao vu khong tinh vao bo loc tu dong


class ContentMissingExceptionTests(TestCase):
    """UC-8.16 ngoai le 3a: noi dung da bi chinh nguoi dang xoa truoc khi Giao vu xu ly."""

    def setUp(self):
        self.staff = User.objects.create_user(email="m-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        author = User.objects.create_user(email="m-author@due.udn.vn", password="Pass1234!")
        reporter = User.objects.create_user(email="m-rep@due.udn.vn", password="Pass1234!")
        post = ForumPost.objects.create(author=author, category=Category.objects.create(name="C"),
                                        title="Bai se bi xoa", body="x", status=ContentStatus.PUBLISHED)
        self.report = Report.objects.create(reporter=reporter, content_type=ContentType.objects.get_for_model(ForumPost),
                                            object_id=post.pk, category=Report.Category.SPAM)
        post.delete()  # nguoi dang tu xoa bai
        self.url = reverse("moderation:report_resolve", args=[self.report.pk])
        self.client.force_login(self.staff)

    def test_page_explains_and_only_offers_dismiss(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Nội dung không còn tồn tại")
        self.assertNotContains(response, 'value="delete"')
        self.assertContains(response, 'value="dismiss"')

    def test_measure_is_cancelled_with_error(self):
        for action in ("warn", "hide", "delete"):
            response = self.client.post(self.url, {"action": action, "resolution_note": "x"}, follow=True)
            self.assertContains(response, "Nội dung không còn tồn tại")
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Report.Status.PENDING)
        self.assertFalse(ModerationLog.objects.exists())

    def test_report_can_still_be_closed(self):
        self.client.post(self.url, {"action": "dismiss", "resolution_note": "Nguoi dang da tu xoa bai"})
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Report.Status.DISMISSED)
