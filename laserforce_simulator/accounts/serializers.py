"""ANG-01g — the output-only ``me`` shape of the Accounts API."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from teams.serializers import PlayerSerializer

from .models import User
from .self_insert import get_self_insert


class MeSerializer(serializers.ModelSerializer):
    """``{id, email, date_joined, is_staff, self_insert}``.

    ``self_insert`` is the resolved link (``get_self_insert``) as full
    ``PlayerSerializer`` output, or ``None``; the raw column is never
    serialized, so a drifted link reads ``None``.
    """

    self_insert = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "date_joined", "is_staff", "self_insert"]
        read_only_fields = fields

    def get_self_insert(self, user: User) -> dict[str, Any] | None:
        player = get_self_insert(user)
        return PlayerSerializer(player).data if player is not None else None
