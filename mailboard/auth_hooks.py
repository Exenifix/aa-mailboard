from django.utils.translation import gettext_lazy as _

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls
from .models import Ticket
from .views import ANY_PERMISSION


class MailboardMenuItem(MenuItemHook):
    """This class ensures only authorized users will see the menu entry"""

    def __init__(self):
        # setup menu entry for sidebar
        MenuItemHook.__init__(
            self,
            _("Mail Board"),
            "fas fa-envelope-open-text fa-fw",
            "mailboard:index",
            navactive=["mailboard:"],
        )

    def render(self, request):
        app_count = Ticket.objects.filter(is_closed=False, assignee=None).count()
        self.count = app_count if app_count > 0 else None
        if any(request.user.has_perm(perm) for perm in ANY_PERMISSION):
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return MailboardMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "mailboard", r"^mailboard/")
