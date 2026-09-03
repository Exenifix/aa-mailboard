from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from esi.exceptions import HTTPClientError

from ..models import PendingEveMail, Ticket, TicketMessage
from ..tasks import check_assignee_timeouts, collect_mails, send_pending_mails
from .utils import (
    create_board_owner,
    create_character,
    create_ticket_direct,
    create_user,
)

TASKS_PATH = "mailboard.tasks"


def mail_header(mail_id, from_id, subject):
    return SimpleNamespace(mail_id=mail_id, from_=from_id, subject=subject, timestamp=timezone.now())


def mail_content(body):
    return SimpleNamespace(body=body)


@patch(f"{TASKS_PATH}.notify_admins_throttled")
@patch(f"{TASKS_PATH}._get_owner_token")
@patch(f"{TASKS_PATH}.esi")
class TestCollectMails(TestCase):
    def setUp(self):
        self.owner = create_board_owner(9001, "Board Owner")
        self.client_char = create_character(1101, "Client Char")

    def _setup_esi(self, mock_esi, headers, bodies):
        """Configure the mocked ESI client with mail headers and bodies."""
        mock_esi.client.Mail.GetCharactersCharacterIdMail.return_value.result.return_value = headers
        mock_esi.client.Mail.GetCharactersCharacterIdMailMailId.side_effect = lambda **kwargs: MagicMock(
            result=MagicMock(return_value=bodies[kwargs["mail_id"]])
        )

    def test_creates_ticket_from_new_mail(self, mock_esi, mock_token, mock_notify):
        self._setup_esi(
            mock_esi,
            [mail_header(101, 1101, "My stuff broke")],
            {101: mail_content("Hello<br>please help")},
        )
        collect_mails()
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.title, "My stuff broke")
        self.assertEqual(ticket.creator_character, self.client_char)
        self.assertEqual(ticket.source, Ticket.SOURCE_MAIL)
        message = ticket.messages.get()
        self.assertEqual(message.type, TicketMessage.TYPE_CLIENT)
        self.assertEqual(message.content, "Hello\nplease help")
        self.assertEqual(message.mail_id, 101)
        # confirmation mail is queued for mail-created tickets
        mail = PendingEveMail.objects.get()
        self.assertEqual(mail.recipient, self.client_char)
        self.assertIn(ticket.mail_tag, mail.subject)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.last_seen_mail_id, 101)
        self.assertFalse(mock_notify.called)

    def test_appends_message_to_existing_ticket(self, mock_esi, mock_token, mock_notify):
        ticket = create_ticket_direct(self.client_char, source=Ticket.SOURCE_MAIL, is_closed=True)
        self._setup_esi(
            mock_esi,
            [mail_header(102, 1101, f"Re: {ticket.mail_tag} Test ticket")],
            {102: mail_content("More info")},
        )
        collect_mails()
        self.assertEqual(Ticket.objects.count(), 1)
        ticket.refresh_from_db()
        self.assertFalse(ticket.is_closed)  # reopened by client reply
        self.assertEqual(ticket.messages.count(), 2)
        self.assertEqual(ticket.messages.last().content, "More info")

    def test_mail_with_stolen_tag_from_other_character_creates_new_ticket(self, mock_esi, mock_token, mock_notify):
        """IDOR protection: a valid tag from the wrong sender must not be
        able to inject messages into the ticket."""
        ticket = create_ticket_direct(self.client_char, source=Ticket.SOURCE_MAIL)
        attacker = create_character(6666, "Evil Attacker")
        self._setup_esi(
            mock_esi,
            [mail_header(103, attacker.character_id, f"Re: {ticket.mail_tag} hi")],
            {103: mail_content("Injected message")},
        )
        collect_mails()
        self.assertEqual(ticket.messages.count(), 1)
        self.assertEqual(Ticket.objects.count(), 2)
        new_ticket = Ticket.objects.exclude(pk=ticket.pk).get()
        self.assertEqual(new_ticket.creator_character, attacker)
        # the stolen tag is stripped from the new ticket title
        self.assertNotIn(ticket.mail_key, new_ticket.title)

    def test_mail_with_wrong_key_creates_new_ticket(self, mock_esi, mock_token, mock_notify):
        ticket = create_ticket_direct(self.client_char, source=Ticket.SOURCE_MAIL)
        self._setup_esi(
            mock_esi,
            [mail_header(104, 1101, f"Re: [MB{ticket.pk}-deadbeef] hi")],
            {104: mail_content("Guessed the id")},
        )
        collect_mails()
        self.assertEqual(ticket.messages.count(), 1)
        self.assertEqual(Ticket.objects.count(), 2)

    def test_skips_already_processed_mails(self, mock_esi, mock_token, mock_notify):
        ticket = create_ticket_direct(self.client_char)
        ticket.messages.update(mail_id=105)
        self._setup_esi(
            mock_esi,
            [mail_header(105, 1101, "Old mail")],
            {105: mail_content("Old content")},
        )
        collect_mails()
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(TicketMessage.objects.count(), 1)

    def test_skips_mails_from_board_owners(self, mock_esi, mock_token, mock_notify):
        other_owner = create_board_owner(9002, "Second Owner")
        self._setup_esi(
            mock_esi,
            [mail_header(106, other_owner.character.character_id, "Internal")],
            {},
        )
        collect_mails()
        self.assertFalse(Ticket.objects.exists())

    def test_skips_mails_older_than_last_seen(self, mock_esi, mock_token, mock_notify):
        self.owner.last_seen_mail_id = 200
        self.owner.save()
        self._setup_esi(mock_esi, [mail_header(150, 1101, "Old mail")], {})
        collect_mails()
        self.assertFalse(Ticket.objects.exists())

    def test_notifies_admins_when_owner_fails(self, mock_esi, mock_token, mock_notify):
        mock_token.return_value = None  # no valid token
        collect_mails()
        self.assertTrue(mock_notify.called)
        self.assertIn("Board Owner", mock_notify.call_args.kwargs["message"])

    def test_ignores_disabled_owners(self, mock_esi, mock_token, mock_notify):
        self.owner.enabled = False
        self.owner.save()
        collect_mails()
        self.assertFalse(mock_esi.client.Mail.GetCharactersCharacterIdMail.called)
        self.assertFalse(mock_notify.called)


