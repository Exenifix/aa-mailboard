"""Routes."""

from django.urls import path

from . import views

app_name = "mailboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("new-ticket", views.new_ticket, name="new_ticket"),
    path("dashboard", views.dashboard, name="dashboard"),
    path("add-board-owner", views.add_board_owner, name="add_board_owner"),
    path("api/tickets", views.api_tickets, name="api_tickets"),
    path(
        "api/tickets/<int:ticket_id>",
        views.api_ticket_detail,
        name="api_ticket_detail",
    ),
    path(
        "api/tickets/<int:ticket_id>/reply",
        views.api_ticket_reply,
        name="api_ticket_reply",
    ),
    path(
        "api/tickets/<int:ticket_id>/category",
        views.api_ticket_category,
        name="api_ticket_category",
    ),
    path(
        "api/tickets/<int:ticket_id>/lock",
        views.api_ticket_lock,
        name="api_ticket_lock",
    ),
    path(
        "api/tickets/<int:ticket_id>/unlock",
        views.api_ticket_unlock,
        name="api_ticket_unlock",
    ),
    path(
        "api/tickets/<int:ticket_id>/assign",
        views.api_ticket_assign,
        name="api_ticket_assign",
    ),
    path("api/contact", views.api_contact_character, name="api_contact_character"),
]
