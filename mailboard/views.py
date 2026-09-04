"""Views."""

import json

from django.contrib import messages
from django.contrib.auth import get_user
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.services.hooks import get_extension_logger
from esi.decorators import token_required
from esi.models import Token

from .app_settings import (
    MAILBOARD_MAX_OPEN_TICKETS,
    MAILBOARD_TICKET_CREATE_COOLDOWN,
)
from .core import add_staff_message, create_staff_ticket, create_ticket, queue_closed_mail
from .models import (
    ESI_MAIL_MAX_BODY,
    BlacklistedCharacter,
    BoardOwner,
    Ticket,
    TicketCategory,
    TicketMessage,
)
from .providers import ESI_MAIL_SCOPES, esi

logger = get_extension_logger(__name__)

ANY_PERMISSION = (
    "mailboard.board_owner",
    "mailboard.board_staff",
    "mailboard.board_user",
)


@login_required
def index(request: HttpRequest):
    """Render the landing page."""
    if not any(request.user.has_perm(perm) for perm in ANY_PERMISSION):
        raise PermissionDenied
    if request.user.has_perm("mailboard.board_staff"):
        return redirect("mailboard:dashboard")
    if request.user.has_perm("mailboard.board_user"):
        return redirect("mailboard:new_ticket")
    context = {"page_title": "Mail Board"}
    return render(request, "mailboard/index.html", context)


@login_required
@permission_required("mailboard.board_user")
def new_ticket(request: HttpRequest):
    """Render the new ticket form and handle its submission."""
    categories = TicketCategory.objects.filter(enabled=True).order_by("name")
    if request.method == "POST":
        error = _create_ticket_from_form(request)
        if not error:
            messages.success(request, "Your ticket has been created.")
            return redirect("mailboard:new_ticket")
        messages.error(request, error)
    context = {
        "page_title": "New Ticket",
        "categories": categories,
        "max_content_length": ESI_MAIL_MAX_BODY,
    }
    return render(request, "mailboard/new_ticket.html", context)


def _create_ticket_from_form(request: HttpRequest):
    """Validate the new ticket form and create the ticket.

    Returns an error message, or None on success.
    """
    title = request.POST.get("title", "").strip()
    content = request.POST.get("content", "").strip()
    category_id = request.POST.get("category", "")

    if not title or not content:
        return "Please provide both a title and a description."
    if len(title) > 255:
        return "The title must be at most 255 characters long."
    if len(content) > ESI_MAIL_MAX_BODY:
        return f"The description must be at most {ESI_MAIL_MAX_BODY} characters long."

    category = None
    if category_id:
        category = TicketCategory.objects.filter(pk=category_id, enabled=True).first()
        if not category:
            return "The selected category does not exist."

    main_character = request.user.profile.main_character
    if not main_character:
        return "You need a main character to create tickets."

    user_characters = EveCharacter.objects.filter(character_ownership__user=request.user)
    open_count = Ticket.objects.filter(creator_character__in=user_characters, is_closed=False).count()
    if open_count >= MAILBOARD_MAX_OPEN_TICKETS:
        return f"You already have {open_count} open tickets. Please wait until " "some of them are closed."

    cooldown = MAILBOARD_TICKET_CREATE_COOLDOWN
    cache_key = f"mailboard_ticket_cooldown_{request.user.pk}"
    if cooldown and cache.get(cache_key):
        return "You are creating tickets too quickly. Please try again later."

    create_ticket(
        title=title,
        category=category,
        creator_character=main_character,
        source=Ticket.SOURCE_WEB,
        content=content,
    )
    if cooldown:
        cache.set(cache_key, True, cooldown)
    return None


@login_required
@permission_required("mailboard.board_staff")
def dashboard(request: HttpRequest):
    """Render the staff dashboard."""
    categories = TicketCategory.objects.order_by("name")
    context = {"page_title": "Dashboard", "categories": categories}
    return render(request, "mailboard/dashboard.html", context)


@login_required
@permission_required("mailboard.board_owner")
@token_required(scopes=ESI_MAIL_SCOPES, new=True)
def add_board_owner(request: HttpRequest, token: Token):
    """Register the character of a newly added token as a board owner."""
    try:
        character = EveCharacter.objects.get(character_id=token.character_id)
    except EveCharacter.DoesNotExist:
        character = EveCharacter.objects.create_character(token.character_id)
    CharacterOwnership.objects.get_or_create(
        character=character,
        defaults={"user": request.user, "owner_hash": token.character_owner_hash},
    )
    _, created = BoardOwner.objects.update_or_create(character=character, defaults={"enabled": True})
    if created:
        messages.success(
            request,
            f"{character.character_name} has been added as a board owner. "
            "Mails to this character will now be collected as tickets.",
        )
    else:
        messages.success(
            request,
            f"The board owner token for {character.character_name} has been updated.",
        )
    return redirect("mailboard:index")


