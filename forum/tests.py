from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from config.mixins import ContentStatus, PinLimitExceeded
from interactions.models import Comment, Like
from moderation.models import SensitiveKeyword

from . import services
from .models import Category


class SensitiveKeywordFilterTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(email="sv1@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chung")
        SensitiveKeyword.objects.create(keyword="camtu")

    def test_post_with_sensitive_keyword_goes_to_pending_review(self):
        post, keyword = services.create_post(self.author, self.category, "Tieu de co camtu", "noi dung")
        self.assertEqual(post.status, ContentStatus.PENDING_REVIEW)
        self.assertIsNotNone(keyword)

    def test_post_without_sensitive_keyword_is_published(self):
        post, keyword = services.create_post(self.author, self.category, "Tieu de binh thuong", "noi dung")
        self.assertEqual(post.status, ContentStatus.PUBLISHED)
        self.assertIsNone(keyword)

    def test_update_reverts_to_pending_review_when_keyword_added(self):
        post, _ = services.create_post(self.author, self.category, "Tieu de", "noi dung")
        self.assertEqual(post.status, ContentStatus.PUBLISHED)
        post, keyword = services.update_post(post, self.author, "Tieu de", "noi dung co camtu")
        self.assertEqual(post.status, ContentStatus.PENDING_REVIEW)
        self.assertIsNotNone(keyword)


