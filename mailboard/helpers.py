"""Helper functions for correlating and composing eve mails."""

import html
import re
import secrets

from django.utils.html import strip_tags

from .models import (
    ESI_MAIL_MAX_BODY,
    ESI_MAIL_MAX_SUBJECT,
    Ticket, TicketCategory, TicketMessage,
)

# Matches the ticket tag embedded in mail subjects, e.g. "[MB123-a1b2c3d4]"
MAIL_TAG_RE = re.compile(r"\[MB(?P<ticket_id>\d{1,18})-(?P<key>[0-9a-f]{4,32})\]")

# Discord limits
DISCORD_EMBED_TITLE_MAX = 256
DISCORD_EMBED_DESC_MAX = 4096


def truncate(text: str, limit: int, ellipsis_: str = "…") -> str:
    """Truncate text to the given length, appending an ellipsis if shortened."""
    if len(text) <= limit:
        return text
    return text[: limit - len(ellipsis_)] + ellipsis_


def clean_mail_body(body: str) -> str:
    """Convert an eve mail HTML body into plain text."""
    body = body.split("-------", maxsplit=1)[0]
    text = re.sub(r"<br\s*/?>", "\n", body or "", flags=re.IGNORECASE)
    return html.unescape(strip_tags(text)).strip()


def strip_mail_tag(subject: str) -> str:
    """Remove any ticket tag from a subject line."""
    return MAIL_TAG_RE.sub("", subject or "").strip()


def find_ticket_for_mail(subject: str, sender_character_id: int) -> Ticket | None:
    """Find the ticket a client mail belongs to, or None.

    A mail is only matched to a ticket when the subject tag carries the
    ticket's secret key AND the mail was sent by the ticket's creator
    character. This prevents third parties from injecting messages into other
    people's tickets by guessing ticket IDs (IDOR).
    """
    match = MAIL_TAG_RE.search(subject or "")
    if not match:
        return None
    ticket = Ticket.objects.select_related("creator_character").filter(pk=int(match.group("ticket_id"))).first()
    if not ticket:
        return None
    if not secrets.compare_digest(ticket.mail_key, match.group("key")):
        return None
    if ticket.creator_character.character_id != sender_character_id:
        return None
    return ticket


def build_reply_subject(ticket) -> str:
    """Subject for outgoing mails about a ticket, always carrying the tag."""
    tag = ticket.mail_tag
    prefix = f"Re: {tag} "
    title = truncate(ticket.title, ESI_MAIL_MAX_SUBJECT - len(prefix))
    return f"{prefix}{title}"


REPLY_FOOTER = (
    "\n\n--\n"
    "You can reply to this mail to add a message to your ticket. "
    "Keep the ticket reference {tag} in the subject line."
)


def match_category_for_title(title: str) -> TicketCategory | None:
    """Pick the enabled category whose keywords best match a ticket title.

    Keywords are compared case-insensitively as substrings of the title; the
    category with the most matching keywords wins (ties broken by name).
    Returns None when no keyword matches.
    """
    title_lower = (title or "").lower()
    best = None
    best_hits = 0
    for category in TicketCategory.objects.filter(enabled=True).exclude(keywords="").order_by("name"):
        keywords = [keyword.strip().lower() for keyword in category.keywords.split(",") if keyword.strip()]
        hits = sum(1 for keyword in keywords if keyword in title_lower)
        if hits > best_hits:
            best = category
            best_hits = hits
    return best


def build_mail_body(ticket: Ticket) -> str:
    """Build an outgoing mail body within the ESI 10,000 character limit.

    Staff notes are internal and never included.
    """
    content = ""
    messages = ticket.messages.exclude(type=TicketMessage.TYPE_NOTE).order_by("-timestamp")
    for message in messages:
        content += f"From: <b>{'SUPPORT' if message.type == TicketMessage.TYPE_STAFF else 'YOU'}</b>\n\n"
        content += message.content + "\n\n" + "-" * 20 + "\n"
        if len(content) > ESI_MAIL_MAX_BODY:
            break

    return truncate(content.strip(), ESI_MAIL_MAX_BODY)
