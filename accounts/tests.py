import re

from django.core import mail
from django.test import TestCase
from django.urls import reverse

from .forms import CAPTCHA_AFTER_FAILED_ATTEMPTS, LOGIN_FAILED_COUNT_SESSION_KEY
from .models import User


class SignupFormTests(TestCase):
    def _signup_payload(self, email):
        return {
            "email": email,
            "last_name": "Nguyen",
            "first_name": "Van A",
            "class_name": "18K1",
            "password1": "Str0ngPass!2345",
            "password2": "Str0ngPass!2345",
        }

    def test_signup_rejects_email_outside_allowed_domain(self):
        response = self.client.post(reverse("account_signup"), self._signup_payload("someone@gmail.com"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="someone@gmail.com").exists())

    def test_signup_with_numeric_local_part_autofills_student_id(self):
        email = "241124022206@due.udn.vn"
        response = self.client.post(reverse("account_signup"), self._signup_payload(email))
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email=email)
        self.assertEqual(user.student_id, "241124022206")
        self.assertEqual(user.role, User.Role.STUDENT)

    def test_signup_with_non_numeric_local_part_leaves_student_id_blank(self):
        email = "nguyenvana@due.udn.vn"
        response = self.client.post(reverse("account_signup"), self._signup_payload(email))
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email=email)
        self.assertEqual(user.student_id, "")

    def test_signup_saves_last_name_and_first_name_separately(self):
        email = "hoten@due.udn.vn"
        response = self.client.post(reverse("account_signup"), self._signup_payload(email))
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email=email)
        self.assertEqual(user.last_name, "Nguyen")
        self.assertEqual(user.first_name, "Van A")

    def test_signup_saves_class_name(self):
        email = "lop@due.udn.vn"
        response = self.client.post(reverse("account_signup"), self._signup_payload(email))
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email=email)
        self.assertEqual(user.class_name, "18K1")


class FullNameDisplayTests(TestCase):
    def test_get_full_name_uses_vietnamese_order_ho_truoc_ten_sau(self):
        user = User(last_name="Nguyen", first_name="Van A")
        self.assertEqual(user.get_full_name(), "Nguyen Van A")


