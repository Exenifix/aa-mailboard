"""Tasks."""

from __future__ import annotations

from datetime import datetime

import requests
from celery import shared_task
from django.conf import settings
from django.urls import reverse

from django.utils import timezone

from allianceauth.eveonline.models import EveCharacter
from allianceauth.services.hooks import get_extension_logger
from app_utils.allianceauth import notify_admins_throttled, notify_throttled
from esi.exceptions import (
    ESIErrorLimitException,
    HTTPClientError,
    HTTPNotModified,
)
from esi.models import Token

from .app_settings import (
    MAILBOARD_MAX_SEND_ATTEMPTS,
    MAILBOARD_TICKET_ASSIGNEE_TIMEOUT,
)
from .core import add_client_message, create_ticket
from .helpers import (
    DISCORD_EMBED_DESC_MAX,
    DISCORD_EMBED_TITLE_MAX,
    clean_mail_body,
    find_ticket_for_mail,
    strip_mail_tag,
    truncate,
)
from .models import (
    BlacklistedCharacter,
    BoardOwner,
    DiscordWebhook,
    PendingEveMail,
    Ticket,
    TicketMessage,
)
from .providers import ESI_MAIL_SCOPES, esi

logger = get_extension_logger(__name__)

# Max mails sent per run of send_pending_mails to stay within ESI rate limits.
MAX_MAILS_PER_RUN = 20


def _get_owner_token(owner: BoardOwner) -> Token | None:
    """Return a valid token with mail scopes for a board owner, or None."""
    return (
        Token.objects.filter(character_id=owner.character.character_id)
        .require_scopes(ESI_MAIL_SCOPES)
        .require_valid()
        .first()
    )


def _mail_header_sender_id(header) -> int | None:
    """Return the sender character id from an ESI mail header object.

    The ESI field is literally named ``from``, which is a Python keyword.
    Depending on the ESI client/codegen version in use, this can surface as
    ``header.from_`` (bravado-style munging) or only be reachable via
    ``getattr(header, "from")`` (pydantic models with no alias configured).
    We try both so this keeps working across client versions, instead of
    crashing the whole collection run on an AttributeError.
    """
    for attr_name in ("from_", "from"):
        try:
            value = getattr(header, attr_name)
        except AttributeError:
            continue
        if value is not None:
            return value
    logger.error(
        "Mail header %s has neither 'from_' nor 'from' attribute; " "available fields: %s",
        getattr(header, "mail_id", "<unknown>"),
        _model_field_names(header),
    )
    return None


def _model_field_names(obj) -> list[str]:
    """Best-effort list of field names on a pydantic/bravado model, for logging."""
    try:
        return list(obj.model_fields.keys())  # pydantic v2
    except AttributeError:
        pass
    try:
        return list(obj.__fields__.keys())  # pydantic v1
    except AttributeError:
        pass
    return [a for a in dir(obj) if not a.startswith("_")]


@shared_task
def collect_mails() -> None:
    """Collect new eve mails from all enabled board owners and turn them
    into tickets or ticket messages."""
    owners = BoardOwner.objects.filter(enabled=True).select_related("character")
    owner_count = owners.count()
    logger.info("Starting mail collection for %d board owner(s)", owner_count)

    failed: list[str] = []
    for owner in owners:
        logger.debug("Collecting mails for board owner %s", owner)
        try:
            _collect_mails_for_owner(owner)
        except ESIErrorLimitException as ex:
            logger.warning("ESI error limited, aborting mail collection: %s", ex)
            break
        except Exception as e:
            logger.exception("Failed to collect mails for board owner %s", owner, exc_info=e)
            failed.append(str(owner))

    if failed:
        logger.warning(
            "Mail collection finished with %d failure(s): %s",
            len(failed),
            ", ".join(failed),
        )
        notify_admins_throttled(
            message_id="mailboard_collect_mails_failed",
            message=(
                "Mail Board failed to collect eve mails for the following "
                f"board owner characters: {', '.join(failed)}. "
                "Check the extension log for details."
            ),
            title="Mail Board: mail collection failures",
            level="warning",
        )
    else:
        logger.info("Mail collection finished successfully for all board owners")