# --- Dashboard API ---


def _ticket_to_dict(ticket: Ticket) -> dict:
    return {
        "id": ticket.pk,
        "title": ticket.title,
        "category": ticket.category.name if ticket.category else None,
        "category_id": ticket.category_id,
        "creator_character": {
            "id": ticket.creator_character.character_id,
            "name": ticket.creator_character.character_name,
        },
        "assignee": _user_display(ticket.assignee) if ticket.assignee else None,
        "assignee_id": ticket.assignee_id,
        "is_closed": ticket.is_closed,
        "has_new_messages": ticket.has_new_messages,
        "source": ticket.source,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


def _user_display(user) -> str:
    main = getattr(user.profile, "main_character", None)
    return main.character_name if main else user.username


@login_required
@permission_required("mailboard.board_staff", raise_exception=True)
@require_GET
def api_tickets(request: HttpRequest):
    """List tickets. By default only open, unassigned tickets and open assigned to me tickets are shown."""
    tickets = Ticket.objects.select_related("category", "creator_character", "assignee__profile__main_character")
    if request.GET.get("all") != "1":
        tickets = tickets.filter(Q(is_closed=False) & (Q(assignee=None) | Q(assignee=request.user)))
        # tickets with new client messages first (oldest first), then the
        # rest by oldest last update
        tickets = tickets.order_by("-has_new_messages", "updated_at")
    else:
        tickets = tickets.order_by("-updated_at")
    status = request.GET.get("status")
    if status == "open":
        tickets = tickets.filter(is_closed=False)
    elif status == "closed":
        tickets = tickets.filter(is_closed=True)
    category = request.GET.get("category")
    if category == "none":
        tickets = tickets.filter(category=None)
    elif category:
        tickets = tickets.filter(category_id=category)
    return JsonResponse({"tickets": [_ticket_to_dict(ticket) for ticket in tickets[:200]]})


@login_required
@permission_required("mailboard.board_staff", raise_exception=True)
@require_GET
def api_ticket_detail(request: HttpRequest, ticket_id: int):
    """Return a ticket with its full message history."""
    try:
        ticket = Ticket.objects.select_related(
            "category", "creator_character", "assignee__profile__main_character"
        ).get(pk=ticket_id)
    except Ticket.DoesNotExist:
        return JsonResponse({"error": "Ticket not found."}, status=404)
    data = _ticket_to_dict(ticket)
    data["messages"] = [
        {
            "type": message.type,
            "author": (
                _user_display(message.staff)
                if message.type == TicketMessage.TYPE_STAFF and message.staff
                else ticket.creator_character.character_name
            ),
            "character_id": (
                None if message.type == TicketMessage.TYPE_STAFF else ticket.creator_character.character_id
            ),
            "timestamp": message.timestamp.isoformat(),
            "content": message.content,
        }
        for message in ticket.messages.select_related("staff__profile__main_character")
    ]
    return JsonResponse(data)


@login_required
@permission_required("mailboard.board_staff", raise_exception=True)
@require_POST
def api_ticket_reply(request: HttpRequest, ticket_id: int):
    """Handle the Close / Send / Send and close dashboard actions."""
    try:
        ticket = Ticket.objects.select_related("creator_character").get(pk=ticket_id)
    except Ticket.DoesNotExist:
        return JsonResponse({"error": "Ticket not found."}, status=404)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)
    action = payload.get("action")
    content = (payload.get("content") or "").strip()

    if action not in ("send", "send_close", "close"):
        return JsonResponse({"error": "Unknown action."}, status=400)
    if action in ("send", "send_close"):
        if not content:
            return JsonResponse({"error": "The message cannot be empty."}, status=400)
        if len(content) > ESI_MAIL_MAX_BODY:
            return JsonResponse(
                {"error": "The message must be at most " f"{ESI_MAIL_MAX_BODY} characters long."},
                status=400,
            )

    if ticket.assignee and ticket.assignee != request.user:
        return JsonResponse(
            {"error": "This ticket is assigned to " f"{_user_display(ticket.assignee)}."},
            status=409,
        )
    if ticket.is_closed and action == "close":
        return JsonResponse({"error": "This ticket is already closed."}, status=400)

    if not ticket.assignee:
        ticket.assign_to(request.user)

    if action in ("send", "send_close"):
        add_staff_message(ticket, request.user, content)
    if action in ("send_close", "close"):
        ticket.is_closed = True
        ticket.has_new_messages = False
        ticket.save(update_fields=["is_closed", "has_new_messages", "updated_at"])
        queue_closed_mail(ticket)
        logger.info("Ticket #%d closed by %s", ticket.pk, request.user)

    return JsonResponse(_ticket_to_dict(ticket))


