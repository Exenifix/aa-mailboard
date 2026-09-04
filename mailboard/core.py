"""Core ticket operations shared by views and tasks."""

from django.db import transaction
from django.utils import timezone

from allianceauth.services.hooks import get_extension_logger

from .helpers import build_mail_body, build_reply_subject, truncate
from .models import ESI_MAIL_MAX_BODY, PendingEveMail, Ticket, TicketMessage

logger = get_extension_logger(__name__)

CONFIRMATION_BODY = (
    'Your ticket "{title}" has been received and assigned the reference '
    "{tag}.\n\n"
    "Our staff will get back to you via eve mail. You can add more "
    "information at any time by replying to this mail and keeping the "
    "ticket reference {tag} in the subject line."
)

CLOSE_BODY = (
    'Your ticket "{title}" has been closed.\n\n'
    "If your issue has not been resolved, please feel free to reopen the ticket via replying to this mail, "
    "open a new one, or contact us on Discord."
)


def create_ticket(*, title, creator_character, source, content, category=None, mail_id=None) -> Ticket:
    """Create a new ticket with its first client message and queue
    webhook notifications."""
    with transaction.atomic():
        ticket = Ticket.objects.create(
            title=truncate(title, 255),
            category=category,
            creator_character=creator_character,
            source=source,
            has_new_messages=True,
        )
        TicketMessage.objects.create(
            ticket=ticket,
            type=TicketMessage.TYPE_CLIENT,
            content=content,
            mail_id=mail_id,
        )
        if source == Ticket.SOURCE_MAIL:
            queue_confirmation_mail(ticket)

    from .tasks import notify_ticket_webhooks

    transaction.on_commit(lambda: notify_ticket_webhooks.delay(ticket.pk))
    logger.info(
        "Created ticket #%d (%s) for %s",
        ticket.pk,
        source,
        creator_character.character_name,
    )
    return ticket


def add_client_message(ticket: Ticket, content: str, mail_id=None) -> TicketMessage:
    """Append a client message to a ticket, reopening it if necessary."""
    message = TicketMessage.objects.create(
        ticket=ticket,
        type=TicketMessage.TYPE_CLIENT,
        content=content,
        mail_id=mail_id,
    )
    ticket.has_new_messages = True
    if ticket.is_closed:
        ticket.is_closed = False
        ticket.save(update_fields=["is_closed", "has_new_messages", "updated_at"])
        logger.info("Reopened ticket #%d after client reply", ticket.pk)
    else:
        ticket.save(update_fields=["has_new_messages", "updated_at"])
    return message


def add_staff_message(ticket: Ticket, user, content: str) -> TicketMessage:
    """Append a staff message to a ticket and queue the eve mail reply."""
    message = TicketMessage.objects.create(
        ticket=ticket,
        type=TicketMessage.TYPE_STAFF,
        staff=user,
        content=content,
    )
    ticket.has_new_messages = False
    ticket.save(update_fields=["has_new_messages", "updated_at"])
    PendingEveMail.objects.create(
        recipient=ticket.creator_character,
        subject=build_reply_subject(ticket),
        content=build_mail_body(ticket),
    )
    return message


def queue_confirmation_mail(ticket: Ticket) -> PendingEveMail:
    """Queue the confirmation mail sent when a ticket is created from a mail."""
    body = CONFIRMATION_BODY.format(title=truncate(ticket.title, 200), tag=ticket.mail_tag)
    return PendingEveMail.objects.create(
        recipient=ticket.creator_character,
        subject=build_reply_subject(ticket),
        content=truncate(body, ESI_MAIL_MAX_BODY),
    )


def create_staff_ticket(*, title, target_character, user, content, category=None) -> Ticket:
    """Create a ticket by staff reaching out to an arbitrary character.

    The ticket is assigned to the initiating staff member and the message is
    queued as an eve mail to the target character. From then on the ticket
    behaves like any other: the character can reply via mail using the
    subject tag.
    """
    with transaction.atomic():
        ticket = Ticket.objects.create(
            title=truncate(title, 255),
            category=category,
            creator_character=target_character,
            source=Ticket.SOURCE_STAFF,
            assignee=user,
            assigned_at=timezone.now(),
        )
        add_staff_message(ticket, user, content)
    logger.info(
        "Created staff outreach ticket #%d to %s by %s",
        ticket.pk,
        target_character.character_name,
        user,
    )
    return ticket


def queue_closed_mail(ticket: Ticket) -> PendingEveMail:
    body = CLOSE_BODY.format(title=truncate(ticket.title, 200))
    return PendingEveMail.objects.create(
        recipient=ticket.creator_character,
        subject=build_reply_subject(ticket),
        content=truncate(body, ESI_MAIL_MAX_BODY),
    )
