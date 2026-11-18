from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Notification


class NotificationUnreadFilterTests(TestCase):
    """Tab 'Chua doc' o trang thong bao phai loc that su, khong chi doi mau vien."""

    def setUp(self):
        self.user = User.objects.create_user(email="notif_filter@due.udn.vn", password="Pass1234!")
        self.read_notif = Notification.objects.create(
            recipient=self.user, verb=Notification.Verb.COMMENT, message="Da doc", is_read=True,
        )
        self.unread_notif = Notification.objects.create(
            recipient=self.user, verb=Notification.Verb.COMMENT, message="Chua doc", is_read=False,
        )

    def test_default_list_shows_all_notifications(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:list"))
        notifications = list(response.context["notifications"])
        self.assertIn(self.read_notif, notifications)
        self.assertIn(self.unread_notif, notifications)

    def test_unread_filter_hides_read_notifications(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("notifications:list"), {"filter": "unread"})
        notifications = list(response.context["notifications"])
        self.assertNotIn(self.read_notif, notifications)
        self.assertIn(self.unread_notif, notifications)


class MarkAllReadRedirectTests(TestCase):
    """Danh dau tat ca da doc bam tu dropdown chuong o BAT KY trang nao phai o lai
    trang do (qua tham so 'next'), khong bi chuyen thang sang /notifications/ nhu
    bam 'Xem tat ca' (tai dung mau _safe_next_url cua interactions.toggle_follow)."""

    def setUp(self):
        self.user = User.objects.create_user(email="mark_all@due.udn.vn", password="Pass1234!")
        Notification.objects.create(recipient=self.user, verb=Notification.Verb.COMMENT, is_read=False)
        self.client.force_login(self.user)

    def test_redirects_back_to_next_page_when_given(self):
        response = self.client.post(reverse("notifications:mark_all_read"), {"next": "/forum/"})
        self.assertRedirects(response, "/forum/")
        self.assertEqual(self.user.notifications.filter(is_read=False).count(), 0)

    def test_falls_back_to_notifications_list_without_next(self):
        response = self.client.post(reverse("notifications:mark_all_read"))
        self.assertRedirects(response, reverse("notifications:list"))

    def test_ignores_unsafe_external_next(self):
        response = self.client.post(reverse("notifications:mark_all_read"), {"next": "https://evil.example/"})
        self.assertRedirects(response, reverse("notifications:list"))


class DeadlineLogicTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        from announcements.models import Announcement

        self.now = timezone.now()
        self.admin = User.objects.create_user(
            email="dl_admin@due.udn.vn", password="Pass1234!", role=User.Role.STAFF,
        )
        self.student = User.objects.create_user(
            email="dl_student@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT,
            last_name="Trần", first_name="Bình",
            student_id="221121514999", class_name="48K22.2", phone="0905111222",
        )
        self.official_urgent = Announcement.objects.create(
            author=self.admin, kind=Announcement.Kind.OFFICIAL,
            title="Nộp học phí HK1", body="Nội dung", status="published",
            deadline_at=self.now + timedelta(hours=5),
        )
        self.event_soon = Announcement.objects.create(
            author=self.admin, kind=Announcement.Kind.EVENT,
            title="Hội thảo AI 2026", body="Nội dung", status="published",
            event_datetime=self.now + timedelta(days=5),
            deadline_at=self.now + timedelta(days=2),
        )
        self.expired_item = Announcement.objects.create(
            author=self.admin, kind=Announcement.Kind.OFFICIAL,
            title="Hạn chót tuần trước", body="Nội dung", status="published",
            deadline_at=self.now - timedelta(days=2),
        )

    def test_admin_has_no_deadline_page_or_links(self):
        self.client.force_login(self.admin)
        resp_dl = self.client.get(reverse("notifications:deadlines"))
        self.assertRedirects(resp_dl, reverse("notifications:list"))

        resp_list = self.client.get(reverse("announcements:list"))
        self.assertNotContains(resp_list, reverse("notifications:deadlines"))

        resp_notif = self.client.get(reverse("notifications:list"))
        self.assertNotContains(resp_notif, reverse("notifications:deadlines"))

    def test_student_sees_urgency_and_tabs(self):
        self.client.force_login(self.student)
        resp = self.client.get(reverse("notifications:deadlines"))
        self.assertEqual(resp.status_code, 200)
        items = resp.context["deadline_items"]
        self.assertEqual([e["announcement"].pk for e in items], [self.official_urgent.pk, self.event_soon.pk])
        self.assertEqual(items[0]["urgency"], "urgent")
        self.assertEqual(items[1]["urgency"], "soon")
        self.assertEqual(resp.context["expired_count"], 1)

        resp_exp = self.client.get(reverse("notifications:deadlines"), {"tab": "expired"})
        self.assertEqual([e["announcement"].pk for e in resp_exp.context["deadline_items"]], [self.expired_item.pk])

    def test_event_registration_automatically_completes_deadline(self):
        self.client.force_login(self.student)
        self.client.post(
            reverse("announcements:register_event", args=[self.event_soon.pk]),
            {
                "full_name": "Trần Bình",
                "student_id": "221121514999",
                "class_name": "48K22.2",
                "email": "dl_student@due.udn.vn",
                "phone": "0905111222",
                "note": "",
            },
        )
        resp = self.client.get(reverse("notifications:deadlines"), {"tab": "done"})
        items = resp.context["deadline_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["announcement"].pk, self.event_soon.pk)
        self.assertTrue(items[0]["is_registered"])
        self.assertTrue(items[0]["is_done"])

    def test_student_can_complete_and_undo_official_deadline(self):
        self.client.force_login(self.student)
        self.client.post(reverse("notifications:complete_deadline", args=[self.official_urgent.pk]))
        resp_done = self.client.get(reverse("notifications:deadlines"), {"tab": "done"})
        self.assertEqual([e["announcement"].pk for e in resp_done.context["deadline_items"]], [self.official_urgent.pk])

        self.client.post(reverse("notifications:uncomplete_deadline", args=[self.official_urgent.pk]))
        resp_pending = self.client.get(reverse("notifications:deadlines"), {"tab": "pending"})
        self.assertIn(self.official_urgent.pk, [e["announcement"].pk for e in resp_pending.context["deadline_items"]])