def _collect_mails_for_owner(owner: BoardOwner) -> None:
    """Fetch and process new mails from a single board owner's inbox."""
    token = _get_owner_token(owner)
    if not token:
        raise RuntimeError(f"No valid token with mail scopes for {owner.character.character_name}")
    character_id = owner.character.character_id
    headers = _fetch_new_mail_headers(character_id, token, owner.last_seen_mail_id, owner.activated_at)
    logger.debug(
        "Fetched %d new mail header(s) for %s (character_id=%d)",
        len(headers),
        owner,
        character_id,
    )
    if not headers:
        return

    owner_character_ids = set(BoardOwner.objects.values_list("character__character_id", flat=True))
    blacklist = {entry.character.character_id: entry for entry in BlacklistedCharacter.objects.all()}
    # process oldest first so conversations stay in order
    processed = 0
    for header in sorted(headers, key=lambda h: h.mail_id):
        try:
            _process_mail(owner, token, header, owner_character_ids, blacklist)
            processed += 1
        except Exception as e:
            logger.exception(
                "Failed to process mail %s for board owner %s",
                getattr(header, "mail_id", "<unknown>"),
                owner,
                exc_info=e
            )

    owner.last_seen_mail_id = max([h.mail_id for h in headers] + [owner.last_seen_mail_id or 0])
    owner.save(update_fields=["last_seen_mail_id"])
    logger.info(
        "Processed %d/%d new mail(s) for %s; last_seen_mail_id=%d",
        processed,
        len(headers),
        owner,
        owner.last_seen_mail_id,
    )


def _fetch_new_mail_headers(
    character_id: int, token: Token, last_seen_mail_id: int | None, owner_activated_at: datetime
) -> list:
    """Return all mail headers newer than last_seen_mail_id, paginating
    backwards through the inbox if needed."""
    headers = []
    last_mail_id = None
    page = 0
    while True:
        page += 1
        params = {"character_id": character_id, "token": token}
        if last_mail_id:
            params["last_mail_id"] = last_mail_id
        try:
            batch = esi.client.Mail.GetCharactersCharacterIdMail(**params).result()
        except HTTPNotModified:
            # ETag hit: inbox unchanged since last poll
            logger.debug("Mail inbox unchanged (304) for character_id=%d", character_id)
            break
        batch = [h for h in batch or [] if h.mail_id]
        new = [h for h in batch if h.mail_id > (last_seen_mail_id or 0) and h.timestamp > owner_activated_at]
        headers += new
        logger.debug(
            "character_id=%d page=%d fetched=%d new=%d",
            character_id,
            page,
            len(batch),
            len(new),
        )
        # first run: only look at the most recent page instead of the
        # whole inbox history
        if last_seen_mail_id is None or len(new) < len(batch) or len(batch) < 50:
            break
        last_mail_id = min(h.mail_id for h in batch)
    return headers