class MyPostsTabTests(TestCase):
    """Tab 'Bai viet cua toi' phai hien MOI trang thai bai cua rieng tac gia (ke ca
    pending_review) kem badge, tranh ho tuong dang loi roi dang lai nhieu lan."""

    def setUp(self):
        SensitiveKeyword.objects.create(keyword="camtu")
        self.author = User.objects.create_user(email="myposts_author@due.udn.vn", password="Pass1234!")
        self.other = User.objects.create_user(email="myposts_other@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="MyPostsCat")
        self.pending_post, _ = services.create_post(self.author, self.category, "Bai co camtu", "noi dung")
        self.other_published, _ = services.create_post(self.other, self.category, "Bai cong khai", "noi dung")

    def test_default_list_does_not_show_own_pending_post(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse("forum:post_list"))
        self.assertNotContains(response, "Bai co camtu")

    def test_mine_tab_shows_own_pending_post_with_badge(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse("forum:post_list"), {"mine": "1"})
        self.assertContains(response, "Bai co camtu")
        self.assertContains(response, "Chờ kiểm duyệt")

    def test_mine_tab_does_not_show_other_users_posts(self):
        self.client.force_login(self.author)
        response = self.client.get(reverse("forum:post_list"), {"mine": "1"})
        self.assertNotContains(response, "Bai cong khai")


class PinLimitTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(email="staff1@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.category = Category.objects.create(name="Chung", max_pinned=1)

    def _make_post(self, title):
        return services.create_post(
            User.objects.create_user(email=f"{title}@due.udn.vn", password="Pass1234!"),
            self.category, title, "noi dung",
        )[0]

    def test_pin_is_rejected_when_category_limit_reached(self):
        post1 = self._make_post("post1")
        post2 = self._make_post("post2")

        services.pin_post(post1, self.staff)
        self.assertTrue(post1.is_pinned)

        with self.assertRaises(PinLimitExceeded):
            services.pin_post(post2, self.staff)

        # Bai cu KHONG bi tu dong bo ghim
        post1.refresh_from_db()
        self.assertTrue(post1.is_pinned)

    def test_unpin_then_pin_new_post_succeeds(self):
        post1 = self._make_post("post1")
        post2 = self._make_post("post2")

        services.pin_post(post1, self.staff)
        services.unpin_post(post1, self.staff)
        services.pin_post(post2, self.staff)
        self.assertTrue(post2.is_pinned)


class EditHistoryTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(email="sv2@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chung")

    def test_edit_creates_history_snapshot_without_overwriting(self):
        post, _ = services.create_post(self.author, self.category, "Tieu de goc", "noi dung goc")
        services.update_post(post, self.author, "Tieu de moi", "noi dung moi")
        services.update_post(post, self.author, "Tieu de moi 2", "noi dung moi 2")

        self.assertEqual(post.edit_count, 2)
        self.assertEqual(post.history.count(), 2)
        # Ban ghi lich su phai luu noi dung TRUOC khi sua, theo dung thu tu
        first_snapshot = post.history.order_by("edited_at").first()
        self.assertEqual(first_snapshot.title, "Tieu de goc")


class PostCreateRateLimitTests(TestCase):
    """Gioi han 10 bai/phut cho moi user (xem @ratelimit tren PostCreateView)."""

    def setUp(self):
        cache.clear()
        self.category = Category.objects.create(name="Chung")
        self.user = User.objects.create_user(email="ratelimit1@due.udn.vn", password="Pass1234!")
        self.client.force_login(self.user)

    def test_11th_post_within_a_minute_is_blocked(self):
        for i in range(10):
            response = self.client.post(
                reverse("forum:post_create"),
                {"category": self.category.pk, "title": f"Bai {i}", "body": "noi dung", "tags": []},
            )
            self.assertEqual(response.status_code, 302, f"Bài thứ {i + 1} phải thành công")

        response = self.client.post(
            reverse("forum:post_create"),
            {"category": self.category.pk, "title": "Bai thu 11", "body": "noi dung", "tags": []},
        )
        self.assertEqual(response.status_code, 403)


class RichTextSanitizeTests(TestCase):
    """Noi dung tu CKEditor la HTML - phai loc bo the/attribute nguy hiem (XSS) truoc khi luu."""

    def setUp(self):
        self.author = User.objects.create_user(email="richtext@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chung")

    def test_script_tag_is_stripped_on_create(self):
        malicious_body = "<p>Noi dung binh thuong</p><script>alert('xss')</script>"
        post, _ = services.create_post(self.author, self.category, "Bai test XSS", malicious_body)
        self.assertNotIn("<script>", post.body)
        self.assertIn("Noi dung binh thuong", post.body)

    def test_onerror_attribute_is_stripped_on_create(self):
        malicious_body = '<img src="x" onerror="alert(1)">'
        post, _ = services.create_post(self.author, self.category, "Bai test XSS 2", malicious_body)
        self.assertNotIn("onerror", post.body)

    def test_allowed_formatting_tags_are_kept(self):
        body = "<p><strong>Dam</strong> va <em>nghieng</em></p><ul><li>Muc 1</li></ul>"
        post, _ = services.create_post(self.author, self.category, "Bai test formatting", body)
        self.assertIn("<strong>", post.body)
        self.assertIn("<ul>", post.body)


class PostListInteractionCountsAndFiltersTests(TestCase):
    """Danh sach bai viet hien so binh luan/luot thich, sap xep theo
    hoat dong gan nhat - lay cam hung tu so sanh voi forum.uit.edu.vn (Discourse)."""

    def setUp(self):
        self.author = User.objects.create_user(email="list_author@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chung")

        self.quiet_post, _ = services.create_post(
            self.author, self.category, "Bai khong ai binh luan", "noi dung",
        )
        self.active_post, _ = services.create_post(
            self.author, self.category, "Bai dang thao luan soi noi", "noi dung",
        )

        # active_post cu hon ve created_at nhung vua co binh luan moi -> phai len dau
        # khi sort=activity, duoi khi sort mac dinh (theo created_at)
        self.active_post.created_at = self.quiet_post.created_at - timedelta(days=5)
        self.active_post.save(update_fields=["created_at"])

        ct = ContentType.objects.get_for_model(self.active_post.__class__)
        comment = Comment.objects.create(
            author=self.author, content_type=ct, object_id=self.active_post.pk,
            body="binh luan", status="published",
        )
        # Dat gio tao binh luan ro rang SAU created_at cua quiet_post mot khoang an toan -
        # khong dua vao thu tu thuc thi tu nhien (auto_now_add) vi do phan giai dong ho
        # cua Windows co the khien 2 lenh lien tiep co cung 1 timestamp, lam test flaky.
        comment.created_at = self.quiet_post.created_at + timedelta(hours=1)
        comment.save(update_fields=["created_at"])
        Like.objects.create(user=self.author, content_type=ct, object_id=self.active_post.pk)

    def test_list_shows_comment_and_like_count(self):
        # Loc theo chuyen muc cua test: danh sach chia 10 bai/trang, bai mau tu migration seed co the day bai test sang trang 2
        response = self.client.get(reverse("forum:post_list_by_category", args=[self.category.slug]))
        posts = {p.pk: p for p in response.context["posts"]}
        self.assertEqual(posts[self.active_post.pk].comment_count, 1)
        self.assertEqual(posts[self.active_post.pk].like_count, 1)
        self.assertEqual(posts[self.quiet_post.pk].comment_count, 0)

    def test_default_sort_is_by_created_at(self):
        response = self.client.get(reverse("forum:post_list"))
        pks = [p.pk for p in response.context["posts"]]
        self.assertEqual(pks[0], self.quiet_post.pk)

    def test_activity_sort_brings_recently_commented_post_first(self):
        response = self.client.get(reverse("forum:post_list"), {"sort": "activity"})
        pks = [p.pk for p in response.context["posts"]]
        self.assertEqual(pks[0], self.active_post.pk)


class PostViewCountTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(email="viewcount_author@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="Chung VC")
        self.post, _ = services.create_post(self.author, self.category, "Bai test luot xem", "noi dung")

    def test_detail_view_increments_view_count(self):
        self.assertEqual(self.post.view_count, 0)
        self.client.get(reverse("forum:post_detail", args=[self.post.pk]))
        self.post.refresh_from_db()
        self.assertEqual(self.post.view_count, 1)

        self.client.get(reverse("forum:post_detail", args=[self.post.pk]))
        self.post.refresh_from_db()
        self.assertEqual(self.post.view_count, 2)

    def test_list_shows_view_count(self):
        self.client.get(reverse("forum:post_detail", args=[self.post.pk]))
        response = self.client.get(reverse("forum:post_list"))
        posts = {p.pk: p for p in response.context["posts"]}
        self.assertEqual(posts[self.post.pk].view_count, 1)


class CategoryCrudViewTests(TestCase):
    """Trang quan ly Category rieng trong giao dien EMT Forum (thay vi Django Admin)."""

    def setUp(self):
        self.staff = User.objects.create_user(email="cat_staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.student = User.objects.create_user(email="cat_student@due.udn.vn", password="Pass1234!")

    def test_student_cannot_access_category_list(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("forum:category_list"))
        self.assertRedirects(response, reverse("home"))

    def test_staff_can_create_edit_delete_category(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("forum:category_create"), {"name": "Góc hỏi đáp", "description": "", "max_pinned": ""})
        category = Category.objects.get(name="Góc hỏi đáp")

        self.client.post(
            reverse("forum:category_edit", args=[category.pk]),
            {"name": "Góc hỏi đáp", "description": "Mô tả mới", "max_pinned": 2},
        )
        category.refresh_from_db()
        self.assertEqual(category.description, "Mô tả mới")
        self.assertEqual(category.max_pinned, 2)

        self.client.post(reverse("forum:category_delete", args=[category.pk]))
        self.assertFalse(Category.objects.filter(pk=category.pk).exists())

    def test_cannot_delete_category_with_existing_posts(self):
        self.client.force_login(self.staff)
        category = Category.objects.create(name="Co bai viet")
        services.create_post(self.staff, category, "Bai test", "noi dung")

        response = self.client.post(reverse("forum:category_delete", args=[category.pk]), follow=True)
        self.assertTrue(Category.objects.filter(pk=category.pk).exists())
        messages = list(response.context["messages"])
        self.assertTrue(any("đang có bài viết" in str(m) for m in messages))
