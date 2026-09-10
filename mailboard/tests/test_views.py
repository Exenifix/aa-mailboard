import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from ..models import PendingEveMail, Ticket, TicketCategory, TicketMessage
from .utils import create_character, create_ticket_direct, create_user

VIEWS_PATH = "mailboard.views"


class TestIndex(TestCase):
    def test_requires_login(self):
        response = self.client.get(reverse("mailboard:index"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_forbidden_without_any_permission(self):
        user = create_user(1001, "No Perms")
        self.client.force_login(user)
        response = self.client.get(reverse("mailboard:index"))
        self.assertEqual(response.status_code, 403)

    def test_staff_redirected_to_dashboard(self):
        user = create_user(1002, "Staff Member", ["mailboard.board_staff"])
        self.client.force_login(user)
        response = self.client.get(reverse("mailboard:index"))
        self.assertRedirects(response, reverse("mailboard:dashboard"))

    def test_board_user_redirected_to_new_ticket(self):
        user = create_user(1003, "Normal User", ["mailboard.board_user"])
        self.client.force_login(user)
        response = self.client.get(reverse("mailboard:index"))
        self.assertRedirects(response, reverse("mailboard:new_ticket"))

    def test_board_owner_sees_landing_page(self):
        user = create_user(1004, "Owner Only", ["mailboard.board_owner"])
        self.client.force_login(user)
        response = self.client.get(reverse("mailboard:index"))
        self.assertEqual(response.status_code, 200)


class TestNewTicket(TestCase):
    def setUp(self):
        cache.clear()
        self.user = create_user(1001, "Bruce Wayne", ["mailboard.board_user"])
        self.category = TicketCategory.objects.create(name="Alpha")

    def test_requires_permission(self):
        user = create_user(1005, "No Perms")
        self.client.force_login(user)
        response = self.client.get(reverse("mailboard:new_ticket"))
        self.assertEqual(response.status_code, 302)

    def test_get_renders_form(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("mailboard:new_ticket"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Alpha")

    def test_creates_ticket_with_category(self):
        self.client.force_login(self.user)
        with patch("mailboard.tasks.notify_ticket_webhooks.delay") as mock_notify:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("mailboard:new_ticket"),
                    {
                        "title": "Help me",
                        "category": self.category.pk,
                        "content": "Something broke",
                    },
                )
        self.assertRedirects(response, reverse("mailboard:new_ticket"))
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.title, "Help me")
        self.assertEqual(ticket.category, self.category)
        self.assertEqual(ticket.source, Ticket.SOURCE_WEB)
        self.assertEqual(ticket.creator_character, self.user.profile.main_character)
        message = ticket.messages.get()
        self.assertEqual(message.type, TicketMessage.TYPE_CLIENT)
        self.assertEqual(message.content, "Something broke")
        self.assertTrue(mock_notify.called)
        # web tickets do not generate a confirmation eve mail
        self.assertFalse(PendingEveMail.objects.exists())

    def test_creates_ticket_without_category(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse("mailboard:new_ticket"),
            {"title": "Help me", "category": "", "content": "Something broke"},
        )
        self.assertIsNone(Ticket.objects.get().category)

    def test_rejects_missing_fields(self):
        self.client.force_login(self.user)
        self.client.post(reverse("mailboard:new_ticket"), {"title": "", "content": ""})
        self.assertFalse(Ticket.objects.exists())

    def test_rejects_disabled_category(self):
        self.category.enabled = False
        self.category.save()
        self.client.force_login(self.user)
        self.client.post(
            reverse("mailboard:new_ticket"),
            {"title": "Help", "category": self.category.pk, "content": "Broke"},
        )
        self.assertFalse(Ticket.objects.exists())

    @patch(f"{VIEWS_PATH}.MAILBOARD_MAX_OPEN_TICKETS", 2)
    @patch(f"{VIEWS_PATH}.MAILBOARD_TICKET_CREATE_COOLDOWN", 0)
    def test_enforces_open_ticket_limit(self):
        main = self.user.profile.main_character
        create_ticket_direct(main)
        create_ticket_direct(main)
        self.client.force_login(self.user)
        self.client.post(
            reverse("mailboard:new_ticket"),
            {"title": "One too many", "content": "Please"},
        )
        self.assertEqual(Ticket.objects.count(), 2)
        # closed tickets do not count towards the limit
        Ticket.objects.all().update(is_closed=True)
        self.client.post(
            reverse("mailboard:new_ticket"),
            {"title": "Now it works", "content": "Please"},
        )
        self.assertEqual(Ticket.objects.count(), 3)

    @patch(f"{VIEWS_PATH}.MAILBOARD_TICKET_CREATE_COOLDOWN", 3600)
    def test_enforces_creation_cooldown(self):
        self.client.force_login(self.user)
        self.client.post(reverse("mailboard:new_ticket"), {"title": "First", "content": "a"})
        self.client.post(reverse("mailboard:new_ticket"), {"title": "Second", "content": "b"})
        self.assertEqual(Ticket.objects.count(), 1)


class TestDashboardApi(TestCase):
    def setUp(self):
        self.staff = create_user(1001, "Staff Member", ["mailboard.board_staff"])
        self.other_staff = create_user(1002, "Other Staff", ["mailboard.board_staff"])
        self.creator = create_character(1101, "Client Char")
        self.category = TicketCategory.objects.create(name="Alpha")
        self.ticket = create_ticket_direct(self.creator, category=self.category)

    def test_dashboard_page_requires_permission(self):
        user = create_user(1005, "No Perms")
        self.client.force_login(user)
        response = self.client.get(reverse("mailboard:dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_page_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("mailboard:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_api_requires_staff_permission(self):
        user = create_user(1005, "No Perms", ["mailboard.board_user"])
        self.client.force_login(user)
        for url in (
            reverse("mailboard:api_tickets"),
            reverse("mailboard:api_ticket_detail", args=[self.ticket.pk]),
        ):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 403, url)
        response = self.client.post(reverse("mailboard:api_ticket_reply", args=[self.ticket.pk]))
        self.assertEqual(response.status_code, 403)

    def test_list_defaults_to_open_unassigned(self):
        assigned = create_ticket_direct(self.creator, title="Assigned")
        assigned.assign_to(self.other_staff)
        closed = create_ticket_direct(self.creator, title="Closed", is_closed=True)
        self.client.force_login(self.staff)
        response = self.client.get(reverse("mailboard:api_tickets"))
        ids = [t["id"] for t in response.json()["tickets"]]
        self.assertEqual(ids, [self.ticket.pk])
        response = self.client.get(reverse("mailboard:api_tickets"), {"all": "1"})
        ids = {t["id"] for t in response.json()["tickets"]}
        self.assertEqual(ids, {self.ticket.pk, assigned.pk, closed.pk})

    def test_list_filters(self):
        no_cat = create_ticket_direct(self.creator, title="No category")
        closed = create_ticket_direct(self.creator, title="Closed", is_closed=True, category=self.category)
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("mailboard:api_tickets"),
            {"all": "1", "category": self.category.pk},
        )
        ids = {t["id"] for t in response.json()["tickets"]}
        self.assertEqual(ids, {self.ticket.pk, closed.pk})
        response = self.client.get(reverse("mailboard:api_tickets"), {"all": "1", "category": "none"})
        ids = {t["id"] for t in response.json()["tickets"]}
        self.assertEqual(ids, {no_cat.pk})
        response = self.client.get(reverse("mailboard:api_tickets"), {"all": "1", "status": "closed"})
        ids = {t["id"] for t in response.json()["tickets"]}
        self.assertEqual(ids, {closed.pk})

    def test_detail_returns_messages(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("mailboard:api_ticket_detail", args=[self.ticket.pk]))
        data = response.json()
        self.assertEqual(data["id"], self.ticket.pk)
        self.assertEqual(len(data["messages"]), 1)
        self.assertEqual(data["messages"][0]["author"], "Client Char")

    def test_detail_404_for_unknown_ticket(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("mailboard:api_ticket_detail", args=[999]))
        self.assertEqual(response.status_code, 404)

    def _reply(self, ticket, action, content=""):
        return self.client.post(
            reverse("mailboard:api_ticket_reply", args=[ticket.pk]),
            json.dumps({"action": action, "content": content}),
            content_type="application/json",
        )

    def test_send_creates_message_and_pending_mail_and_assigns(self):
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "send", "We are on it")
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assignee, self.staff)
        self.assertFalse(self.ticket.is_closed)
        message = self.ticket.messages.filter(type=TicketMessage.TYPE_STAFF).get()
        self.assertEqual(message.staff, self.staff)
        self.assertEqual(message.content, "We are on it")
        mail = PendingEveMail.objects.get()
        self.assertEqual(mail.recipient, self.creator)
        self.assertIn(self.ticket.mail_tag, mail.subject)
        self.assertIn("We are on it", mail.content)

    def test_send_and_close(self):
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "send_close", "Fixed, closing")
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_closed)
        self.assertTrue(PendingEveMail.objects.exists())

    def test_close_without_message(self):
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "close")
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_closed)
        # closing queues a "ticket closed" notification mail
        mail = PendingEveMail.objects.get()
        self.assertEqual(mail.recipient, self.creator)
        self.assertIn("closed", mail.content)

    def test_send_rejects_empty_message(self):
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "send", "  ")
        self.assertEqual(response.status_code, 400)

    def test_rejects_unknown_action(self):
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "explode")
        self.assertEqual(response.status_code, 400)

    def test_can_reply_to_ticket_assigned_to_someone_else(self):
        """By default assignment is soft: any staff member can respond."""
        self.ticket.assign_to(self.other_staff)
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "send", "Helping out")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.ticket.messages.filter(type=TicketMessage.TYPE_STAFF).exists())
        self.ticket.refresh_from_db()
        # helping out does not steal the assignment
        self.assertEqual(self.ticket.assignee, self.other_staff)

    def test_cannot_touch_ticket_locked_by_someone_else(self):
        self.ticket.assign_to(self.other_staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.staff)
        response = self._reply(self.ticket, "send", "Mine now")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.ticket.messages.filter(type=TicketMessage.TYPE_STAFF).exists())
