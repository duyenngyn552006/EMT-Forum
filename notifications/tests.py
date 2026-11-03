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
