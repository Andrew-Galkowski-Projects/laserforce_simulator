"""ANG-01g — the output-only ``me`` shape of the Accounts API."""

from __future__ import annotations

from typing import Any

from django.conf import settings
from rest_framework import serializers

from desktop_app.notice import current_notice
from teams.serializers import PlayerSerializer

from .models import User
from .self_insert import get_self_insert


class MeSerializer(serializers.ModelSerializer):
    """``{id, email, date_joined, is_staff, self_insert, desktop, desktop_notice}``.

    ``self_insert`` is the resolved link (``get_self_insert``) as full
    ``PlayerSerializer`` output, or ``None``; the raw column is never
    serialized, so a drifted link reads ``None``.

    DESK-01: ``desktop`` is ``settings.DESKTOP_APP`` (the SPA hides the Account
    screens when it is set) and ``desktop_notice`` the launcher's reset notice,
    always ``None`` on the Hosted deploy.
    """

    self_insert = serializers.SerializerMethodField()
    desktop = serializers.SerializerMethodField()
    desktop_notice = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "date_joined",
            "is_staff",
            "self_insert",
            "desktop",
            "desktop_notice",
        ]
        read_only_fields = fields

    def get_self_insert(self, user: User) -> dict[str, Any] | None:
        player = get_self_insert(user)
        return PlayerSerializer(player).data if player is not None else None

    def get_desktop(self, user: User) -> bool:
        return bool(settings.DESKTOP_APP)

    def get_desktop_notice(self, user: User) -> str | None:
        return current_notice() if settings.DESKTOP_APP else None
