"""Tests for blacklist, new-message flag, ordering, category change and
staff outreach."""

import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..core import add_client_message, create_ticket
from ..models import (
    BlacklistedCharacter,
    PendingEveMail,
    Ticket,
    TicketCategory,
    TicketMessage,
)
from ..tasks import collect_mails
from .utils import (
    create_board_owner,
    create_character,
    create_ticket_direct,
    create_user,
)

TASKS_PATH = "mailboard.tasks"
VIEWS_PATH = "mailboard.views"


def mail_header(mail_id, from_id, subject, timestamp=None):
    return SimpleNamespace(
        mail_id=mail_id,
        from_=from_id,
        subject=subject,
        timestamp=timestamp or timezone.now(),
    )


def mail_content(body):
    return SimpleNamespace(body=body)


@patch(f"{TASKS_PATH}.notify_admins_throttled")
@patch(f"{TASKS_PATH}._get_owner_token")
@patch(f"{TASKS_PATH}.esi")
class TestBlacklist(TestCase):
    def setUp(self):
        self.owner = create_board_owner(9001, "Board Owner")
        self.client_char = create_character(1101, "Client Char")

    def _setup_esi(self, mock_esi, headers, bodies):
        mock_esi.client.Mail.GetCharactersCharacterIdMail.return_value.result.return_value = headers
        mock_esi.client.Mail.GetCharactersCharacterIdMailMailId.side_effect = lambda **kwargs: MagicMock(
            result=MagicMock(return_value=bodies.get(kwargs["mail_id"], mail_content("spam")))
        )

    def test_active_blacklist_ignores_mail(self, mock_esi, mock_token, mock_notify):
        BlacklistedCharacter.objects.create(character_id=1101)
        self._setup_esi(
            mock_esi,
            [mail_header(101, 1101, "Buy my stuff")],
            {101: mail_content("spam")},
        )
        collect_mails()
        self.assertFalse(Ticket.objects.exists())
        self.owner.refresh_from_db()
        # the mail still advances the watermark, so it can never come back
        self.assertEqual(self.owner.last_seen_mail_id, 101)

    def test_mails_before_unblock_stay_ignored(self, mock_esi, mock_token, mock_notify):
        entry = BlacklistedCharacter.objects.create(character_id=1101)
        entry.active = False
        entry.save()
        entry.refresh_from_db()
        self.assertIsNotNone(entry.unblocked_at)

        old_spam = mail_header(101, 1101, "Old spam", timestamp=entry.unblocked_at - timedelta(hours=1))
        new_mail = mail_header(102, 1101, "Legit request", timestamp=entry.unblocked_at + timedelta(hours=1))
        self._setup_esi(
            mock_esi,
            [old_spam, new_mail],
            {101: mail_content("spam"), 102: mail_content("please help")},
        )
        collect_mails()
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.title, "Legit request")
        self.assertFalse(TicketMessage.objects.filter(mail_id=101).exists())

    def test_reblocked_character_is_ignored_again(self, mock_esi, mock_token, mock_notify):
        entry = BlacklistedCharacter.objects.create(character_id=1101)
        entry.active = False
        entry.save()
        entry.active = True
        entry.save()
        self._setup_esi(
            mock_esi,
            [mail_header(103, 1101, "Spam again", timestamp=timezone.now() + timedelta(hours=1))],
            {103: mail_content("spam")},
        )
        collect_mails()
        self.assertFalse(Ticket.objects.exists())