def _process_mail(
    owner: BoardOwner,
    token: Token,
    header,
    owner_character_ids: set[int],
    blacklist: dict[int, BlacklistedCharacter],
) -> None:
    """Turn a single incoming mail into a ticket message or a new ticket."""
    sender_id = _mail_header_sender_id(header)
    if not sender_id:
        logger.warning(
            "Skipping mail %s: could not determine sender id",
            getattr(header, "mail_id", "<unknown>"),
        )
        return
    blacklist_entry = blacklist.get(sender_id)
    if blacklist_entry and blacklist_entry.ignores_mail_at(header.timestamp):
        # Completely ignore blacklisted senders. The mail still advances
        # last_seen_mail_id, so it will never be picked up again - even if
        # the character is later removed from the blacklist, only mails
        # received after the unblock are processed.
        logger.info(
            "Ignoring mail %d from blacklisted character %d",
            header.mail_id,
            sender_id,
        )
        return
    if sender_id in owner_character_ids:
        logger.debug(
            "Skipping mail %d: sender %d is a board owner character",
            header.mail_id,
            sender_id,
        )
        return
    if TicketMessage.objects.filter(mail_id=header.mail_id).exists():
        logger.debug(
            "Skipping mail %d: already recorded as a ticket message",
            header.mail_id,
        )
        return

    logger.debug("Fetching full body for mail %d (sender=%d)", header.mail_id, sender_id)
    mail = esi.client.Mail.GetCharactersCharacterIdMailMailId(
        character_id=owner.character.character_id,
        mail_id=header.mail_id,
        token=token,
    ).result()
    content = clean_mail_body(mail.body or "")
    subject = header.subject or ""

    ticket = find_ticket_for_mail(subject, sender_id)
    if ticket:
        m = add_client_message(ticket, content, mail_id=header.mail_id)
        logger.info("Added mail %d to ticket #%d", header.mail_id, ticket.pk)
        if ticket.assignee:
            notify_throttled(
                f"ticket_new_message_{m.pk}",
                ticket.assignee,
                "New message in ticket",
                f"You've got a new message in your ticket **#{ticket.pk}**. Please check it on [dashboard]({settings.SITE_URL}{reverse('mailboard:dashboard')})!",
            )
        return

    try:
        sender = EveCharacter.objects.get(character_id=sender_id)
    except EveCharacter.DoesNotExist:
        logger.debug("Creating new EveCharacter record for character_id=%d", sender_id)
        sender = EveCharacter.objects.create_character(sender_id)
    ticket = create_ticket(
        title=strip_mail_tag(subject) or "(no subject)",
        creator_character=sender,
        source=Ticket.SOURCE_MAIL,
        content=content,
        mail_id=header.mail_id,
    )
    logger.info(
        "Created ticket #%d from mail %d (sender=%d)",
        ticket.pk,
        header.mail_id,
        sender_id,
    )


@shared_task
def send_pending_mails() -> None:
    """Send queued eve mails via the first available board owner."""
    pending = list(PendingEveMail.objects.select_related("recipient")[:MAX_MAILS_PER_RUN])
    if not pending:
        logger.debug("No pending mails to send")
        return
    logger.info("Attempting to send %d pending mail(s)", len(pending))

    available: list[tuple[BoardOwner, Token]] = []
    failed_owners: list[str] = []
    for owner in BoardOwner.objects.filter(enabled=True).select_related("character"):
        token = _get_owner_token(owner)
        if token:
            available.append((owner, token))
        else:
            logger.warning("No valid mail token for board owner %s", owner)
            failed_owners.append(str(owner))

    if not available:
        logger.error(
            "No available board owners to send mail; configured but failed: %s",
            ", ".join(failed_owners) if failed_owners else "none configured",
        )

    sent_count = 0
    for mail in pending:
        if not available:
            break
        sent = False
        while available and not sent:
            owner, token = available[0]
            try:
                esi.client.Mail.PostCharactersCharacterIdMail(
                    character_id=owner.character.character_id,
                    token=token,
                    body={
                        "approved_cost": 0,
                        "body": mail.content,
                        "recipients": [
                            {
                                "recipient_id": mail.recipient.character_id,
                                "recipient_type": "character",
                            }
                        ],
                        "subject": mail.subject,
                    },
                ).result(use_etag=False, use_cache=False, store_cache=False)
                sent = True
                logger.info(
                    "Sent mail %d to %s via %s",
                    mail.pk,
                    mail.recipient.character_name,
                    owner,
                )
            except ESIErrorLimitException as ex:
                logger.warning("ESI error limited, aborting mail sending: %s", ex)
                return
            except HTTPClientError as ex:
                if ex.status_code in (401, 403):
                    logger.exception("Board owner %s cannot send mails", owner)
                    failed_owners.append(str(owner))
                    available.pop(0)
                else:
                    # problem with this particular mail (e.g. CSPA charge,
                    # invalid recipient) - skip it and retry later
                    logger.exception("Failed to send pending mail %d via %s", mail.pk, owner)
                    _record_mail_failure(mail)
                    break
            except Exception as e:
                logger.exception("Board owner %s failed to send mail", owner, exc_info=e)
                failed_owners.append(str(owner))
                available.pop(0)
        if sent:
            mail.delete()
            sent_count += 1

    logger.info("Finished sending pending mails: %d/%d sent", sent_count, len(pending))

    if not available:
        notify_admins_throttled(
            message_id="mailboard_no_board_owners",
            message=(
                "Mail Board could not send pending eve mails: no available "
                "board owner characters "
                f"({', '.join(failed_owners) if failed_owners else 'none configured'}). "
                "Please add or fix a board owner."
            ),
            title="Mail Board: no available board owners",
            level="danger",
        )


