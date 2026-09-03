# Mail Board plugin for Alliance Auth

A ticket / support board for [Alliance Auth](https://gitlab.com/allianceauth/allianceauth)
that collects user requests from two places:

- **Eve mail** sent to a *board owner* character (collected via
  [django-esi](https://gitlab.com/allianceauth/django-esi), no member audit needed)
- The **web interface** (a simple new-ticket form)

Staff members view all tickets in a dashboard and respond to them. Responses
are delivered back to the ticket creator via eve mail, sent from a board owner
character. New tickets can additionally be announced via Discord webhooks.

## Features

- Tickets from eve mail and the web, in one dashboard
- Replies from staff are sent to the client via eve mail; clients can answer
  those mails to continue the conversation on the same ticket
- Ticket categories, per-category Discord webhook notifications
- Ticket claiming with automatic release after an inactivity timeout
- Max open tickets per user and a creation rate limit
- Mail reply correlation uses a per-ticket secret **and** a sender check, so
  ticket IDs cannot be abused to inject messages into other people's tickets

## Installation

1. Install the package into your Alliance Auth venv:

   ```bash
   pip install allianceauth-mailboard
   ```

2. Add `"mailboard"` to `INSTALLED_APPS` in your `local.py`.

3. Add the periodic tasks to your `local.py`:

   ```python
   CELERYBEAT_SCHEDULE["mailboard_collect_mails"] = {
       "task": "mailboard.tasks.collect_mails",
       "schedule": crontab(minute="*/5"),
   }
   CELERYBEAT_SCHEDULE["mailboard_send_pending_mails"] = {
       "task": "mailboard.tasks.send_pending_mails",
       "schedule": crontab(minute="*"),
   }
   CELERYBEAT_SCHEDULE["mailboard_check_assignee_timeouts"] = {
       "task": "mailboard.tasks.check_assignee_timeouts",
       "schedule": crontab(minute="0"),
   }
   ```

4. Run migrations and restart Auth:

   ```bash
   python manage.py migrate
   python manage.py collectstatic
   ```

5. In the admin panel, grant permissions (see below) and create ticket
   categories / Discord webhooks as needed.

6. A user with the `board_owner` permission opens
   *Mail Board → Add Board Owner* and authorizes a character with the
   `esi-mail.read_mail.v1` and `esi-mail.send_mail.v1` scopes. Mails sent to
   that character will be collected as tickets, and it is used to send replies.

## Permissions

Permission | Description
-- | --
`mailboard.board_owner` | Can add a mail receive character (board owner)
`mailboard.board_staff` | Can view tickets and respond to them
`mailboard.board_user` | Can create tickets via the web interface

The menu entry is visible to users with any of the three permissions.

## Settings

Name | Description | Default
-- | -- | --
`MAILBOARD_TICKET_ASSIGNEE_TIMEOUT` | Days after which an assigned ticket becomes unassigned again. `0` disables the timeout. | `3`
`MAILBOARD_MAX_OPEN_TICKETS` | Max concurrently open tickets per user (web interface) | `10`
`MAILBOARD_TICKET_CREATE_COOLDOWN` | Minimum seconds between ticket creations per user (web interface) | `60`
`MAILBOARD_MAX_SEND_ATTEMPTS` | Permanent send failures before an outgoing mail is dropped (admins are notified) | `10`
`MAILBOARD_ESI_COMPATIBILITY_DATE` | ESI compatibility date for the OpenAPI client | `"2026-08-18"`

## How mail correlation works

Every outgoing mail subject carries a tag like `[MB123-1a2b3c4d]` containing
the ticket ID and a random per-ticket secret. An incoming mail is only added
to an existing ticket when both the secret matches **and** the mail was sent
by the ticket's creator character; otherwise a new ticket is created. Replying
to a closed ticket reopens it.

Admins are notified (throttled) when mail collection fails for a board owner
or when no board owner is able to send pending mails.

## Development

Tests run against a local sqlite / fakeredis test harness:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .[test] fakeredis
DJANGO_SETTINGS_MODULE=testauth.settings_aa5.local .venv/bin/python runtests.py mailboard -v 2
```
