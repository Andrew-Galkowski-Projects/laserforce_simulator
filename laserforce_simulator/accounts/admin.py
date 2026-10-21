from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from .models import Invite, InviteRedemption, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """UX-01 — email-keyed admin. Account removal is the built-in delete action."""

    ordering = ("email",)
    list_display = ("email", "is_staff", "is_superuser", "is_active", "date_joined")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


class InviteRedemptionInline(admin.TabularInline):
    """Who signed up through this invite, and when."""

    model = InviteRedemption
    extra = 0
    can_delete = False
    fields = ("user", "redeemed_at")
    readonly_fields = ("user", "redeemed_at")

    def has_add_permission(
        self, request: HttpRequest, obj: Invite | None = None
    ) -> bool:
        return False


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    """Create an Invite, then send its sign-up link: shown in a message right
    after it is added, and in the changelist while it is still redeemable."""

    list_display = ("__str__", "email", "uses", "status", "expires_at", "created_at")
    search_fields = ("email", "note", "code")
    readonly_fields = ("code", "use_count")
    inlines = [InviteRedemptionInline]

    @admin.display(description="Uses")
    def uses(self, invite: Invite) -> str:
        return f"{invite.use_count} / {invite.max_uses}"

    @admin.display(description="Status")
    def status(self, invite: Invite) -> str:
        if invite.is_expired:
            return "Expired"
        if not invite.uses_left:
            return "Used up"
        return "Active"

    @staticmethod
    def signup_url(request: HttpRequest, invite: Invite) -> str:
        return request.build_absolute_uri(f"{reverse('register')}?invite={invite.code}")

    def get_list_display(self, request: HttpRequest) -> Sequence[Any]:
        def signup_link(invite: Invite) -> str:
            if not invite.is_redeemable:
                return "-"
            return format_html(
                '<a href="{0}">{0}</a>', self.signup_url(request, invite)
            )

        signup_link.short_description = "Sign-up link"  # type: ignore[attr-defined]
        return (*self.list_display, signup_link)

    def get_fields(
        self, request: HttpRequest, obj: Invite | None = None
    ) -> Sequence[Any]:
        editable = ("email", "note", "max_uses", "expires_at")
        if obj is None:
            return editable
        return (*editable, "code", "use_count")

    def get_inlines(
        self, request: HttpRequest, obj: Invite | None
    ) -> list[type[admin.options.InlineModelAdmin]]:
        return list(self.inlines) if obj is not None else []

    def response_add(
        self, request: HttpRequest, obj: Invite, post_url_continue: str | None = None
    ) -> HttpResponse:
        self.message_user(
            request,
            format_html("Send this sign-up link: {}", self.signup_url(request, obj)),
            messages.SUCCESS,
        )
        return super().response_add(request, obj, post_url_continue)
