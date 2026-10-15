"""ANG-01a — django-filter allowlist for the ArenaMap list endpoint (ADR-0039)."""

from __future__ import annotations

from django_filters import rest_framework as filters

from core.models import ArenaMap


class ArenaMapFilterSet(filters.FilterSet):
    is_default = filters.BooleanFilter()
    # Filters the `has_confirmed_config` Exists annotation added by
    # `ArenaMapViewSet.get_queryset`.
    has_confirmed_config = filters.BooleanFilter(field_name="has_confirmed_config")

    class Meta:
        model = ArenaMap
        fields = ["is_default", "has_confirmed_config"]
