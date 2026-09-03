"""Models."""

import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from allianceauth.eveonline.models import EveCharacter

# ESI hard limits for POST /characters/{character_id}/mail/
ESI_MAIL_MAX_SUBJECT = 1000
ESI_MAIL_MAX_BODY = 10000

# Length of the random secret embedded in mail subject tags.
MAIL_KEY_LENGTH = 8

User = get_user_model()


def generate_mail_key() -> str:
    """Generate a random secret for correlating eve mail replies to a ticket."""
    return secrets.token_hex(MAIL_KEY_LENGTH // 2)


class General(models.Model):
    """A meta model for app permissions."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("board_owner", "Can add a mail receive character (board owner)"),
            ("board_staff", "Can view tickets and respond to them"),
            ("board_user", "Can create tickets via the web interface"),
        )


class TicketCategory(models.Model):
    """A category tickets can be filed under."""

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(null=True, blank=True)
    enabled = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "ticket categories"

    def __str__(self) -> str:
        return str(self.name)


class Ticket(models.Model):
    """A support request created via the web interface or via eve mail."""

    SOURCE_WEB = "web"
    SOURCE_MAIL = "mail"
    SOURCE_CHOICES = (
        (SOURCE_WEB, "Web"),
        (SOURCE_MAIL, "Eve mail"),
    )

    title = models.CharField(max_length=255)
    category = models.ForeignKey(
        TicketCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
    )
    creator_character = models.ForeignKey(EveCharacter, on_delete=models.CASCADE, related_name="+")
    assignee = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mailboard_tickets",
    )
    is_closed = models.BooleanField(default=False)
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES, default=SOURCE_WEB)
    mail_key = models.CharField(
        max_length=MAIL_KEY_LENGTH,
        default=generate_mail_key,
        editable=False,
        help_text="Secret used to authenticate eve mail replies to this ticket.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    assigned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"#{self.pk}: {self.title}"

    @property
    def mail_tag(self) -> str:
        """Subject tag used to correlate eve mails with this ticket."""
        return f"[MB{self.pk}-{self.mail_key}]"

    @property
    def is_unanswered(self) -> bool:
        """True if the newest message on the ticket is from the client."""
        try:
            newest = self.messages.latest("timestamp")
        except TicketMessage.DoesNotExist:
            return False
        return newest.type == TicketMessage.TYPE_CLIENT

    def assign_to(self, user) -> None:
        self.assignee = user
        self.assigned_at = timezone.now()
        self.save(update_fields=["assignee", "assigned_at", "updated_at"])


class TicketMessage(models.Model):
    """A single message within a ticket."""

    TYPE_CLIENT = "client"
    TYPE_STAFF = "staff"
    TYPE_CHOICES = (
        (TYPE_CLIENT, "Client"),
        (TYPE_STAFF, "Staff"),
    )

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    type = models.CharField(max_length=8, choices=TYPE_CHOICES)
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    content = models.TextField()
    mail_id = models.BigIntegerField(
        null=True,
        blank=True,
        unique=True,
        help_text="ID of the eve mail this message was created from, if any.",
    )

    class Meta:
        ordering = ["timestamp"]

    def __str__(self) -> str:
        return f"{self.ticket_id}/{self.pk} ({self.type})"


class DiscordWebhook(models.Model):
    """A Discord webhook notified about new tickets."""

    name = models.CharField(max_length=100)
    url = models.URLField(max_length=500)
    enabled = models.BooleanField(default=True)
    mention = models.CharField(max_length=128, null=True, blank=True)
    categories = models.ManyToManyField(
        TicketCategory,
        blank=True,
        related_name="webhooks",
        help_text="Notify for tickets created in these categories.",
    )
    all_categories = models.BooleanField(default=False, help_text="Notify for tickets in any category.")
    no_category = models.BooleanField(default=False, help_text="Notify for tickets without a category.")

    def __str__(self) -> str:
        return str(self.name)

    @classmethod
    def webhooks_for_category(cls, category) -> models.QuerySet:
        """Return enabled webhooks listening for the given category (or None)."""
        qs = cls.objects.filter(enabled=True)
        if category is None:
            return qs.filter(models.Q(all_categories=True) | models.Q(no_category=True))
        return qs.filter(models.Q(all_categories=True) | models.Q(categories=category)).distinct()


class BoardOwner(models.Model):
    """A character whose mailbox is polled for tickets and used to send replies."""

    character = models.OneToOneField(EveCharacter, on_delete=models.CASCADE, related_name="+")
    enabled = models.BooleanField(default=True)
    activated_at = models.DateTimeField(auto_now_add=True)
    last_seen_mail_id = models.BigIntegerField(
        null=True,
        blank=True,
        help_text="Highest eve mail ID already processed for this character.",
    )

    def __str__(self) -> str:
        return str(self.character.character_name)


class PendingEveMail(models.Model):
    """An outgoing eve mail waiting to be sent via a board owner character."""

    recipient = models.ForeignKey(EveCharacter, on_delete=models.CASCADE, related_name="+")
    subject = models.CharField(max_length=ESI_MAIL_MAX_SUBJECT)
    content = models.TextField(max_length=ESI_MAIL_MAX_BODY)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Mail to {self.recipient.character_name}: {self.subject[:50]}"


@receiver(pre_save, sender=BoardOwner)
def board_owner_pre_save(sender, instance: BoardOwner, **kwargs):
    if not instance.pk:
        return

    try:
        old = BoardOwner.objects.get(pk=instance.pk)
    except BoardOwner.DoesNotExist:
        return
    if not old.enabled and instance.enabled:
        instance.activated_at = timezone.now()
