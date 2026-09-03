from django.test import TestCase

from ..helpers import (
    build_mail_body,
    build_reply_subject,
    clean_mail_body,
    find_ticket_for_mail,
    strip_mail_tag,
    truncate,
)
from ..models import ESI_MAIL_MAX_BODY, ESI_MAIL_MAX_SUBJECT, DiscordWebhook, TicketCategory, TicketMessage
from .utils import create_character, create_ticket_direct


class TestMailTagMatching(TestCase):
    def setUp(self):
        self.creator = create_character(1001, "Bruce Wayne")
        self.ticket = create_ticket_direct(self.creator)

    def test_should_find_ticket_with_valid_tag_and_sender(self):
        subject = f"Re: {self.ticket.mail_tag} Test ticket"
        result = find_ticket_for_mail(subject, self.creator.character_id)
        self.assertEqual(result, self.ticket)

    def test_should_not_find_ticket_with_wrong_key(self):
        subject = f"Re: [MB{self.ticket.pk}-deadbeef] Test ticket"
        self.assertIsNone(find_ticket_for_mail(subject, self.creator.character_id))

    def test_should_not_find_ticket_for_wrong_sender(self):
        """Someone who knows the tag but is not the creator cannot inject."""
        subject = f"Re: {self.ticket.mail_tag} Test ticket"
        self.assertIsNone(find_ticket_for_mail(subject, 666))

    def test_should_not_find_ticket_without_tag(self):
        self.assertIsNone(find_ticket_for_mail("Hello", self.creator.character_id))

    def test_should_not_find_ticket_for_unknown_id(self):
        subject = f"[MB{self.ticket.pk + 999}-{self.ticket.mail_key}]"
        self.assertIsNone(find_ticket_for_mail(subject, self.creator.character_id))

    def test_strip_mail_tag(self):
        subject = f"Re: {self.ticket.mail_tag} Hello there"
        stripped = strip_mail_tag(subject)
        self.assertNotIn(self.ticket.mail_key, stripped)
        self.assertIn("Hello there", stripped)


class TestMailComposition(TestCase):
    def setUp(self):
        self.creator = create_character(1001, "Bruce Wayne")
        self.ticket = create_ticket_direct(self.creator)

    def test_reply_subject_contains_tag_and_respects_limit(self):
        self.ticket.title = "x" * 500
        subject = build_reply_subject(self.ticket)
        self.assertIn(self.ticket.mail_tag, subject)
        self.assertLessEqual(len(subject), ESI_MAIL_MAX_SUBJECT)

    def test_mail_body_contains_tag_and_respects_limit(self):
        # the latest message carries the ticket tag, which is echoed back to the
        # client so a reply can be correlated to this ticket
        self.ticket.messages.create(
            type=TicketMessage.TYPE_STAFF,
            content=f"Thanks! See {self.ticket.mail_tag} for reference.",
        )
        body = build_mail_body(self.ticket)
        self.assertIn(self.ticket.mail_tag, body)
        self.assertLessEqual(len(body), ESI_MAIL_MAX_BODY)

    def test_clean_mail_body(self):
        raw = 'Hello<br>World<br/><font color="#ffffff">colored &amp; fun</font>'
        self.assertEqual(clean_mail_body(raw), "Hello\nWorld\ncolored & fun")

    def test_truncate(self):
        self.assertEqual(truncate("abc", 10), "abc")
        self.assertEqual(len(truncate("a" * 20, 10)), 10)


class TestWebhookMatching(TestCase):
    def setUp(self):
        self.cat_a = TicketCategory.objects.create(name="Alpha")
        self.cat_b = TicketCategory.objects.create(name="Bravo")

    def _webhook(self, **kwargs):
        webhook = DiscordWebhook.objects.create(name="hook", url="https://discord.example.com/hook", **kwargs)
        return webhook

    def test_category_webhook_matches_its_category_only(self):
        webhook = self._webhook()
        webhook.categories.add(self.cat_a)
        self.assertIn(webhook, DiscordWebhook.webhooks_for_category(self.cat_a))
        self.assertNotIn(webhook, DiscordWebhook.webhooks_for_category(self.cat_b))
        self.assertNotIn(webhook, DiscordWebhook.webhooks_for_category(None))

    def test_all_categories_webhook_matches_everything(self):
        webhook = self._webhook(all_categories=True)
        self.assertIn(webhook, DiscordWebhook.webhooks_for_category(self.cat_a))
        self.assertIn(webhook, DiscordWebhook.webhooks_for_category(None))

    def test_no_category_webhook_matches_null_category_only(self):
        webhook = self._webhook(no_category=True)
        self.assertNotIn(webhook, DiscordWebhook.webhooks_for_category(self.cat_a))
        self.assertIn(webhook, DiscordWebhook.webhooks_for_category(None))

    def test_disabled_webhook_never_matches(self):
        webhook = self._webhook(all_categories=True, enabled=False)
        self.assertNotIn(webhook, DiscordWebhook.webhooks_for_category(self.cat_a))
        self.assertNotIn(webhook, DiscordWebhook.webhooks_for_category(None))
