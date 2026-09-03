"""App settings."""

from django.conf import settings

# Number of days after which an assigned ticket is automatically released back
# to the unassigned pool. Set to 0 to disable the timeout.
MAILBOARD_TICKET_ASSIGNEE_TIMEOUT = getattr(settings, "MAILBOARD_TICKET_ASSIGNEE_TIMEOUT", 3)

# Maximum number of concurrently open tickets per user (web interface).
MAILBOARD_MAX_OPEN_TICKETS = getattr(settings, "MAILBOARD_MAX_OPEN_TICKETS", 10)

# Minimum number of seconds between two ticket creations by the same user
# (web interface rate limit).
MAILBOARD_TICKET_CREATE_COOLDOWN = getattr(settings, "MAILBOARD_TICKET_CREATE_COOLDOWN", 60)

# How many times sending of a single pending eve mail may fail with a
# permanent (4xx) error before it is dropped and admins are notified.
MAILBOARD_MAX_SEND_ATTEMPTS = getattr(settings, "MAILBOARD_MAX_SEND_ATTEMPTS", 10)

# ESI compatibility date used by the OpenAPI client.
MAILBOARD_ESI_COMPATIBILITY_DATE = "2026-08-18"