class ClassNameFieldVisibilityTests(TestCase):
    """'Lop' chi danh cho Sinh vien/BCH Khoa - khong danh cho Giang vien/Giao vu Khoa."""

    def test_student_and_bch_have_class_name_field(self):
        student = User.objects.create_user(email="sv-lop@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT)
        bch = User.objects.create_user(email="bch-lop@due.udn.vn", password="Pass1234!", role=User.Role.BCH)
        self.assertTrue(student.has_class_name_field)
        self.assertTrue(bch.has_class_name_field)

    def test_lecturer_and_staff_have_no_class_name_field(self):
        lecturer = User.objects.create_user(email="gv-lop@due.udn.vn", password="Pass1234!", role=User.Role.LECTURER)
        staff = User.objects.create_user(email="gvk-lop@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.assertFalse(lecturer.has_class_name_field)
        self.assertFalse(staff.has_class_name_field)

    def test_profile_edit_form_hides_class_name_for_lecturer(self):
        lecturer = User.objects.create_user(email="gv-form@due.udn.vn", password="Pass1234!", role=User.Role.LECTURER)
        self.client.force_login(lecturer)
        response = self.client.get(reverse("accounts:profile_edit"))
        self.assertNotIn(b'name="class_name"', response.content)

    def test_profile_edit_form_shows_class_name_for_student(self):
        student = User.objects.create_user(email="sv-form@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT)
        self.client.force_login(student)
        response = self.client.get(reverse("accounts:profile_edit"))
        self.assertIn(b'name="class_name"', response.content)

    def test_assign_role_clears_class_name_when_changing_to_lecturer(self):
        staff = User.objects.create_user(email="gvk-assign@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        student = User.objects.create_user(
            email="sv-assign@due.udn.vn", password="Pass1234!", role=User.Role.STUDENT, class_name="20K1"
        )
        self.client.force_login(staff)
        self.client.post(reverse("accounts:assign_role", args=[student.pk]), {"role": User.Role.LECTURER})
        student.refresh_from_db()
        self.assertEqual(student.role, User.Role.LECTURER)
        self.assertEqual(student.class_name, "")

    def test_assign_role_notifies_target_user(self):
        from notifications.models import Notification

        staff = User.objects.create_user(email="gvk-notify@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        student = User.objects.create_user(email="sv-notify@due.udn.vn", password="Pass1234!")
        self.client.force_login(staff)
        self.client.post(reverse("accounts:assign_role", args=[student.pk]), {"role": User.Role.LECTURER})
        notif = Notification.objects.get(recipient=student, verb=Notification.Verb.MODERATION)
        self.assertIn("Giảng viên", notif.message)


class LoginCaptchaTests(TestCase):
    """Dang nhap sai nhieu lan lien tiep phai bat buoc giai CAPTCHA
    (yeu cau xac thuc CAPTCHA khi phat hien hoat dong bat thuong)."""

    def setUp(self):
        self.user = User.objects.create_user(email="login_test@due.udn.vn", password="Correct!2345")

    def _fail_login(self, password="Sai_mat_khau!"):
        return self.client.post(reverse("account_login"), {"login": self.user.email, "password": password})

    def test_login_form_has_no_captcha_initially(self):
        response = self.client.get(reverse("account_login"))
        self.assertNotIn(b"id_captcha", response.content)

    def test_captcha_required_after_threshold_failed_attempts(self):
        for _ in range(CAPTCHA_AFTER_FAILED_ATTEMPTS):
            self._fail_login()
        response = self.client.get(reverse("account_login"))
        self.assertIn(b"id_captcha", response.content)

    def test_successful_login_resets_failed_attempt_count(self):
        self._fail_login()
        response = self.client.post(
            reverse("account_login"), {"login": self.user.email, "password": "Correct!2345"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get(LOGIN_FAILED_COUNT_SESSION_KEY, 0), 0)


class PasswordResetEmailTests(TestCase):
    """Quen mat khau -> gui mail chua link -> dat lai mat khau -> gui them mail xac nhan
    (ACCOUNT_EMAIL_NOTIFICATIONS=True, xem config/settings.py)."""

    def setUp(self):
        self.user = User.objects.create_user(email="pwreset@due.udn.vn", password="OldPass!2345")

    def test_request_reset_sends_email_with_working_link(self):
        response = self.client.post(reverse("account_reset_password"), {"email": self.user.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Khôi phục mật khẩu", mail.outbox[0].subject)
        self.assertIn("/accounts/password/reset/key/", mail.outbox[0].body)

    def test_completing_reset_sends_confirmation_email_and_changes_password(self):
        self.client.post(reverse("account_reset_password"), {"email": self.user.email})
        link = re.search(r"(/accounts/password/reset/key/\S+)", mail.outbox[0].body).group(1)
        redirect = self.client.get(link).headers["Location"]
        self.client.post(redirect, {"password1": "NewPass!6789", "password2": "NewPass!6789"})

        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("đặt lại", mail.outbox[1].subject)

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewPass!6789"))


class ChangeEmailDisabledTests(TestCase):
    """Chan tinh nang 'Doi Email' cua allauth - form doi email mac dinh khong di qua
    validate domain @due.udn.vn (chi ap dung luc dang ky), neu mo se cho doi sang
    email domain bat ky (xem config/urls.py)."""

    def test_account_email_url_redirects_away_instead_of_showing_change_form(self):
        user = User.objects.create_user(email="emaillock@due.udn.vn", password="Pass1234!")
        self.client.force_login(user)
        response = self.client.get("/accounts/email/")
        self.assertRedirects(response, reverse("home"))


class ProfileStatsAndActivityTests(TestCase):
    """Trang ho so phai hien Thong ke ho so + Lich su hoat dong ca nhan (bai dang,
    binh luan) thay vi chi hien thong tin tinh nhu truoc."""

    def setUp(self):
        from forum import services as forum_services
        from forum.models import Category
        from interactions.models import Comment
        from django.contrib.contenttypes.models import ContentType

        from interactions.models import Follow

        self.user = User.objects.create_user(email="profilestats@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="ProfileStatsCat")
        self.post, _ = forum_services.create_post(self.user, category, "Bai test thong ke", "noi dung")

        other_author = User.objects.create_user(email="other_author@due.udn.vn", password="Pass1234!")
        other_post, _ = forum_services.create_post(other_author, category, "Bai cua nguoi khac", "noi dung khac")
        Comment.objects.create(
            author=self.user, content_type=ContentType.objects.get_for_model(other_post), object_id=other_post.pk,
            body="Binh luan cua toi", status="published",
        )
        Follow.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(category), object_id=category.pk)
        Follow.objects.create(user=self.user, content_type=ContentType.objects.get_for_model(other_post), object_id=other_post.pk)

    def test_profile_shows_post_and_comment_counts(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.user.pk]))
        self.assertEqual(response.context["stats"]["forum_post_count"], 1)
        self.assertEqual(response.context["stats"]["comment_count"], 1)

    def test_owner_sees_follow_counts_and_link_in_stats(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.user.pk]))
        self.assertEqual(response.context["stats"]["followed_post_count"], 1)
        self.assertEqual(response.context["stats"]["followed_category_count"], 1)
        self.assertContains(response, "Đã theo dõi: 1 bài viết, 1 chuyên mục")
        self.assertContains(response, reverse("interactions:following"))

    def test_other_viewer_does_not_see_follow_stat(self):
        other = User.objects.create_user(email="profilestats_viewer@due.udn.vn", password="Pass1234!")
        self.client.force_login(other)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.user.pk]))
        self.assertNotIn("followed_post_count", response.context["stats"])
        self.assertNotContains(response, "Đã theo dõi")

    def test_profile_activity_lists_own_post_and_comment(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.user.pk]))
        titles = [a["title"] for a in response.context["page_obj"]]
        self.assertIn("Bai test thong ke", titles)
        self.assertIn("Bai cua nguoi khac", titles)

    def test_profile_activity_is_paginated_at_five_per_page(self):
        from forum import services as forum_services
        from forum.models import Category

        category = Category.objects.create(name="PhanTrangCat")
        for i in range(8):
            forum_services.create_post(self.user, category, f"Bai phan trang {i}", "noi dung")
        self.client.force_login(self.user)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.user.pk]))
        self.assertEqual(len(response.context["page_obj"]), 5)
        self.assertTrue(response.context["page_obj"].has_next())


