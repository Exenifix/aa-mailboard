"""Shared test helpers."""

from allianceauth.authentication.models import CharacterOwnership
from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from ..models import BoardOwner, Ticket, TicketMessage


def create_character(character_id=1001, name="Bruce Wayne") -> EveCharacter:
    return EveCharacter.objects.create(
        character_id=character_id,
        character_name=name,
        corporation_id=2001,
        corporation_name="Wayne Technologies",
        corporation_ticker="WYT",
    )


def create_user(character_id=1001, name="Bruce Wayne", permissions=None):
    """Create a user with a main character and the given mailboard permissions."""
    user = AuthUtils.create_user(name.replace(" ", "_") + str(character_id))
    AuthUtils.add_main_character_2(
        user,
        name=name,
        character_id=character_id,
        corp_id=2001,
        corp_name="Wayne Technologies",
        corp_ticker="WYT",
    )
    CharacterOwnership.objects.get_or_create(
        character=user.profile.main_character,
        defaults={"user": user, "owner_hash": f"hash{character_id}"},
    )
    for permission in permissions or []:
        user = AuthUtils.add_permission_to_user_by_name(permission, user)
    return user


def create_ticket_direct(creator_character, **kwargs) -> Ticket:
    """Create a bare ticket without side effects."""
    params = {
        "title": "Test ticket",
        "creator_character": creator_character,
        "source": Ticket.SOURCE_WEB,
    }
    params.update(kwargs)
    ticket = Ticket.objects.create(**params)
    TicketMessage.objects.create(ticket=ticket, type=TicketMessage.TYPE_CLIENT, content="Please help")
    return ticket


def create_board_owner(character_id=9001, name="Board Owner", **kwargs) -> BoardOwner:
    character = create_character(character_id=character_id, name=name)
    return BoardOwner.objects.create(character=character, **kwargs)