@login_required
@permission_required("mailboard.board_staff", raise_exception=True)
@require_POST
def api_ticket_category(request: HttpRequest, ticket_id: int):
    """Change the category of an open ticket."""
    try:
        ticket = Ticket.objects.select_related("creator_character").get(pk=ticket_id)
    except Ticket.DoesNotExist:
        return JsonResponse({"error": "Ticket not found."}, status=404)
    if ticket.is_closed:
        return JsonResponse({"error": "The category of a closed ticket cannot be changed."}, status=400)

    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    category = None
    category_id = payload.get("category_id")
    if category_id:
        category = TicketCategory.objects.filter(pk=category_id).first()
        if not category:
            return JsonResponse({"error": "The selected category does not exist."}, status=400)

    ticket.category = category
    ticket.save(update_fields=["category"])
    logger.info(
        "Ticket #%d category changed to %s by %s",
        ticket.pk,
        category or "Other",
        request.user,
    )
    return JsonResponse(_ticket_to_dict(ticket))


@login_required
@permission_required("mailboard.board_staff", raise_exception=True)
@require_POST
def api_contact_character(request: HttpRequest):
    """Create a staff outreach ticket to an arbitrary character.

    The first message is sent to the character via eve mail; afterwards the
    ticket behaves like any other.
    """
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    character_input = str(payload.get("character") or "").strip()
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()

    if not character_input:
        return JsonResponse({"error": "Please provide a character name or ID."}, status=400)
    if not title or not content:
        return JsonResponse({"error": "Please provide both a title and a message."}, status=400)
    if len(title) > 255:
        return JsonResponse({"error": "The title must be at most 255 characters long."}, status=400)
    if len(content) > ESI_MAIL_MAX_BODY:
        return JsonResponse(
            {"error": f"The message must be at most {ESI_MAIL_MAX_BODY} characters long."},
            status=400,
        )

    category = None
    category_id = payload.get("category_id")
    if category_id:
        category = TicketCategory.objects.filter(pk=category_id).first()
        if not category:
            return JsonResponse({"error": "The selected category does not exist."}, status=400)

    character, error = _resolve_character(character_input)
    if error:
        return JsonResponse({"error": error}, status=400)

    if BlacklistedCharacter.objects.filter(character__character_id=character.character_id, active=True).exists():
        return JsonResponse(
            {"error": f"{character.character_name} is blacklisted; their replies would be ignored."},
            status=400,
        )

    ticket = create_staff_ticket(
        title=title,
        category=category,
        target_character=character,
        user=request.user,
        content=content,
    )
    return JsonResponse(_ticket_to_dict(ticket))


def _resolve_character(character_input: str):
    """Resolve a character name or ID to an EveCharacter.

    Returns a (character, error) tuple with exactly one of them set.
    """
    character_id = None
    if character_input.isdigit():
        character_id = int(character_input)
    else:
        existing = EveCharacter.objects.filter(character_name__iexact=character_input).first()
        if existing:
            return existing, None
        try:
            result = esi.client.Universe.PostUniverseIds(body=[character_input]).result()
        except Exception:
            logger.exception("ESI name resolution failed for %r", character_input)
            return None, "Could not resolve the character name via ESI. Please try again later."
        characters = getattr(result, "characters", None)
        if characters is None and isinstance(result, dict):
            characters = result.get("characters")
        if not characters:
            return None, f'No character named "{character_input}" was found.'
        first = characters[0]
        character_id = first["id"] if isinstance(first, dict) else first.id

    try:
        return EveCharacter.objects.get(character_id=character_id), None
    except EveCharacter.DoesNotExist:
        pass
    try:
        return EveCharacter.objects.create_character(character_id), None
    except Exception:
        logger.exception("Failed to create EveCharacter for id %s", character_id)
        return None, f"No character with ID {character_id} was found."
