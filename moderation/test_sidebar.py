"""Kiem thu thanh dieu huong Trung tam kiem duyet (M6)."""
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from config.mixins import ContentStatus
from forum.models import Category, ForumPost
from interactions.models import Comment

from .models import Report
from .services import pending_counts


class ModerationSidebarTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="sb-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.student = User.objects.create_user(email="sb-st@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="Chung")
        self.post = ForumPost.objects.create(
            author=self.student, category=category, title="Bai", body="x", status=ContentStatus.PUBLISHED,
        )
        ct = ContentType.objects.get_for_model(ForumPost)
        Report.objects.create(reporter=self.student, content_type=ct, object_id=self.post.pk, category=Report.Category.SPAM)
        Comment.objects.create(author=self.student, content_type=ct, object_id=self.post.pk, body="cho duyet",
                               status=Comment.Status.PENDING_REVIEW)

    def test_pending_counts(self):
        self.assertEqual(pending_counts(), {"reports": 1, "content": 1, "appeals": 0})

    def test_sidebar_on_moderation_pages_with_counts_and_active_item(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("moderation:report_list"))
        self.assertContains(response, "Trung tâm kiểm duyệt")
        self.assertContains(response, "<strong>2</strong> việc đang chờ xử lý", html=False)
        self.assertContains(
            response,
            f'class="mod-side-link active" href="{reverse("moderation:report_list")}" aria-current="page"',
        )
        self.assertContains(response, 'aria-label="1 đang chờ"', count=2)  # bao cao + noi dung cho duyet

    def test_every_staff_page_has_sidebar(self):
        self.client.force_login(self.staff)
        for name in ("pending_content_list", "appeal_list", "keyword_list", "log_list", "keyword_create"):
            response = self.client.get(reverse(f"moderation:{name}"))
            self.assertContains(response, 'class="mod-side"', msg_prefix=name)
        response = self.client.get(reverse("dashboard:overview"))
        self.assertContains(response, f'class="mod-side-link active" href="{reverse("dashboard:overview")}"')

    def test_user_facing_report_form_has_no_sidebar(self):
        self.client.force_login(self.student)
        other = User.objects.create_user(email="sb-other@due.udn.vn", password="Pass1234!")
        self.client.force_login(other)
        response = self.client.get(reverse("moderation:create_report", args=["forumpost", self.post.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="mod-side"')
