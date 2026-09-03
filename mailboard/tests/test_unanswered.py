from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from ..models import Ticket, TicketMessage
from .utils import create_character


class TestIsUnanswered(TestCase):
    def _ticket(self):
        return Ticket.objects.create(
            title="Test ticket",
            creator_character=create_character(),
            source=Ticket.SOURCE_WEB,
        )

    def test_no_messages_is_not_unanswered(self):
        self.assertFalse(self._ticket().is_unanswered)

    def test_client_message_is_unanswered(self):
        ticket = self._ticket()
        TicketMessage.objects.create(ticket=ticket, type=TicketMessage.TYPE_CLIENT, content="help")
        self.assertTrue(ticket.is_unanswered)

    def test_staff_reply_clears_unanswered(self):
        ticket = self._ticket()
        TicketMessage.objects.create(ticket=ticket, type=TicketMessage.TYPE_CLIENT, content="help")
        TicketMessage.objects.create(ticket=ticket, type=TicketMessage.TYPE_STAFF, content="thanks")
        self.assertFalse(ticket.is_unanswered)

    def test_newer_client_message_sets_unanswered_again(self):
        ticket = self._ticket()
        t0 = timezone.now()
        TicketMessage.objects.create(ticket=ticket, type=TicketMessage.TYPE_CLIENT, content="help", timestamp=t0)
        TicketMessage.objects.create(ticket=ticket, type=TicketMessage.TYPE_STAFF, content="thanks", timestamp=t0)
        TicketMessage.objects.create(
            ticket=ticket,
            type=TicketMessage.TYPE_CLIENT,
            content="more",
            timestamp=t0 + timedelta(seconds=1),
        )
        self.assertTrue(ticket.is_unanswered)
