"""Admin site."""

from django.contrib import admin

from .models import (
    BlacklistedCharacter,
    BoardOwner,
    DiscordWebhook,
    Ticket,
    TicketCategory,
    TicketMessage,
)


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled")
    list_filter = ("enabled",)
    search_fields = ("name",)


class TicketMessageInline(admin.TabularInline):
    model = TicketMessage
    extra = 0
    readonly_fields = ("timestamp",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "category",
        "creator_character",
        "assignee",
        "source",
        "is_closed",
        "has_new_messages",
        "created_at",
    )
    list_filter = ("is_closed", "has_new_messages", "source", "category")
    search_fields = ("title", "creator_character__character_name")
    readonly_fields = ("mail_key", "created_at", "updated_at")
    inlines = (TicketMessageInline,)


@admin.register(TicketMessage)
class TicketMessageAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "type", "staff", "timestamp", "mail_id")
    list_filter = ("type",)
    search_fields = ("ticket__title", "content")
    readonly_fields = ("timestamp",)


@admin.register(DiscordWebhook)
class DiscordWebhookAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "all_categories", "no_category")
    list_filter = ("enabled",)
    filter_horizontal = ("categories",)


@admin.register(BoardOwner)
class BoardOwnerAdmin(admin.ModelAdmin):
    list_display = ("character", "enabled", "activated_at", "last_seen_mail_id")
    list_filter = ("enabled",)


@admin.register(BlacklistedCharacter)
class BlacklistedCharacterAdmin(admin.ModelAdmin):
    list_display = ("character_id", "character_name", "active", "created_at", "unblocked_at")
    list_filter = ("active",)
    search_fields = ("character_id", "character_name")
    readonly_fields = ("created_at", "unblocked_at")


# PendingEveMail is intentionally not registered with the admin site.
