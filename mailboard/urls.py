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
]