@patch(f"{TASKS_PATH}.notify_admins_throttled")
@patch(f"{TASKS_PATH}._get_owner_token")
@patch(f"{TASKS_PATH}.esi")
class TestSendPendingMails(TestCase):
    def setUp(self):
        self.owner = create_board_owner(9001, "Board Owner")
        self.recipient = create_character(1101, "Client Char")
        self.mail = PendingEveMail.objects.create(recipient=self.recipient, subject="Test subject", content="Test body")

    def test_sends_mail_and_deletes_it(self, mock_esi, mock_token, mock_notify):
        send_pending_mails()
        self.assertFalse(PendingEveMail.objects.exists())
        call = mock_esi.client.Mail.PostCharactersCharacterIdMail.call_args
        self.assertEqual(call.kwargs["character_id"], self.owner.character.character_id)
        body = call.kwargs["body"]
        self.assertEqual(body["subject"], "Test subject")
        self.assertEqual(body["body"], "Test body")
        self.assertEqual(
            body["recipients"],
            [{"recipient_id": 1101, "recipient_type": "character"}],
        )
        self.assertFalse(mock_notify.called)

    def test_notifies_admins_when_no_owner_has_token(self, mock_esi, mock_token, mock_notify):
        mock_token.return_value = None
        send_pending_mails()
        self.assertTrue(PendingEveMail.objects.exists())
        self.assertTrue(mock_notify.called)

    def test_notifies_admins_when_no_owners_configured(self, mock_esi, mock_token, mock_notify):
        self.owner.delete()
        send_pending_mails()
        self.assertTrue(mock_notify.called)

    def test_falls_back_to_next_owner_on_auth_error(self, mock_esi, mock_token, mock_notify):
        owner2 = create_board_owner(9002, "Second Owner")
        calls = []

        def post_mail(**kwargs):
            calls.append(kwargs["character_id"])
            if kwargs["character_id"] == self.owner.character.character_id:
                raise HTTPClientError(403, {}, None)
            return MagicMock()

        mock_esi.client.Mail.PostCharactersCharacterIdMail.side_effect = post_mail
        send_pending_mails()
        self.assertEqual(
            calls,
            [
                self.owner.character.character_id,
                owner2.character.character_id,
            ],
        )
        self.assertFalse(PendingEveMail.objects.exists())

    def test_mail_specific_error_increments_attempts(self, mock_esi, mock_token, mock_notify):
        mock_esi.client.Mail.PostCharactersCharacterIdMail.return_value.result.side_effect = HTTPClientError(
            400, {}, None
        )
        send_pending_mails()
        self.mail.refresh_from_db()
        self.assertEqual(self.mail.attempts, 1)

    @patch(f"{TASKS_PATH}.MAILBOARD_MAX_SEND_ATTEMPTS", 2)
    def test_mail_dropped_after_max_attempts(self, mock_esi, mock_token, mock_notify):
        self.mail.attempts = 1
        self.mail.save()
        mock_esi.client.Mail.PostCharactersCharacterIdMail.return_value.result.side_effect = HTTPClientError(
            400, {}, None
        )
        send_pending_mails()
        self.assertFalse(PendingEveMail.objects.exists())
        self.assertTrue(mock_notify.called)

    def test_does_nothing_without_pending_mails(self, mock_esi, mock_token, mock_notify):
        PendingEveMail.objects.all().delete()
        send_pending_mails()
        self.assertFalse(mock_esi.client.Mail.PostCharactersCharacterIdMail.called)
        self.assertFalse(mock_notify.called)


class TestAssigneeTimeouts(TestCase):
    def setUp(self):
        self.staff = create_user(1001, "Staff Member", ["mailboard.board_staff"])
        self.creator = create_character(1101, "Client Char")

    def _assigned_ticket(self, days_ago):
        ticket = create_ticket_direct(self.creator)
        ticket.assignee = self.staff
        ticket.assigned_at = timezone.now() - timezone.timedelta(days=days_ago)
        ticket.save()
        return ticket

    @patch(f"{TASKS_PATH}.MAILBOARD_TICKET_ASSIGNEE_TIMEOUT", 3)
    def test_releases_expired_assignments(self):
        expired = self._assigned_ticket(days_ago=4)
        recent = self._assigned_ticket(days_ago=1)
        check_assignee_timeouts()
        expired.refresh_from_db()
        recent.refresh_from_db()
        self.assertIsNone(expired.assignee)
        self.assertEqual(recent.assignee, self.staff)

    @patch(f"{TASKS_PATH}.MAILBOARD_TICKET_ASSIGNEE_TIMEOUT", 3)
    def test_keeps_closed_tickets_assigned(self):
        ticket = self._assigned_ticket(days_ago=10)
        ticket.is_closed = True
        ticket.save()
        check_assignee_timeouts()
        ticket.refresh_from_db()
        self.assertEqual(ticket.assignee, self.staff)

    @patch(f"{TASKS_PATH}.MAILBOARD_TICKET_ASSIGNEE_TIMEOUT", 0)
    def test_disabled_when_timeout_is_zero(self):
        ticket = self._assigned_ticket(days_ago=100)
        check_assignee_timeouts()
        ticket.refresh_from_db()
        self.assertEqual(ticket.assignee, self.staff)
