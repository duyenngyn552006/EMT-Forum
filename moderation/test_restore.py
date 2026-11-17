"""Kiem thu chuc nang Noi dung da an & Khoi phuc: an la xu ly tam thoi, Giao vu khoi phuc duoc
sau khi xac minh; xoa la vinh vien, khong khoi phuc."""
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from config.mixins import ContentStatus
from forum.models import Category, ForumPost
from interactions.models import Comment
from notifications.models import Notification

from .models import ModerationLog, Report


class RestoreHiddenContentTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="rs-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.author = User.objects.create_user(email="rs-author@due.udn.vn", password="Pass1234!")
        self.reporter = User.objects.create_user(email="rs-rep@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="Chung")
        self.post = ForumPost.objects.create(
            author=self.author, category=category, title="Lich thi thay doi", body="x", status=ContentStatus.PUBLISHED,
        )
        self.ct = ContentType.objects.get_for_model(ForumPost)

    def _hide_via_report(self):
        report = Report.objects.create(reporter=self.reporter, content_type=self.ct, object_id=self.post.pk,
                                       category=Report.Category.MISINFORMATION)
        self.client.force_login(self.staff)
        self.client.post(reverse("moderation:report_resolve", args=[report.pk]),
                         {"action": "hide", "resolution_note": "An tam de xac minh voi phong Dao tao"})
        self.post.refresh_from_db()

    def test_hidden_post_listed_with_reason(self):
        self._hide_via_report()
        response = self.client.get(reverse("moderation:hidden_content_list"))
        self.assertContains(response, "Lich thi thay doi")
        self.assertContains(response, "An tam de xac minh voi phong Dao tao")
        self.assertContains(response, reverse("moderation:restore_content", args=["forumpost", self.post.pk]))

    def test_restore_publishes_logs_and_notifies_author(self):
        self._hide_via_report()
        Notification.objects.all().delete()
        response = self.client.post(reverse("moderation:restore_content", args=["forumpost", self.post.pk]),
                                    {"note": "Da xac minh dung"})
        self.assertRedirects(response, reverse("moderation:hidden_content_list"))
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ContentStatus.PUBLISHED)
        log = ModerationLog.objects.get(action=ModerationLog.Action.RESTORE)
        self.assertEqual(log.target_user, self.author)
        self.assertEqual(log.reason, "Da xac minh dung")
        note = Notification.objects.get(recipient=self.author)
        self.assertIn("đã được khôi phục", note.message)

    def test_restore_hidden_comment(self):
        comment = Comment.objects.create(author=self.author, content_type=self.ct, object_id=self.post.pk,
                                         body="binh luan", status=Comment.Status.HIDDEN)
        self.client.force_login(self.staff)
        self.client.post(reverse("moderation:restore_content", args=["comment", comment.pk]))
        comment.refresh_from_db()
        self.assertEqual(comment.status, Comment.Status.PUBLISHED)

    def test_removed_content_cannot_be_restored(self):
        self.post.status = ContentStatus.REMOVED
        self.post.save()
        self.client.force_login(self.staff)
        self.client.post(reverse("moderation:restore_content", args=["forumpost", self.post.pk]))
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ContentStatus.REMOVED)
        self.assertFalse(ModerationLog.objects.filter(action=ModerationLog.Action.RESTORE).exists())

    def test_only_staff_and_post_only(self):
        self.post.status = ContentStatus.HIDDEN
        self.post.save()
        url = reverse("moderation:restore_content", args=["forumpost", self.post.pk])
        self.client.force_login(self.author)
        self.client.post(url)
        self.post.refresh_from_db()
        self.assertEqual(self.post.status, ContentStatus.HIDDEN)
        self.assertEqual(self.client.get(reverse("moderation:hidden_content_list")).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_sidebar_shows_hidden_count(self):
        self.post.status = ContentStatus.HIDDEN
        self.post.save()
        self.client.force_login(self.staff)
        response = self.client.get(reverse("moderation:hidden_content_list"))
        self.assertContains(response, 'aria-label="1 nội dung đang ẩn"')
        self.assertContains(response, f'class="mod-side-link active" href="{reverse("moderation:hidden_content_list")}"')
