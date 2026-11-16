"""Kiem thu D1 (tab Kiem duyet tren Dashboard) va D5 (xuat so lieu ra file Excel)."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from config.mixins import ContentStatus
from forum.models import Category, ForumPost
from moderation.models import ModerationLog, Report


class ModerationTabTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="mt-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.reporter = User.objects.create_user(email="mt-r@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="Chung")
        self.post = ForumPost.objects.create(
            author=self.reporter, category=category, title="Bai", body="x", status=ContentStatus.PUBLISHED,
        )
        from django.contrib.contenttypes.models import ContentType
        ct = ContentType.objects.get_for_model(ForumPost)
        now = timezone.now()
        self.open_report = Report.objects.create(
            reporter=self.reporter, content_type=ct, object_id=self.post.pk, category=Report.Category.SPAM,
        )
        handled = Report.objects.create(
            reporter=self.reporter, content_type=ct, object_id=self.post.pk, category=Report.Category.HARASSMENT,
            status=Report.Status.DISMISSED, handled_by=self.staff, handled_at=now,
        )
        Report.objects.filter(pk=handled.pk).update(created_at=now - timedelta(hours=3))
        ModerationLog.objects.create(actor=None, action=ModerationLog.Action.WARN, reason="tu khoa")
        ModerationLog.objects.create(actor=self.staff, action=ModerationLog.Action.APPROVE_CONTENT, reason="ok")
        self.client.force_login(self.staff)

    def test_tab_shows_moderation_numbers(self):
        response = self.client.get(reverse("dashboard:overview"), {"tab": "moderation"})
        self.assertEqual(response.status_code, 200)
        mod = response.context["mod"]
        self.assertEqual(mod["new_reports"], 2)
        self.assertEqual(mod["handled"], 1)
        self.assertEqual(mod["dismissed"], 1)
        self.assertEqual(mod["auto_held"], 1)
        self.assertAlmostEqual(mod["avg_hours"], 3, delta=0.1)
        self.assertContains(response, "Báo cáo theo loại vi phạm")
        self.assertContains(response, "Xúc phạm, quấy rối")

    def test_staff_activity_table(self):
        response = self.client.get(reverse("dashboard:overview"), {"tab": "moderation"})
        row = response.context["mod"]["activity_rows"][0]
        self.assertEqual(row["actor"], self.staff)
        self.assertEqual(row["total"], 2)  # 1 bao cao da xu ly + 1 lan duyet noi dung

    def test_period_links_keep_tab(self):
        response = self.client.get(reverse("dashboard:overview"), {"tab": "moderation", "period": "year", "year": "2025"})
        self.assertContains(response, 'href="?period=year&amp;year=2024&amp;tab=moderation"')
        self.assertContains(response, 'name="tab" value="moderation"')

    def test_overview_tab_unchanged_by_default(self):
        response = self.client.get(reverse("dashboard:overview"))
        self.assertIn("chart", response.context)
        self.assertNotIn("mod", response.context)

    def test_student_cannot_open_tab_or_export(self):
        self.client.force_login(self.reporter)
        self.assertEqual(self.client.get(reverse("dashboard:overview"), {"tab": "moderation"}).status_code, 302)
        self.assertEqual(self.client.get(reverse("dashboard:export_csv")).status_code, 302)


class ExportTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="ex-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.client.force_login(self.staff)

    def test_export_is_utf16_tab_separated_with_all_sections(self):
        response = self.client.get(reverse("dashboard:export_csv"), {"period": "month", "year": "2026", "month": "2"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("emt-forum-thong-ke-month-2026-02-01.csv", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"\xff\xfe"))  # BOM UTF-16 LE -> Excel nhan dung tieng Viet
        text = response.content.decode("utf-16")
        for heading in ("CHỈ SỐ", "THEO THỜI GIAN", "NGƯỜI DÙNG THEO VAI TRÒ", "KIỂM DUYỆT", "BÁO CÁO THEO LOẠI VI PHẠM"):
            self.assertIn(heading, text)
        self.assertIn("Kỳ thống kê\tTháng 2/2026", text)
        # Thang 2/2026 co 28 ngay -> 28 dong theo thoi gian
        time_rows = text.split("THEO THỜI GIAN")[1].split("NGƯỜI DÙNG")[0].strip().splitlines()[1:]
        self.assertEqual(len([r for r in time_rows if r.strip()]), 28)
