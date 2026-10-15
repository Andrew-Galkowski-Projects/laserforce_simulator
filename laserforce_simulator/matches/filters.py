"""ANG-01a — django-filter allowlists for the Match / GameRound / GameEvent APIs (ADR-0039).

Match and GameRound filters apply to the ``list`` action only
(``ListOnlyFilterMixin``); ``GameEventFilterSet`` is bound by hand inside
``GameRoundViewSet.events``. Unknown query params are ignored; a typed filter
with an unparseable value is a 400.
"""

from __future__ import annotations

from django.db.models import Q, QuerySet
from django_filters import rest_framework as filters

from matches.models import GameEvent, GameRound, Match


class MatchFilterSet(filters.FilterSet):
    team = filters.NumberFilter(method="filter_team")
    season = filters.NumberFilter(field_name="season")
    match_type = filters.ChoiceFilter(choices=Match.MATCH_TYPES)
    # `?date_played_after=` / `?date_played_before=` (YYYY-MM-DD, inclusive).
    date_played = filters.DateFromToRangeFilter()

    class Meta:
        model = Match
        fields = ["team", "season", "match_type", "date_played"]

    def filter_team(
        self, queryset: QuerySet[Match], name: str, value: float
    ) -> QuerySet[Match]:
        """A Match the Team played on either side."""
        return queryset.filter(Q(team_red_id=value) | Q(team_blue_id=value))


class GameRoundFilterSet(filters.FilterSet):
    match = filters.NumberFilter(field_name="match")
    arena_map = filters.NumberFilter(field_name="arena_map")

    class Meta:
        model = GameRound
        fields = ["match", "arena_map"]


class GameEventFilterSet(filters.FilterSet):
    event_type = filters.ChoiceFilter(choices=GameEvent.EVENT_TYPES)
    actor = filters.NumberFilter(field_name="actor")
    target = filters.NumberFilter(field_name="target")
    # `?timestamp_min=` / `?timestamp_max=` in raw ticks, inclusive.
    timestamp = filters.RangeFilter()

    class Meta:
        model = GameEvent
        fields = ["event_type", "actor", "target", "timestamp"]