class TestNewMessagesFlag(TestCase):
    def setUp(self):
        self.creator = create_character(1001, "Bruce Wayne")

    def test_set_on_ticket_creation(self):
        with patch("mailboard.tasks.notify_ticket_webhooks"):
            ticket = create_ticket(
                title="Help",
                creator_character=self.creator,
                source=Ticket.SOURCE_WEB,
                content="please",
            )
        self.assertTrue(ticket.has_new_messages)

    def test_set_on_client_message(self):
        ticket = create_ticket_direct(self.creator)
        self.assertFalse(ticket.has_new_messages)
        add_client_message(ticket, "more info")
        ticket.refresh_from_db()
        self.assertTrue(ticket.has_new_messages)

    def test_cleared_on_staff_reply_and_close(self):
        staff = create_user(1002, "Staff Member", ["mailboard.board_staff"])
        self.client.force_login(staff)
        for action in ("send", "close"):
            ticket = create_ticket_direct(self.creator, has_new_messages=True)
            response = self.client.post(
                reverse("mailboard:api_ticket_reply", args=[ticket.pk]),
                data=json.dumps({"action": action, "content": "On it"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            ticket.refresh_from_db()
            self.assertFalse(ticket.has_new_messages)


class TestTicketOrdering(TestCase):
    def setUp(self):
        self.staff = create_user(1002, "Staff Member", ["mailboard.board_staff"])
        self.creator = create_character(1001, "Bruce Wayne")
        now = timezone.now()
        self.t_new_old = self._ticket("new old", now - timedelta(hours=4), new=True)
        self.t_new_recent = self._ticket("new recent", now - timedelta(hours=1), new=True)
        self.t_plain_old = self._ticket("plain old", now - timedelta(hours=3))
        self.t_plain_recent = self._ticket("plain recent", now - timedelta(hours=2))

    def _ticket(self, title, updated_at, new=False):
        ticket = create_ticket_direct(self.creator, title=title)
        Ticket.objects.filter(pk=ticket.pk).update(updated_at=updated_at, has_new_messages=new)
        return ticket

    def _ids(self, **params):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("mailboard:api_tickets"), params)
        return [t["id"] for t in response.json()["tickets"]]

    def test_default_order_new_messages_first_then_oldest_update(self):
        self.assertEqual(
            self._ids(),
            [
                self.t_new_old.pk,
                self.t_new_recent.pk,
                self.t_plain_old.pk,
                self.t_plain_recent.pk,
            ],
        )

    def test_all_view_is_newest_first(self):
        self.assertEqual(
            self._ids(all="1"),
            [
                self.t_new_recent.pk,
                self.t_plain_recent.pk,
                self.t_plain_old.pk,
                self.t_new_old.pk,
            ],
        )


class TestChangeCategory(TestCase):
    def setUp(self):
        self.staff = create_user(1002, "Staff Member", ["mailboard.board_staff"])
        self.creator = create_character(1001, "Bruce Wayne")
        self.category = TicketCategory.objects.create(name="Alpha")
        self.ticket = create_ticket_direct(self.creator)

    def _change(self, ticket, category_id):
        return self.client.post(
            reverse("mailboard:api_ticket_category", args=[ticket.pk]),
            data=json.dumps({"category_id": category_id}),
            content_type="application/json",
        )

    def test_requires_permission(self):
        user = create_user(1005, "No Perms")
        self.client.force_login(user)
        response = self._change(self.ticket, self.category.pk)
        self.assertEqual(response.status_code, 403)

    def test_change_category(self):
        self.client.force_login(self.staff)
        response = self._change(self.ticket, self.category.pk)
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.category, self.category)

    def test_change_to_other(self):
        self.ticket.category = self.category
        self.ticket.save()
        self.client.force_login(self.staff)
        response = self._change(self.ticket, None)
        self.assertEqual(response.status_code, 200)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.category)

    def test_rejected_for_closed_ticket(self):
        self.ticket.is_closed = True
        self.ticket.save()
        self.client.force_login(self.staff)
        response = self._change(self.ticket, self.category.pk)
        self.assertEqual(response.status_code, 400)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.category)

    def test_rejected_for_unknown_category(self):
        self.client.force_login(self.staff)
        response = self._change(self.ticket, 99999)
        self.assertEqual(response.status_code, 400)


@patch(f"{VIEWS_PATH}.esi")
class TestContactCharacter(TestCase):
    def setUp(self):
        self.staff = create_user(1002, "Staff Member", ["mailboard.board_staff"])
        self.target = create_character(1101, "Client Char")
        self.client.force_login(self.staff)

    def _contact(self, **overrides):
        payload = {
            "character": "Client Char",
            "title": "Recruitment",
            "category_id": None,
            "content": "Hello, we would like to talk to you.",
        }
        payload.update(overrides)
        return self.client.post(
            reverse("mailboard:api_contact_character"),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_requires_permission(self, mock_esi):
        user = create_user(1005, "No Perms")
        self.client.force_login(user)
        self.assertEqual(self._contact().status_code, 403)

    def test_contact_existing_character_by_name(self, mock_esi):
        response = self._contact()
        self.assertEqual(response.status_code, 200)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.creator_character, self.target)
        self.assertEqual(ticket.source, Ticket.SOURCE_STAFF)
        self.assertEqual(ticket.assignee, self.staff)
        self.assertFalse(ticket.has_new_messages)
        message = ticket.messages.get()
        self.assertEqual(message.type, TicketMessage.TYPE_STAFF)
        self.assertEqual(message.staff, self.staff)
        mail = PendingEveMail.objects.get()
        self.assertEqual(mail.recipient, self.target)
        self.assertIn(ticket.mail_tag, mail.subject)
        self.assertIn("we would like to talk", mail.content)
        # name already known locally: no ESI call needed
        mock_esi.client.Universe.PostUniverseIds.assert_not_called()

    def test_contact_by_id(self, mock_esi):
        response = self._contact(character=str(self.target.character_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ticket.objects.get().creator_character, self.target)

    def test_contact_resolves_unknown_name_via_esi(self, mock_esi):
        mock_esi.client.Universe.PostUniverseIds.return_value.result.return_value = SimpleNamespace(
            characters=[SimpleNamespace(id=self.target.character_id, name="Client Char")]
        )
        response = self._contact(character="client char alias")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Ticket.objects.get().creator_character, self.target)

    def test_contact_unknown_name_returns_error(self, mock_esi):
        mock_esi.client.Universe.PostUniverseIds.return_value.result.return_value = SimpleNamespace(characters=None)
        response = self._contact(character="Nobody Here")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Ticket.objects.exists())

    def test_contact_blacklisted_character_rejected(self, mock_esi):
        BlacklistedCharacter.objects.create(character_id=self.target.character_id)
        response = self._contact()
        self.assertEqual(response.status_code, 400)
        self.assertIn("blacklisted", response.json()["error"])
        self.assertFalse(Ticket.objects.exists())

    def test_contact_requires_fields(self, mock_esi):
        self.assertEqual(self._contact(character="").status_code, 400)
        self.assertEqual(self._contact(title="").status_code, 400)
        self.assertEqual(self._contact(content="").status_code, 400)
        self.assertFalse(Ticket.objects.exists())

    def test_client_reply_matches_staff_ticket(self, mock_esi):
        """After outreach the ticket behaves like a normal one."""
        from ..helpers import find_ticket_for_mail

        self._contact()
        ticket = Ticket.objects.get()
        subject = f"Re: {ticket.mail_tag} Recruitment"
        self.assertEqual(
            find_ticket_for_mail(subject, self.target.character_id),
            ticket,
        )
