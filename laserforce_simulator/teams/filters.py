"""ANG-01a — django-filter allowlists for the Team / Player list endpoints (ADR-0039).

Applied to the ``list`` action only (``ListOnlyFilterMixin``). Unknown query
params are ignored; a typed filter with an unparseable value is a 400.
"""

from __future__ import annotations

from django.db.models import QuerySet
from django_filters import rest_framework as filters

from teams.models import Player, Team


class TeamFilterSet(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")
    include_free_agents = filters.BooleanFilter(method="filter_include_free_agents")

    class Meta:
        model = Team
        fields = ["name", "include_free_agents"]

    def filter_include_free_agents(
        self, queryset: QuerySet[Team], name: str, value: bool
    ) -> QuerySet[Team]:
        # The exclusion itself lives in `filter_queryset` so it also applies
        # when the param is absent; this method only declares the param.
        return queryset

    def filter_queryset(self, queryset: QuerySet[Team]) -> QuerySet[Team]:
        """Exclude every free-agent pool Team unless ``?include_free_agents=true``.

        "Free-agent pool" is exactly ``Team.objects.regular()``'s complement —
        the global "Free Agents" Team plus every per-League pool — matching the
        HTML ``team_list``.
        """
        queryset = super().filter_queryset(queryset)
        if self.form.cleaned_data.get("include_free_agents") is not True:
            queryset = queryset.filter(pk__in=Team.objects.regular().values("pk"))
        return queryset


class PlayerFilterSet(filters.FilterSet):
    # A foreign / nonexistent team id yields an empty page, never a 404 — the
    # base queryset is already ownership-scoped, so nothing leaks.
    team = filters.NumberFilter(field_name="team")
    name = filters.CharFilter(lookup_expr="icontains")

    class Meta:
        model = Player
        fields = ["team", "name"]
