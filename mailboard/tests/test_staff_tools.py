"""Tests for ticket locks, the mailboard admin role, staff notes and
category keywords."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..helpers import build_mail_body, match_category_for_title
from ..models import PendingEveMail, Ticket, TicketCategory, TicketMessage
from ..tasks import check_assignee_timeouts, collect_mails
from .utils import (
    create_board_owner,
    create_character,
    create_ticket_direct,
    create_user,
)

TASKS_PATH = "mailboard.tasks"


class ApiTestBase(TestCase):
    def setUp(self):
        self.staff = create_user(1002, "Staff Member", ["mailboard.board_staff"])
        self.other_staff = create_user(1003, "Other Staff", ["mailboard.board_staff"])
        self.admin = create_user(1004, "Board Admin", ["mailboard.mailboard_admin"])
        self.creator = create_character(1001, "Bruce Wayne")
        self.ticket = create_ticket_direct(self.creator)

    def _post(self, url_name, ticket=None, payload=None):
        args = [ticket.pk] if ticket else []
        return self.client.post(
            reverse(f"mailboard:{url_name}", args=args),
            data=json.dumps(payload or {}),
            content_type="application/json",
        )


class TestLocking(ApiTestBase):
    def test_lock_assigns_and_locks(self):
        self.client.force_login(self.staff)
        response = self._post("api_ticket_lock", self.ticket)
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_locked)
        self.assertEqual(self.ticket.assignee, self.staff)
        self.assertIsNotNone(self.ticket.assigned_at)

    def test_cannot_lock_ticket_locked_by_other(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.other_staff)
        response = self._post("api_ticket_lock", self.ticket)
        self.assertEqual(response.status_code, 409)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assignee, self.staff)

    def test_admin_can_take_over_lock(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.admin)
        response = self._post("api_ticket_lock", self.ticket)
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_locked)
        self.assertEqual(self.ticket.assignee, self.admin)

    def test_cannot_lock_closed_ticket(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(is_closed=True)
        self.client.force_login(self.staff)
        self.assertEqual(self._post("api_ticket_lock", self.ticket).status_code, 400)

    def test_unlock_by_owner_keeps_assignee(self):
        self.client.force_login(self.staff)
        self._post("api_ticket_lock", self.ticket)
        response = self._post("api_ticket_unlock", self.ticket)
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_locked)
        self.assertEqual(self.ticket.assignee, self.staff)

    def test_unlock_by_other_staff_forbidden(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.other_staff)
        self.assertEqual(self._post("api_ticket_unlock", self.ticket).status_code, 403)

    def test_unlock_by_admin_allowed(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.admin)
        self.assertEqual(self._post("api_ticket_unlock", self.ticket).status_code, 200)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_locked)

    def test_admin_can_reply_to_locked_ticket(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.admin)
        response = self._post(
            "api_ticket_reply", self.ticket, {"action": "send", "content": "Admin here"}
        )
        self.assertEqual(response.status_code, 200)

    def test_locked_blocks_category_change_for_others(self):
        category = TicketCategory.objects.create(name="Alpha")
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.other_staff)
        response = self._post("api_ticket_category", self.ticket, {"category_id": category.pk})
        self.assertEqual(response.status_code, 409)
        # owner can
        self.client.force_login(self.staff)
        response = self._post("api_ticket_category", self.ticket, {"category_id": category.pk})
        self.assertEqual(response.status_code, 200)

    def test_locked_blocks_close_for_others(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.other_staff)
        response = self._post("api_ticket_reply", self.ticket, {"action": "close"})
        self.assertEqual(response.status_code, 409)

    def test_closing_releases_lock(self):
        self.client.force_login(self.staff)
        self._post("api_ticket_lock", self.ticket)
        response = self._post("api_ticket_reply", self.ticket, {"action": "close"})
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.is_closed)
        self.assertFalse(self.ticket.is_locked)

    def test_assignee_timeout_releases_lock(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(
            is_locked=True, assigned_at=timezone.now() - timedelta(days=10)
        )
        check_assignee_timeouts()
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assignee)
        self.assertFalse(self.ticket.is_locked)


class TestAdminRole(ApiTestBase):
    def test_admin_without_staff_perm_can_use_api(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("mailboard:api_tickets"))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_open_dashboard(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("mailboard:dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_assign_requires_admin(self):
        self.client.force_login(self.staff)
        response = self._post("api_ticket_assign", self.ticket, {"user_id": self.other_staff.pk})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_assign_staff(self):
        self.client.force_login(self.admin)
        response = self._post("api_ticket_assign", self.ticket, {"user_id": self.other_staff.pk})
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assignee, self.other_staff)

    def test_admin_cannot_assign_non_staff(self):
        outsider = create_user(1005, "Random User")
        self.client.force_login(self.admin)
        response = self._post("api_ticket_assign", self.ticket, {"user_id": outsider.pk})
        self.assertEqual(response.status_code, 400)

    def test_admin_unassign_clears_lock(self):
        self.ticket.assign_to(self.staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.admin)
        response = self._post("api_ticket_assign", self.ticket, {"user_id": None})
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assignee)
        self.assertFalse(self.ticket.is_locked)


class TestStaffNotes(ApiTestBase):
    def test_note_does_not_assign_or_mail(self):
        self.client.force_login(self.staff)
        response = self._post(
            "api_ticket_reply", self.ticket, {"action": "note", "content": "Internal info"}
        )
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.assignee)
        self.assertFalse(PendingEveMail.objects.exists())
        note = self.ticket.messages.get(type=TicketMessage.TYPE_NOTE)
        self.assertEqual(note.staff, self.staff)
        self.assertEqual(note.content, "Internal info")

    def test_note_requires_content(self):
        self.client.force_login(self.staff)
        response = self._post("api_ticket_reply", self.ticket, {"action": "note", "content": " "})
        self.assertEqual(response.status_code, 400)

    def test_note_allowed_on_ticket_locked_by_other(self):
        self.ticket.assign_to(self.other_staff)
        Ticket.objects.filter(pk=self.ticket.pk).update(is_locked=True)
        self.client.force_login(self.staff)
        response = self._post(
            "api_ticket_reply", self.ticket, {"action": "note", "content": "FYI"}
        )
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.assignee, self.other_staff)

    def test_note_does_not_clear_new_messages_flag(self):
        Ticket.objects.filter(pk=self.ticket.pk).update(has_new_messages=True)
        self.client.force_login(self.staff)
        self._post("api_ticket_reply", self.ticket, {"action": "note", "content": "FYI"})
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.has_new_messages)

    def test_notes_excluded_from_outgoing_mail_body(self):
        TicketMessage.objects.create(
            ticket=self.ticket,
            type=TicketMessage.TYPE_NOTE,
            staff=self.staff,
            content="SECRET-NOTE-CONTENT",
        )
        TicketMessage.objects.create(
            ticket=self.ticket,
            type=TicketMessage.TYPE_STAFF,
            staff=self.staff,
            content="Public reply",
        )
        body = build_mail_body(self.ticket)
        self.assertIn("Public reply", body)
        self.assertNotIn("SECRET-NOTE-CONTENT", body)

    def test_notes_visible_in_detail_api(self):
        TicketMessage.objects.create(
            ticket=self.ticket,
            type=TicketMessage.TYPE_NOTE,
            staff=self.staff,
            content="Internal info",
        )
        self.client.force_login(self.other_staff)
        response = self.client.get(reverse("mailboard:api_ticket_detail", args=[self.ticket.pk]))
        types = [message["type"] for message in response.json()["messages"]]
        self.assertIn("note", types)


class TestCategoryKeywords(TestCase):
    def setUp(self):
        self.cat_srp = TicketCategory.objects.create(name="SRP", keywords="srp, reimbursement, ship lost")
        self.cat_recruit = TicketCategory.objects.create(name="Recruitment", keywords="join, recruit")

    def test_best_match_wins(self):
        self.assertEqual(match_category_for_title("SRP reimbursement please"), self.cat_srp)
        self.assertEqual(match_category_for_title("I want to JOIN you"), self.cat_recruit)

    def test_no_match_returns_none(self):
        self.assertIsNone(match_category_for_title("hello there"))
        self.assertIsNone(match_category_for_title(""))

    def test_disabled_category_ignored(self):
        self.cat_srp.enabled = False
        self.cat_srp.save()
        self.assertIsNone(match_category_for_title("srp please"))

    @patch(f"{TASKS_PATH}.notify_admins_throttled")
    @patch(f"{TASKS_PATH}._get_owner_token")
    @patch(f"{TASKS_PATH}.esi")
    def test_mail_ticket_gets_keyword_category(self, mock_esi, mock_token, mock_notify):
        create_board_owner(9001, "Board Owner")
        create_character(1101, "Client Char")
        header = SimpleNamespace(
            mail_id=101, from_=1101, subject="Need SRP for my ship", timestamp=timezone.now()
        )
        mock_esi.client.Mail.GetCharactersCharacterIdMail.return_value.result.return_value = [header]
        mock_esi.client.Mail.GetCharactersCharacterIdMailMailId.return_value = MagicMock(
            result=MagicMock(return_value=SimpleNamespace(body="my ship exploded"))
        )
        collect_mails()
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.category, self.cat_srp)