class UserListSearchAndRoleDropdownTests(TestCase):
    """Trang Quan ly tai khoan: tim theo email + doi vai tro bang dropdown ngay trong
    bang (tai dung view assign_role co san, chi doi UI)."""

    def setUp(self):
        self.staff = User.objects.create_user(email="ullist_staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.student = User.objects.create_user(email="ullist_student@due.udn.vn", password="Pass1234!")
        self.lecturer = User.objects.create_user(email="ullist_lecturer@due.udn.vn", password="Pass1234!", role=User.Role.LECTURER)

    def test_search_by_email_filters_list(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accounts:user_list"), {"q": "lecturer"})
        users = list(response.context["users"])
        self.assertIn(self.lecturer, users)
        self.assertNotIn(self.student, users)

    def test_role_dropdown_renders_with_current_role_selected(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accounts:user_list"))
        self.assertContains(response, f'value="{self.student.role}" selected')

    def test_changing_role_via_dropdown_form_updates_user(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("accounts:assign_role", args=[self.student.pk]), {"role": User.Role.LECTURER})
        self.student.refresh_from_db()
        self.assertEqual(self.student.role, User.Role.LECTURER)


class HeaderRoleBadgeVisibilityTests(TestCase):
    """Chi Giao vu Khoa moi thay badge vai tro canh ten minh o header."""

    def test_staff_sees_role_badge_in_header(self):
        staff = User.objects.create_user(email="badge_staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.client.force_login(staff)
        response = self.client.get(reverse("home"))
        self.assertContains(response, staff.get_role_display())

    def test_student_does_not_see_role_badge_in_header(self):
        student = User.objects.create_user(email="badge_student@due.udn.vn", password="Pass1234!")
        self.client.force_login(student)
        response = self.client.get(reverse("home"))
        html = response.content.decode("utf-8")
        button_start = html.index('class="user-menu-btn"')
        button_end = html.index("</button>", button_start)
        self.assertNotIn("pill", html[button_start:button_end])


class RoleBadgeVisibleOnlyToStaffViewerTests(TestCase):
    """Badge vai tro (BXH ca nhan o trang chu, trang ho so) chi HIEN THI khi NGUOI XEM
    la Giao vu Khoa - nguoi xem chua dang nhap hoac vai tro khac thi khong thay badge
    nao ca (du nguoi duoc xem la ai)."""

    def _make_comment(self, author, target):
        from django.contrib.contenttypes.models import ContentType

        from interactions.models import Comment

        Comment.objects.create(
            author=author, content_type=ContentType.objects.get_for_model(target), object_id=target.pk,
            body="binh luan", status="published",
        )

    def setUp(self):
        from forum import services as forum_services
        from forum.models import Category

        self.student = User.objects.create_user(email="rb_student@due.udn.vn", password="Pass1234!")
        self.other_student = User.objects.create_user(email="rb_student2@due.udn.vn", password="Pass1234!")
        self.staff = User.objects.create_user(email="rb_staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        category = Category.objects.create(name="RoleBadgeCat")
        post, _ = forum_services.create_post(self.staff, category, "Bai cua giao vu", "noi dung")
        self._make_comment(self.student, post)
        self._make_comment(self.staff, post)

    def test_anonymous_viewer_sees_no_role_badge_on_leaderboard(self):
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, self.staff.get_role_display())

    def test_non_staff_viewer_sees_no_role_badge_on_leaderboard(self):
        self.client.force_login(self.other_student)
        response = self.client.get(reverse("home"))
        self.assertNotContains(response, self.staff.get_role_display())

    def test_staff_viewer_sees_role_badges_on_leaderboard(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("home"))
        self.assertContains(response, self.staff.get_role_display())
        self.assertContains(response, self.student.get_role_display())

    def test_non_staff_viewer_sees_no_role_badge_on_profile_page(self):
        self.client.force_login(self.other_student)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.staff.pk]))
        self.assertNotContains(response, self.staff.get_role_display())

    def test_staff_viewer_sees_role_badge_on_profile_page(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("accounts:profile_detail", args=[self.student.pk]))
        self.assertContains(response, self.student.get_role_display())


class ViewOtherUserProfileTests(TestCase):
    """Xem trang ca nhan cua nguoi khac: giong het trang cua minh, tru email, Bai viet
    da luu, Dang theo doi - cac muc nay chi chu tai khoan moi thay."""

    def setUp(self):
        self.owner = User.objects.create_user(email="profile_owner@due.udn.vn", password="Pass1234!")
        self.viewer = User.objects.create_user(email="profile_viewer@due.udn.vn", password="Pass1234!")

    def test_owner_sees_own_email_and_private_links(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.owner.get_absolute_url())
        self.assertContains(response, self.owner.email)
        self.assertContains(response, reverse("interactions:saved"))
        self.assertContains(response, reverse("interactions:following"))

    def test_other_viewer_cannot_see_email_or_private_links(self):
        self.client.force_login(self.viewer)
        response = self.client.get(self.owner.get_absolute_url())
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, reverse("interactions:saved"))
        self.assertNotContains(response, reverse("interactions:following"))

    def test_get_absolute_url_points_to_profile_detail(self):
        self.assertEqual(self.owner.get_absolute_url(), reverse("accounts:profile_detail", args=[self.owner.pk]))


class SavedItemsListTests(TestCase):
    """Danh sach bai da luu cua toi - chi minh xem duoc (khong co tham so user trong URL)."""

    def setUp(self):
        from django.contrib.contenttypes.models import ContentType

        from forum import services as forum_services
        from forum.models import Category
        from interactions.models import SavedItem

        self.user = User.objects.create_user(email="saved_user@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="SavedCat")
        self.post, _ = forum_services.create_post(self.user, category, "Bai de luu", "noi dung")
        content_type = ContentType.objects.get_for_model(self.post)
        SavedItem.objects.create(user=self.user, content_type=content_type, object_id=self.post.pk)

    def test_saved_list_shows_saved_post(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("interactions:saved"))
        self.assertContains(response, "Bai de luu")

    def test_anonymous_cannot_view_saved_list(self):
        response = self.client.get(reverse("interactions:saved"))
        self.assertNotEqual(response.status_code, 200)