def _record_mail_failure(mail: PendingEveMail) -> None:
    """Count a permanent send failure and drop the mail after too many."""
    mail.attempts += 1
    logger.warning("Recorded send failure #%d for pending mail %d", mail.attempts, mail.pk)
    if mail.attempts >= MAILBOARD_MAX_SEND_ATTEMPTS:
        logger.error(
            "Dropping pending mail %d after %d failed attempts",
            mail.pk,
            mail.attempts,
        )
        notify_admins_throttled(
            message_id=f"mailboard_mail_dropped_{mail.pk}",
            message=(
                f"Mail Board dropped an outgoing eve mail to "
                f"{mail.recipient.character_name} (subject: {mail.subject[:100]}) "
                f"after {mail.attempts} failed attempts."
            ),
            title="Mail Board: outgoing mail dropped",
            level="warning",
        )
        mail.delete()
    else:
        mail.save(update_fields=["attempts"])


@shared_task
def check_assignee_timeouts() -> None:
    """Release tickets whose assignee has not acted within the timeout."""
    days = MAILBOARD_TICKET_ASSIGNEE_TIMEOUT
    if not days:
        logger.debug("Assignee timeout disabled, skipping check")
        return
    deadline = timezone.now() - timezone.timedelta(days=days)
    count = Ticket.objects.filter(is_closed=False, assignee__isnull=False, assigned_at__lt=deadline).update(
        assignee=None, assigned_at=None
    )
    if count:
        logger.info("Released %d ticket(s) from inactive assignees", count)
    else:
        logger.debug("No tickets to release from inactive assignees")


@shared_task
def notify_ticket_webhooks(ticket_id: int) -> None:
    """Notify all Discord webhooks listening for the ticket's category."""
    try:
        ticket = Ticket.objects.select_related("category", "creator_character").get(pk=ticket_id)
    except Ticket.DoesNotExist:
        logger.warning("notify_ticket_webhooks: ticket #%d does not exist", ticket_id)
        return
    webhooks = DiscordWebhook.webhooks_for_category(ticket.category)
    if not webhooks:
        logger.debug(
            "No Discord webhooks configured for ticket #%d category %s",
            ticket.pk,
            ticket.category,
        )
        return

    first_message = ticket.messages.filter(type=TicketMessage.TYPE_CLIENT).first()
    payload = {
        "username": "Mail Board",
        "embeds": [
            {
                "title": truncate(
                    f"New ticket #{ticket.pk}: {ticket.title}",
                    DISCORD_EMBED_TITLE_MAX,
                ),
                "description": truncate(
                    first_message.content if first_message else "",
                    DISCORD_EMBED_DESC_MAX,
                ),
                "color": 0x3498DB,
                "fields": [
                    {
                        "name": "Category",
                        "value": ticket.category.name if ticket.category else "Other",
                        "inline": True,
                    },
                    {
                        "name": "Creator",
                        "value": ticket.creator_character.character_name,
                        "inline": True,
                    },
                    {
                        "name": "Source",
                        "value": ticket.get_source_display(),
                        "inline": True,
                    },
                ],
            }
        ],
    }
    notified = 0
    for webhook in webhooks:
        try:
            p = payload.copy()
            if webhook.mention:
                p["content"] = webhook.mention
            response = requests.post(webhook.url, json=p, timeout=15)
            response.raise_for_status()
            notified += 1
        except requests.RequestException:
            logger.exception(
                "Failed to notify webhook %s about ticket #%d",
                webhook.name,
                ticket.pk,
            )
    logger.info(
        "Notified %d/%d webhook(s) for ticket #%d",
        notified,
        len(webhooks),
        ticket.pk,
    )
