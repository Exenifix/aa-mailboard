from unittest.mock import patch

from django.test import TestCase

from ..models import DiscordWebhook, TicketCategory
from ..tasks import notify_ticket_webhooks
from .utils import create_character, create_ticket_direct

TASKS_PATH = "mailboard.tasks"


@patch(f"{TASKS_PATH}.requests.post")
class TestNotifyTicketWebhooks(TestCase):
    def setUp(self):
        self.creator = create_character(1101, "Client Char")
        self.category = TicketCategory.objects.create(name="Alpha")
        self.webhook = DiscordWebhook.objects.create(name="hook", url="https://discord.example.com/hook")
        self.webhook.categories.add(self.category)

    def test_notifies_matching_webhook(self, mock_post):
        ticket = create_ticket_direct(self.creator, category=self.category)
        notify_ticket_webhooks(ticket.pk)
        self.assertTrue(mock_post.called)
        self.assertEqual(mock_post.call_args.args[0], self.webhook.url)
        embed = mock_post.call_args.kwargs["json"]["embeds"][0]
        self.assertIn(ticket.title, embed["title"])
        self.assertEqual(embed["description"], "Please help")
        self.assertEqual(embed["fields"][0]["value"], "Alpha")

    def test_does_not_notify_non_matching_webhook(self, mock_post):
        ticket = create_ticket_direct(self.creator)  # no category
        notify_ticket_webhooks(ticket.pk)
        self.assertFalse(mock_post.called)

    def test_respects_discord_length_limits(self, mock_post):
        ticket = create_ticket_direct(self.creator, category=self.category)
        ticket.messages.update(content="x" * 8000)
        ticket.title = "y" * 255
        ticket.save()
        notify_ticket_webhooks(ticket.pk)
        embed = mock_post.call_args.kwargs["json"]["embeds"][0]
        self.assertLessEqual(len(embed["title"]), 256)
        self.assertLessEqual(len(embed["description"]), 4096)

    def test_handles_deleted_ticket(self, mock_post):
        notify_ticket_webhooks(999)
        self.assertFalse(mock_post.called)

    def test_survives_webhook_error(self, mock_post):
        import requests as requests_lib

        mock_post.side_effect = requests_lib.ConnectionError
        ticket = create_ticket_direct(self.creator, category=self.category)
        notify_ticket_webhooks(ticket.pk)  # must not raise
