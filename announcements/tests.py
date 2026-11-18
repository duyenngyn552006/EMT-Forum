from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from . import services
from .models import Announcement


class AnnouncementViewCountTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="vc_staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.announcement, _ = services.create_announcement(
            self.staff, Announcement.Kind.OFFICIAL, "Thong bao test luot xem", "noi dung",
        )

    def test_detail_view_increments_view_count(self):
        self.assertEqual(self.announcement.view_count, 0)
        self.client.get(reverse("announcements:detail", args=[self.announcement.pk]))
        self.announcement.refresh_from_db()
        self.assertEqual(self.announcement.view_count, 1)


class EventDatetimeTests(TestCase):
    def setUp(self):
        self.bch = User.objects.create_user(email="bchtest2@due.udn.vn", password="Pass1234!", role=User.Role.BCH)

    def test_event_announcement_requires_event_datetime(self):
        response = self.client.post(
            reverse("announcements:create"),
            self._login_and_build_payload(kind=Announcement.Kind.EVENT, with_datetime=False),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "event_datetime", [
            "Bắt buộc nhập thời gian diễn ra sự kiện đối với thông báo loại sự kiện."
        ])

    def test_event_announcement_with_datetime_is_created(self):
        response = self.client.post(
            reverse("announcements:create"),
            self._login_and_build_payload(kind=Announcement.Kind.EVENT, with_datetime=True),
        )
        self.assertEqual(response.status_code, 302)
        announcement = Announcement.objects.get(title="Ngay hoi viec lam")
        self.assertIsNotNone(announcement.event_datetime)

    def _login_and_build_payload(self, kind, with_datetime):
        self.client.force_login(self.bch)
        payload = {
            "kind": kind,
            "title": "Ngay hoi viec lam",
            "body": "noi dung su kien",
            "attachments-TOTAL_FORMS": "0",
            "attachments-INITIAL_FORMS": "0",
            "attachments-MIN_NUM_FORMS": "0",
            "attachments-MAX_NUM_FORMS": "1000",
        }
        if with_datetime:
            payload["event_datetime_0"] = "2026-10-20"
            payload["event_datetime_1"] = "08:00"
        return payload

    def test_pin_scope_is_per_kind(self):
        staff = User.objects.create_user(email="staff3@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        a1, _ = services.create_announcement(self.bch, Announcement.Kind.EVENT, "SK1", "noi dung")
        a2, _ = services.create_announcement(self.bch, Announcement.Kind.EVENT, "SK2", "noi dung")

        from django.test import override_settings

        with override_settings(MAX_PINNED_PER_CATEGORY=1):
            services.pin_announcement(a1, staff)
            from config.mixins import PinLimitExceeded

            with self.assertRaises(PinLimitExceeded):
                services.pin_announcement(a2, staff)


class AnnouncementPostPermissionTests(TestCase):
    """Giao vu Khoa, BCH Khoa, Giang vien dang thong bao duoc, dang ngay khong
    qua duyet noi bo nao; Sinh vien khong dang duoc."""

    def setUp(self):
        self.lecturer = User.objects.create_user(email="post_lecturer@due.udn.vn", password="Pass1234!", role=User.Role.LECTURER)
        self.student = User.objects.create_user(email="post_student@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT)

    def _payload(self):
        return {
            "kind": Announcement.Kind.OFFICIAL,
            "title": "Thong bao tu giang vien",
            "body": "noi dung thong bao",
            "attachments-TOTAL_FORMS": "0",
            "attachments-INITIAL_FORMS": "0",
            "attachments-MIN_NUM_FORMS": "0",
            "attachments-MAX_NUM_FORMS": "10",
        }

    def test_lecturer_can_post_announcement_immediately(self):
        self.client.force_login(self.lecturer)
        response = self.client.post(reverse("announcements:create"), self._payload())
        self.assertEqual(response.status_code, 302)
        announcement = Announcement.objects.get(title="Thong bao tu giang vien")
        self.assertEqual(announcement.status, "published")

    def test_lecturer_sees_create_button_on_list_page(self):
        self.client.force_login(self.lecturer)
        response = self.client.get(reverse("announcements:list"))
        self.assertContains(response, reverse("announcements:create"))

    def test_student_cannot_post_announcement(self):
        self.client.force_login(self.student)
        response = self.client.post(reverse("announcements:create"), self._payload(), follow=True)
        self.assertFalse(Announcement.objects.filter(title="Thong bao tu giang vien").exists())
        messages = list(response.context["messages"])
        self.assertTrue(any("Chỉ Giáo vụ Khoa" in str(m) for m in messages))


class EventRegistrationFormAndNotificationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="ev_admin@due.udn.vn", password="Pass1234!", role=User.Role.STAFF,
            last_name="Đặng Văn", first_name="Giang",
        )
        self.bch = User.objects.create_user(
            email="ev_bch@due.udn.vn", password="Pass1234!", role=User.Role.BCH,
            last_name="Trần", first_name="Minh",
        )
        self.student = User.objects.create_user(
            email="ev_student@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT,
            last_name="Nguyễn Văn", first_name="An",
            student_id="221121514101", class_name="48K22.1", phone="0905123456",
        )
        self.event, _ = services.create_announcement(
            self.bch, Announcement.Kind.EVENT, "Ngày hội việc làm EMT", "Nội dung sự kiện",
        )

    def test_get_register_page_prefills_user_info(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("announcements:register_event", args=[self.event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nguyễn Văn An")
        self.assertContains(response, "221121514101")
        self.assertContains(response, "48K22.1")
        self.assertContains(response, "0905123456")

    def test_post_valid_registration_saves_info_and_notifies_admin(self):
        from notifications.models import Notification
        from .models import EventRegistration

        self.client.force_login(self.student)
        response = self.client.post(
            reverse("announcements:register_event", args=[self.event.pk]),
            {
                "full_name": "Nguyễn Văn An",
                "student_id": "221121514101",
                "class_name": "48K22.1",
                "email": "ev_student@due.udn.vn",
                "phone": "0905123456",
                "note": "Đăng ký tham gia phỏng vấn thử",
            },
        )
        self.assertRedirects(response, self.event.get_absolute_url())

        reg = EventRegistration.objects.get(announcement=self.event, user=self.student)
        self.assertEqual(reg.full_name, "Nguyễn Văn An")
        self.assertEqual(reg.student_id, "221121514101")
        self.assertEqual(reg.class_name, "48K22.1")
        self.assertEqual(reg.phone, "0905123456")
        self.assertEqual(reg.note, "Đăng ký tham gia phỏng vấn thử")

        admin_notif = Notification.objects.filter(
            recipient=self.admin, verb=Notification.Verb.EVENT_REGISTRATION
        ).first()
        self.assertIsNotNone(admin_notif)
        self.assertEqual(
            admin_notif.message,
            'Nguyễn Văn An đã đăng ký tham gia "Ngày hội việc làm EMT"',
        )
        self.assertEqual(admin_notif.content_object, reg)

        # Clicking notification redirects admin directly to #registrations on event detail
        self.client.force_login(self.admin)
        click_resp = self.client.get(reverse("notifications:click", args=[admin_notif.pk]))
        self.assertRedirects(click_resp, f"{self.event.get_absolute_url()}#registrations")

        # Admin sees the registration list table; regular student does not
        detail_admin = self.client.get(self.event.get_absolute_url())
        self.assertContains(detail_admin, "Danh sách đăng ký tham gia")
        self.assertContains(detail_admin, "Đăng ký tham gia phỏng vấn thử")

        other_student = User.objects.create_user(
            email="other_sv@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT,
        )
        self.client.force_login(other_student)
        detail_student = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(detail_student, "Danh sách đăng ký tham gia")

    def test_post_invalid_phone_returns_form_errors(self):
        from notifications.models import Notification
        from .models import EventRegistration

        self.client.force_login(self.student)
        response = self.client.post(
            reverse("announcements:register_event", args=[self.event.pk]),
            {
                "full_name": "Nguyễn Văn An",
                "student_id": "221121514101",
                "class_name": "48K22.1",
                "email": "ev_student@due.udn.vn",
                "phone": "123",
                "note": "",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("phone", response.context["form"].errors)
        self.assertFalse(EventRegistration.objects.filter(announcement=self.event, user=self.student).exists())
        self.assertFalse(
            Notification.objects.filter(recipient=self.admin, verb=Notification.Verb.EVENT_REGISTRATION).exists()
        )


class AnnouncementRoleSeparationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email="perm_staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF,
        )
        self.bch = User.objects.create_user(
            email="perm_bch@due.udn.vn", password="Pass1234!", role=User.Role.BCH,
        )
        self.lecturer = User.objects.create_user(
            email="perm_lecturer@due.udn.vn", password="Pass1234!", role=User.Role.LECTURER,
        )
        self.student = User.objects.create_user(
            email="perm_student@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT,
        )
        self.event, _ = services.create_announcement(
            self.staff, Announcement.Kind.EVENT, "Sự kiện phân quyền", "Nội dung",
        )
        self.pending = Announcement.objects.create(
            author=self.staff, kind=Announcement.Kind.OFFICIAL,
            title="Thông báo chờ duyệt", body="Nội dung ẩn", status="pending_review",
        )

    def test_admin_roles_sidebar_and_event_detail_are_management_focused(self):
        for admin_user in (self.staff, self.bch, self.lecturer):
            self.client.force_login(admin_user)
            list_resp = self.client.get(reverse("announcements:list"))
            self.assertContains(list_resp, "Đăng thông báo mới")
            self.assertContains(list_resp, "Thông báo của tôi")
            self.assertNotContains(list_resp, "Sự kiện đã đăng ký")
            self.assertNotContains(list_resp, "Hạn chót của tôi")

            detail_resp = self.client.get(self.event.get_absolute_url())
            self.assertContains(detail_resp, "Xem danh sách đăng ký")
            self.assertNotContains(detail_resp, "Đăng ký tham gia")
            self.assertNotContains(
                detail_resp,
                reverse("moderation:create_report", args=["announcement", self.event.pk]),
            )

            # Admin roles cannot register for events or open student deadline list
            reg_resp = self.client.get(reverse("announcements:register_event", args=[self.event.pk]))
            self.assertRedirects(reg_resp, self.event.get_absolute_url())

            dl_resp = self.client.get(reverse("notifications:deadlines"))
            self.assertRedirects(dl_resp, reverse("notifications:list"))

    def test_student_sidebar_and_restrictions(self):
        self.client.force_login(self.student)
        list_resp = self.client.get(reverse("announcements:list"))
        self.assertNotContains(list_resp, "Đăng thông báo mới")
        self.assertNotContains(list_resp, "Thông báo của tôi")
        self.assertContains(list_resp, "Sự kiện đã đăng ký")
        self.assertContains(list_resp, "Hạn chót của tôi")

        detail_resp = self.client.get(self.event.get_absolute_url())
        self.assertContains(detail_resp, "Đăng ký tham gia")
        self.assertNotContains(detail_resp, "Danh sách đăng ký tham gia")

        # Student cannot view unpublished announcement, edit, or pin
        pending_resp = self.client.get(self.pending.get_absolute_url())
        self.assertRedirects(pending_resp, reverse("announcements:list"))

        edit_resp = self.client.get(reverse("announcements:edit", args=[self.event.pk]))
        self.assertRedirects(edit_resp, self.event.get_absolute_url())

        pin_resp = self.client.post(reverse("announcements:pin", args=[self.event.pk]))
        self.assertRedirects(pin_resp, self.event.get_absolute_url())
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_pinned)

    def test_anonymous_cannot_crash_on_edit(self):
        resp = self.client.get(reverse("announcements:edit", args=[self.event.pk]))
        self.assertRedirects(resp, self.event.get_absolute_url())
