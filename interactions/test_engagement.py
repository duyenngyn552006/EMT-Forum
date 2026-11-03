"""Test cho cac tinh nang tuong tac: cam xuc (reaction), binh chon (poll), theo doi (follow)."""
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from accounts.models import User
from forum import services
from forum.models import Category, ForumPost
from groups.models import Group, GroupMembership, GroupPost
from moderation.models import SensitiveKeyword
from notifications.models import Notification, NotificationPreference

from .models import Follow, Like, Poll, PollOption, PollVote


class ReactionTests(TestCase):
    """Cam xuc thay cho Like: moi user 1 cam xuc / bai, bam lai = go, chon cam xuc khac = doi."""

    def setUp(self):
        self.author = User.objects.create_user(email="rx-author@due.udn.vn", password="Pass1234!")
        self.user = User.objects.create_user(email="rx-user@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="ReactionCat")
        self.post = ForumPost.objects.create(author=self.author, category=category, title="Bai cam xuc", body="x", status="published")
        self.url = reverse("interactions:toggle_like", args=["forumpost", self.post.pk])
        self.client.force_login(self.user)

    def _reactions(self):
        return list(Like.objects.filter(object_id=self.post.pk).values_list("user__email", "reaction"))

    def test_default_reaction_is_like(self):
        self.client.post(self.url)
        self.assertEqual(self._reactions(), [("rx-user@due.udn.vn", "like")])

    def test_choose_specific_reaction(self):
        self.client.post(self.url, {"reaction": "love"})
        self.assertEqual(self._reactions(), [("rx-user@due.udn.vn", "love")])

    def test_invalid_reaction_falls_back_to_like(self):
        self.client.post(self.url, {"reaction": "<script>"})
        self.assertEqual(self._reactions(), [("rx-user@due.udn.vn", "like")])

    def test_changing_reaction_updates_same_row(self):
        self.client.post(self.url, {"reaction": "like"})
        self.client.post(self.url, {"reaction": "idea"})
        self.assertEqual(self._reactions(), [("rx-user@due.udn.vn", "idea")])

    def test_same_reaction_again_removes_it(self):
        self.client.post(self.url, {"reaction": "wow"})
        self.client.post(self.url, {"reaction": "wow"})
        self.assertEqual(self._reactions(), [])

    def test_ajax_response_reports_reaction_and_total(self):
        response = self.client.post(self.url, {"reaction": "love"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.json(), {"liked": True, "count": 1, "reaction": "love"})

    def test_owner_notified_only_on_first_reaction_not_on_change(self):
        self.client.post(self.url, {"reaction": "like"})
        self.client.post(self.url, {"reaction": "love"})
        self.assertEqual(Notification.objects.filter(recipient=self.author, verb=Notification.Verb.LIKE).count(), 1)

    def test_detail_page_shows_reaction_counts(self):
        self.client.post(self.url, {"reaction": "love"})
        response = self.client.get(self.post.get_absolute_url())
        self.assertContains(response, 'name="reaction" value="idea"')
        love = next(r for r in response.context["reaction_options"] if r["key"] == "love")
        self.assertEqual((love["count"], love["active"]), (1, True))


class ReactionButtonTests(TestCase):
    """Nut Thich duy nhat: chua chon -> "Thich"; da chon -> hien cam xuc do va bam lai de go."""

    def setUp(self):
        self.user = User.objects.create_user(email="rb-user@due.udn.vn", password="Pass1234!")
        category = Category.objects.create(name="ReactionBtnCat")
        self.post = ForumPost.objects.create(author=self.user, category=category, title="Bai nut", body="x", status="published")
        self.client.force_login(self.user)

    def _main_button_value(self, response):
        return response.context["user_reaction_option"]

    def test_no_reaction_shows_default_like_button(self):
        response = self.client.get(self.post.get_absolute_url())
        self.assertIsNone(self._main_button_value(response))
        self.assertContains(response, 'id="reactionMainBtn"')
        self.assertContains(response, "Thích")

    def test_main_button_shows_chosen_reaction_and_picker_still_offers_all(self):
        Like.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(ForumPost), object_id=self.post.pk, reaction="love",
        )
        response = self.client.get(self.post.get_absolute_url())
        self.assertEqual(self._main_button_value(response)["key"], "love")
        # Nut chinh: emoji + so luong dung truoc nhan ("❤️ 1 Yêu thích")
        self.assertRegex(response.content.decode(), r"❤️\s*<span class=\"count\">1</span>\s*Yêu thích")
        for key in ("like", "love", "wow", "idea"):
            self.assertContains(response, f'name="reaction" value="{key}"')

    def test_anonymous_sees_summary_but_no_picker(self):
        Like.objects.create(
            user=self.user, content_type=ContentType.objects.get_for_model(ForumPost), object_id=self.post.pk, reaction="wow",
        )
        self.client.logout()
        response = self.client.get(self.post.get_absolute_url())
        self.assertContains(response, "reaction-chip")
        self.assertNotContains(response, 'id="reactionWrap"')


class PollTests(TestCase):
    """Poll: ai duoc tao/binh chon, doi lua chon truoc han chot, khoa sau han, quet tu khoa nhay cam."""

    def setUp(self):
        self.lecturer = User.objects.create_user(email="poll-lec@due.udn.vn", password="Pass1234!", role=User.Role.LECTURER)
        self.student = User.objects.create_user(email="poll-sv@due.udn.vn", password="Pass1234!")
        self.other_student = User.objects.create_user(email="poll-sv2@due.udn.vn", password="Pass1234!")
        self.staff = User.objects.create_user(email="poll-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        category = Category.objects.create(name="PollCat")
        self.post = ForumPost.objects.create(author=self.lecturer, category=category, title="Bai poll", body="x", status="published")
        self.student_post = ForumPost.objects.create(author=self.student, category=category, title="Bai SV", body="x", status="published")

        self.group = Group.objects.create(name="Nhom poll", created_by=self.lecturer, status=Group.Status.ACTIVE)
        GroupMembership.objects.create(group=self.group, user=self.lecturer, role=GroupMembership.Role.LEADER)
        GroupMembership.objects.create(group=self.group, user=self.student, role=GroupMembership.Role.MEMBER)
        self.group_post = GroupPost.objects.create(group=self.group, author=self.student, title="Bai nhom", body="x", status="published")

        self.category = category
        self.forum_create_url = reverse("forum:post_create")
        self.group_create_url = reverse("groups:post_create", args=[self.group.slug])
        self.poll_payload = {"poll-question": "Chon gio hop?", "poll-options": "Sang\nChieu\nToi"}

    def _make_poll(self, post=None, **kwargs):
        post = post or self.post
        poll = Poll.objects.create(
            content_type=ContentType.objects.get_for_model(post), object_id=post.pk,
            question="Hop luc nao?", created_by=self.lecturer, **kwargs,
        )
        for i, text in enumerate(["A", "B"]):
            PollOption.objects.create(poll=poll, text=text, position=i)
        return poll

    # --- tao poll: gop vao luc dang bai, bat ky tac gia nao cung tao duoc tren bai cua minh ---
    def test_poll_fields_are_not_required_so_browser_does_not_block_submit(self):
        """O tao poll ghi "khong bat buoc" nhung field van required=True mac dinh cua Django
        se sinh thuoc tinh HTML "required", khien trinh duyet chan nop ca bai dang du poll
        de trong (loi nguoi dung bao cao: bam Dang bi chan "Please fill out this field")."""
        self.client.force_login(self.student)
        response = self.client.get(self.forum_create_url)
        poll_form = response.context["poll_form"]
        self.assertFalse(poll_form.fields["question"].required)
        self.assertFalse(poll_form.fields["options"].required)

    def test_submitting_post_with_poll_fields_left_blank_succeeds(self):
        self.client.force_login(self.student)
        response = self.client.post(
            self.forum_create_url,
            {"category": self.category.pk, "title": "Bai khong poll", "body": "noi dung",
             "poll-question": "", "poll-options": "", "poll-closes_at": ""},
        )
        post = ForumPost.objects.get(title="Bai khong poll")
        self.assertRedirects(response, post.get_absolute_url())
        self.assertFalse(Poll.objects.exists())

    def test_poll_create_standalone_route_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("interactions:poll_create", args=["forumpost", self.post.pk])

    def test_any_author_can_attach_poll_when_creating_forum_post(self):
        self.client.force_login(self.student)  # sinh vien - truoc day khong duoc tao poll
        response = self.client.post(
            self.forum_create_url,
            {"category": self.category.pk, "title": "Bai co poll", "body": "noi dung", **self.poll_payload},
        )
        post = ForumPost.objects.get(title="Bai co poll")
        self.assertRedirects(response, post.get_absolute_url())
        poll = Poll.objects.get()
        self.assertEqual(poll.created_by, self.student)
        self.assertEqual([o.text for o in poll.options.all()], ["Sang", "Chieu", "Toi"])

    def test_poll_fields_hidden_when_editing_existing_post(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("forum:post_edit", args=[self.student_post.pk]))
        self.assertNotContains(response, "Thêm bình chọn")

    def test_any_member_can_attach_poll_when_creating_own_group_post(self):
        self.client.force_login(self.student)  # thanh vien thuong - truoc day chi truong/pho nhom moi tao duoc
        self.client.post(
            self.group_create_url, {"title": "Bai nhom co poll", "body": "noi dung", **self.poll_payload},
        )
        poll = Poll.objects.get()
        self.assertEqual(poll.created_by, self.student)
        self.assertEqual(poll.content_object.title, "Bai nhom co poll")

    def test_submitting_group_post_with_poll_fields_left_blank_succeeds(self):
        self.client.force_login(self.student)
        self.client.post(
            self.group_create_url,
            {"title": "Bai nhom khong poll", "body": "noi dung",
             "poll-question": "", "poll-options": "", "poll-closes_at": ""},
        )
        self.assertTrue(GroupPost.objects.filter(title="Bai nhom khong poll").exists())
        self.assertFalse(Poll.objects.exists())

    def test_poll_needs_at_least_two_distinct_options(self):
        self.client.force_login(self.student)
        response = self.client.post(
            self.forum_create_url,
            {"category": self.category.pk, "title": "Bai loi poll", "body": "noi dung",
             "poll-question": "Q", "poll-options": "A\na\n"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Poll.objects.exists())
        # Poll loi -> tu choi ca bai dang kem, khong tao "mo coi" khong co poll
        self.assertFalse(ForumPost.objects.filter(title="Bai loi poll").exists())

    def test_past_closing_time_is_rejected(self):
        self.client.force_login(self.student)
        self.client.post(
            self.forum_create_url,
            {"category": self.category.pk, "title": "Bai han qua khu", "body": "noi dung",
             **self.poll_payload, "poll-closes_at": "2000-01-01T10:00"},
        )
        self.assertFalse(Poll.objects.exists())
        self.assertFalse(ForumPost.objects.filter(title="Bai han qua khu").exists())

    def test_sensitive_keyword_in_poll_blocks_the_whole_post(self):
        SensitiveKeyword.objects.create(keyword="camtu")
        self.client.force_login(self.student)
        self.client.post(
            self.forum_create_url,
            {"category": self.category.pk, "title": "Bai poll cam tu", "body": "noi dung",
             "poll-question": "Hoi ve camtu", "poll-options": "A\nB"},
        )
        self.assertFalse(Poll.objects.exists())
        self.assertFalse(ForumPost.objects.filter(title="Bai poll cam tu").exists())

    # --- binh chon ---
    def test_vote_then_change_vote_keeps_one_ballot(self):
        poll = self._make_poll()
        a, b = poll.options.all()
        self.client.force_login(self.student)
        url = reverse("interactions:poll_vote", args=[poll.pk])
        self.client.post(url, {"option_id": a.pk})
        self.client.post(url, {"option_id": b.pk})
        self.assertEqual(PollVote.objects.get(user=self.student).option_id, b.pk)

    def test_cannot_vote_for_option_of_another_poll(self):
        poll = self._make_poll()
        other = self._make_poll(post=self.student_post)
        self.client.force_login(self.student)
        self.client.post(reverse("interactions:poll_vote", args=[poll.pk]), {"option_id": other.options.first().pk})
        self.assertFalse(PollVote.objects.exists())

    def test_closed_poll_rejects_votes_and_keeps_existing_one(self):
        poll = self._make_poll(closes_at=timezone.now() + timedelta(hours=1))
        a, b = poll.options.all()
        self.client.force_login(self.student)
        url = reverse("interactions:poll_vote", args=[poll.pk])
        self.client.post(url, {"option_id": a.pk})
        poll.closes_at = timezone.now() - timedelta(minutes=1)
        poll.save()
        self.client.post(url, {"option_id": b.pk})
        self.assertEqual(PollVote.objects.get(user=self.student).option_id, a.pk)

    def test_group_poll_only_members_can_vote(self):
        poll = self._make_poll(post=self.group_post)
        option = poll.options.first()
        url = reverse("interactions:poll_vote", args=[poll.pk])
        self.client.force_login(self.other_student)  # khong phai thanh vien
        self.client.post(url, {"option_id": option.pk})
        self.assertFalse(PollVote.objects.exists())
        self.client.force_login(self.student)  # thanh vien
        self.client.post(url, {"option_id": option.pk})
        self.assertEqual(PollVote.objects.count(), 1)

    def test_anonymous_cannot_vote(self):
        poll = self._make_poll()
        self.client.post(reverse("interactions:poll_vote", args=[poll.pk]), {"option_id": poll.options.first().pk})
        self.assertFalse(PollVote.objects.exists())

    # --- hien thi / xoa ---
    def test_results_show_percentages(self):
        poll = self._make_poll()
        a, b = poll.options.all()
        PollVote.objects.create(poll=poll, option=a, user=self.student)
        PollVote.objects.create(poll=poll, option=a, user=self.other_student)
        PollVote.objects.create(poll=poll, option=b, user=self.staff)
        self.client.force_login(self.student)
        response = self.client.get(self.post.get_absolute_url())
        options = {o.text: (o.percent, o.vote_count) for o in response.context["poll_options"]}
        self.assertEqual(options, {"A": (67, 2), "B": (33, 1)})
        self.assertEqual(response.context["poll_total_votes"], 3)

    def test_creator_or_staff_can_delete_poll_others_cannot(self):
        poll = self._make_poll()
        url = reverse("interactions:poll_delete", args=[poll.pk])
        self.client.force_login(self.student)
        self.client.post(url)
        self.assertTrue(Poll.objects.filter(pk=poll.pk).exists())
        self.client.force_login(self.staff)
        self.client.post(url)
        self.assertFalse(Poll.objects.filter(pk=poll.pk).exists())


class FollowTests(TestCase):
    """Theo doi chuyen muc/the/bai viet -> nhan thong bao khi co bai/binh luan moi."""

    def setUp(self):
        self.follower = User.objects.create_user(email="fl-follower@due.udn.vn", password="Pass1234!")
        self.author = User.objects.create_user(email="fl-author@due.udn.vn", password="Pass1234!")
        self.commenter = User.objects.create_user(email="fl-commenter@due.udn.vn", password="Pass1234!")
        self.category = Category.objects.create(name="FollowCat")
        self.post = ForumPost.objects.create(author=self.author, category=self.category, title="Bai theo doi", body="x", status="published")

    def _follow(self, model_key, obj, user=None):
        self.client.force_login(user or self.follower)
        return self.client.post(reverse("interactions:toggle_follow", args=[model_key, obj.pk]))

    def _follow_updates(self, user):
        return Notification.objects.filter(recipient=user, verb=Notification.Verb.FOLLOW_UPDATE).count()

    def test_toggle_follow_creates_then_removes(self):
        self._follow("category", self.category)
        self.assertEqual(Follow.objects.filter(user=self.follower).count(), 1)
        self._follow("category", self.category)
        self.assertEqual(Follow.objects.filter(user=self.follower).count(), 0)

    def test_cannot_follow_unsupported_model(self):
        response = self._follow("comment", self.post)
        self.assertEqual(response.status_code, 404)

    def test_anonymous_cannot_follow(self):
        self.client.post(reverse("interactions:toggle_follow", args=["category", self.category.pk]))
        self.assertFalse(Follow.objects.exists())

    def test_open_redirect_via_next_is_ignored(self):
        self.client.force_login(self.follower)
        response = self.client.post(
            reverse("interactions:toggle_follow", args=["category", self.category.pk]), {"next": "https://evil.example/"},
        )
        self.assertEqual(response["Location"], reverse("interactions:following"))

    def test_category_follower_notified_of_new_post_but_not_author(self):
        self._follow("category", self.category)
        self._follow("category", self.category, user=self.author)
        services.create_post(self.author, self.category, "Bai moi", "noi dung")
        self.assertEqual(self._follow_updates(self.follower), 1)
        self.assertEqual(self._follow_updates(self.author), 0)

    def test_post_pending_review_does_not_notify_until_approved(self):
        SensitiveKeyword.objects.create(keyword="camtu")
        self._follow("category", self.category)
        post, _ = services.create_post(self.author, self.category, "Bai camtu", "noi dung")
        self.assertEqual(self._follow_updates(self.follower), 0)

        staff = User.objects.create_user(email="fl-staff@due.udn.vn", password="Pass1234!", role=User.Role.STAFF)
        self.client.force_login(staff)
        self.client.post(reverse("moderation:approve_content", args=["forumpost", post.pk]))
        self.assertEqual(self._follow_updates(self.follower), 1)

    def test_post_follower_notified_of_new_comment_once(self):
        self._follow("forumpost", self.post)
        self.client.force_login(self.commenter)
        self.client.post(reverse("interactions:add_comment", args=["forumpost", self.post.pk]), {"body": "Binh luan moi"})
        self.assertEqual(self._follow_updates(self.follower), 1)

    def test_follower_who_is_also_post_owner_gets_no_duplicate(self):
        self._follow("forumpost", self.post, user=self.author)
        self.client.force_login(self.commenter)
        self.client.post(reverse("interactions:add_comment", args=["forumpost", self.post.pk]), {"body": "Binh luan moi"})
        notifications = Notification.objects.filter(recipient=self.author)
        self.assertEqual(notifications.count(), 1)
        self.assertEqual(notifications.get().verb, Notification.Verb.COMMENT)

    def test_pending_review_comment_does_not_notify_followers(self):
        SensitiveKeyword.objects.create(keyword="camtu")
        self._follow("forumpost", self.post)
        self.client.force_login(self.commenter)
        self.client.post(reverse("interactions:add_comment", args=["forumpost", self.post.pk]), {"body": "co camtu"})
        self.assertFalse(Notification.objects.filter(recipient=self.follower).exists())

    def test_notify_on_follow_preference_is_respected(self):
        NotificationPreference.objects.create(user=self.follower, notify_on_follow=False)
        self._follow("category", self.category)
        services.create_post(self.author, self.category, "Bai moi", "noi dung")
        self.assertFalse(Notification.objects.filter(recipient=self.follower).exists())

    def test_following_list_shows_targets(self):
        self._follow("category", self.category)
        self._follow("forumpost", self.post)
        response = self.client.get(reverse("interactions:following"))
        self.assertContains(response, "FollowCat")
        self.assertContains(response, "Bai theo doi")
