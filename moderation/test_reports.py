"""Kiem thu cac tinh nang bao cao vi pham bo sung: M1 (danh muc ly do), M2 (bao cao binh luan,
chong trung, gop bao cao), M3 (thong bao ket qua xu ly)."""
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from config.mixins import ContentStatus
from forum.models import Category, ForumPost
from interactions.models import Comment
from notifications.models import Notification

from .models import ModerationLog, Report


class ReportFeatureTestBase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="rp-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.author = User.objects.create_user(email="rp-author@due.udn.vn", password="Pass1234!")
        self.reporter1 = User.objects.create_user(email="rp-r1@due.udn.vn", password="Pass1234!")
        self.reporter2 = User.objects.create_user(email="rp-r2@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chung")
        self.post = ForumPost.objects.create(
            author=self.author, category=self.category, title="Bai bi bao cao",
            body="noi dung", status=ContentStatus.PUBLISHED,
        )
        self.report_url = reverse("moderation:create_report", args=["forumpost", self.post.pk])

    def _report(self, user, category=Report.Category.SPAM, reason=""):
        self.client.force_login(user)
        return self.client.post(self.report_url, {"category": category, "reason": reason})


class ReportCategoryTests(ReportFeatureTestBase):
    """M1 - nguoi bao cao chon loai vi pham, 'Khac' bat buoc mo ta, Giao vu loc theo loai."""

    def test_form_shows_all_categories(self):
        self.client.force_login(self.reporter1)
        response = self.client.get(self.report_url)
        for _value, label in Report.Category.choices:
            self.assertContains(response, label)

    def test_report_saved_with_category(self):
        self._report(self.reporter1, Report.Category.HARASSMENT)
        report = Report.objects.get()
        self.assertEqual(report.category, Report.Category.HARASSMENT)
        self.assertEqual(report.reason, "")

    def test_other_requires_description(self):
        response = self._report(self.reporter1, Report.Category.OTHER, reason="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Report.objects.exists())
        self._report(self.reporter1, Report.Category.OTHER, reason="Dang bai trung lap")
        self.assertEqual(Report.objects.count(), 1)

    def test_staff_can_filter_by_category(self):
        self._report(self.reporter1, Report.Category.SPAM)
        other_post = ForumPost.objects.create(
            author=self.author, category=self.category, title="Bai thu hai", body="x", status=ContentStatus.PUBLISHED,
        )
        self.client.force_login(self.reporter2)
        self.client.post(reverse("moderation:create_report", args=["forumpost", other_post.pk]),
                         {"category": Report.Category.COPYRIGHT})
        self.client.force_login(self.staff)
        response = self.client.get(reverse("moderation:report_list"), {"category": Report.Category.COPYRIGHT})
        self.assertContains(response, "Bai thu hai")
        self.assertNotContains(response, "Bai bi bao cao")


class ReportDedupAndGroupingTests(ReportFeatureTestBase):
    """M2 - khong bao cao trung, gop bao cao cung noi dung, bao cao duoc binh luan."""

    def test_same_user_cannot_report_twice_while_open(self):
        self._report(self.reporter1)
        response = self._report(self.reporter1, Report.Category.HARASSMENT)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(response.status_code, 302)

    def test_reports_on_same_content_are_grouped(self):
        self._report(self.reporter1, Report.Category.SPAM)
        self._report(self.reporter2, Report.Category.HARASSMENT)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("moderation:report_list"))
        self.assertEqual(len(response.context["groups"]), 1)
        self.assertContains(response, "2 người báo cáo")

    def test_resolving_one_report_closes_all_reports_of_content(self):
        self._report(self.reporter1)
        self._report(self.reporter2)
        first = Report.objects.order_by("pk").first()
        self.client.force_login(self.staff)
        self.client.post(reverse("moderation:report_resolve", args=[first.pk]),
                         {"action": "hide", "resolution_note": "Spam quang cao"})
        self.assertFalse(Report.objects.filter(status__in=Report.OPEN_STATUSES).exists())
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ContentStatus.HIDDEN)
        self.assertTrue(ModerationLog.objects.filter(action=ModerationLog.Action.HIDE, target_user=self.author).exists())

    def test_comment_can_be_reported_from_post_page(self):
        comment = Comment.objects.create(
            author=self.author, content_type=ContentType.objects.get_for_model(ForumPost),
            object_id=self.post.pk, body="binh luan xuc pham",
        )
        self.client.force_login(self.reporter1)
        page = self.client.get(self.post.get_absolute_url())
        report_url = reverse("moderation:create_report", args=["comment", comment.pk])
        self.assertContains(page, report_url)
        response = self.client.post(report_url, {"category": Report.Category.HARASSMENT})
        self.assertRedirects(response, f"{self.post.get_absolute_url()}#comment-bubble-{comment.pk}",
                             fetch_redirect_response=False)
        self.assertEqual(Report.objects.get().content_object, comment)

    def test_author_does_not_see_report_link_on_own_comment(self):
        comment = Comment.objects.create(
            author=self.author, content_type=ContentType.objects.get_for_model(ForumPost),
            object_id=self.post.pk, body="binh luan cua tac gia",
        )
        self.client.force_login(self.author)
        page = self.client.get(self.post.get_absolute_url())
        self.assertNotContains(page, reverse("moderation:create_report", args=["comment", comment.pk]))


class ReportResultNotificationTests(ReportFeatureTestBase):
    """M3 - bao ket qua cho nguoi bao cao; bao tac gia khi noi dung bi an/xoa."""

    def _resolve(self, action):
        report = Report.objects.first()
        self.client.force_login(self.staff)
        self.client.post(reverse("moderation:report_resolve", args=[report.pk]),
                         {"action": action, "resolution_note": "Da kiem tra"})

    def test_every_reporter_is_notified(self):
        self._report(self.reporter1)
        self._report(self.reporter2)
        self._resolve("delete")
        for user in (self.reporter1, self.reporter2):
            note = Notification.objects.get(recipient=user)
            self.assertIn("đã được xử lý", note.message)
            self.assertIn("đã bị xóa", note.message)

    def test_author_notified_with_reason_when_hidden(self):
        self._report(self.reporter1, Report.Category.SPAM)
        self._resolve("hide")
        note = Notification.objects.get(recipient=self.author)
        self.assertIn("bị tạm ẩn", note.message)
        self.assertIn("Spam, quảng cáo", note.message)

    def test_author_not_notified_when_dismissed(self):
        self._report(self.reporter1)
        self._resolve("dismiss")
        self.assertFalse(Notification.objects.filter(recipient=self.author).exists())
        self.assertIn("không phát hiện vi phạm", Notification.objects.get(recipient=self.reporter1).message)
