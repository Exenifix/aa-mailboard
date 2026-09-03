"""ESI client provider (django-esi OpenAPI client)."""

from esi.openapi_clients import ESIClientProvider

from . import __version__
from .app_settings import MAILBOARD_ESI_COMPATIBILITY_DATE

# Scopes the board owner character must grant so the app can read the
# character's inbox and send mails on its behalf.
ESI_MAIL_SCOPES = [
    "esi-mail.read_mail.v1",
    "esi-mail.send_mail.v1",
]

esi = ESIClientProvider(
    compatibility_date=MAILBOARD_ESI_COMPATIBILITY_DATE,
    ua_appname="AllianceAuthMailboard",
    ua_version=__version__,
    ua_url="https://github.com/Exenifix/allianceauth-mailboard",
    tags=["Mail"],
)
