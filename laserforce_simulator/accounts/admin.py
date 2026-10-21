from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.html import format_html

from .models import Invite, User


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


@admin.register(Invite)
class InviteAdmin(admin.ModelAdmin):
    """Create an Invite, then send its sign-up link: shown in a message right
    after it is added, and in the changelist until it is used."""

    list_display = ("__str__", "email", "created_at", "used_by", "used_at")
    list_filter = (("used_at", admin.EmptyFieldListFilter),)
    search_fields = ("email", "note", "code")
    readonly_fields = ("code", "used_by", "used_at")

    @staticmethod
    def signup_url(request: HttpRequest, invite: Invite) -> str:
        return request.build_absolute_uri(f"{reverse('register')}?invite={invite.code}")

    def get_list_display(self, request: HttpRequest) -> Sequence[Any]:
        def signup_link(invite: Invite) -> str:
            if invite.is_used:
                return "-"
            return format_html(
                '<a href="{0}">{0}</a>', self.signup_url(request, invite)
            )

        signup_link.short_description = "Sign-up link"  # type: ignore[attr-defined]
        return (*self.list_display, signup_link)

    def get_fields(
        self, request: HttpRequest, obj: Invite | None = None
    ) -> Sequence[Any]:
        if obj is None:
            return ("email", "note")
        return ("email", "note", "code", "used_by", "used_at")

    def response_add(
        self, request: HttpRequest, obj: Invite, post_url_continue: str | None = None
    ) -> HttpResponse:
        self.message_user(
            request,
            format_html("Send this sign-up link: {}", self.signup_url(request, obj)),
            messages.SUCCESS,
        )
        return super().response_add(request, obj, post_url_continue)
