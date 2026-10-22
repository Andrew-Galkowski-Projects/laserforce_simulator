"""ANG-01a — django-filter allowlists for the Match / GameRound / GameEvent APIs (ADR-0039).

Match and GameRound filters apply to the ``list`` action only
(``ListOnlyFilterMixin``); ``GameEventFilterSet`` is bound by hand inside
``GameRoundViewSet.events``. Unknown query params are ignored; a typed filter
with an unparseable value is a 400.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.db.models import Exists, OuterRef, Q, QuerySet
from django_filters import rest_framework as filters

from matches.models import (
    GameEvent,
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
    Tournament,
)


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
    # ANG-06c — ``?single_round=true`` keeps only Single Rounds (no Match),
    # ``false`` only a Match's Rounds.
    single_round = filters.BooleanFilter(field_name="match", lookup_expr="isnull")

    class Meta:
        model = GameRound
        fields = ["match", "arena_map", "single_round"]


class GameEventFilterSet(filters.FilterSet):
    event_type = filters.ChoiceFilter(choices=GameEvent.EVENT_TYPES)
    actor = filters.NumberFilter(field_name="actor")
    target = filters.NumberFilter(field_name="target")
    # `?timestamp_min=` / `?timestamp_max=` in raw ticks, inclusive.
    timestamp = filters.RangeFilter()

    class Meta:
        model = GameEvent
        fields = ["event_type", "actor", "target", "timestamp"]


class LeagueFilterSet(filters.FilterSet):
    """ANG-01d-1 — ``?state=`` / ``?mode=`` (bad choice ⇒ 400)."""

    state = filters.ChoiceFilter(choices=League.STATE_CHOICES)
    mode = filters.ChoiceFilter(choices=League.MODE_CHOICES)

    class Meta:
        model = League
        fields = ["state", "mode"]


class SeasonFilterSet(filters.FilterSet):
    """ANG-01d-1 — ``?league=`` / ``?state=``. A foreign ``league`` id yields an
    empty page (the base queryset is already ownership-scoped)."""

    league = filters.NumberFilter(field_name="league")
    state = filters.ChoiceFilter(choices=Season.STATE_CHOICES)

    class Meta:
        model = Season
        fields = ["league", "state"]


_STRICT_BOOLEANS: dict[str, bool] = {
    "true": True,
    "1": True,
    "false": False,
    "0": False,
}


class StrictBooleanField(forms.Field):
    """``true`` / ``false`` / ``1`` / ``0`` (case-insensitive); anything else
    is an ``invalid`` error. Blank means "not filtered" (``None``)."""

    def to_python(self, value: Any) -> bool | None:
        if value in self.empty_values:
            return None
        parsed = _STRICT_BOOLEANS.get(str(value).strip().lower())
        if parsed is None:
            raise forms.ValidationError(
                "Enter true or false.", code="invalid", params={"value": value}
            )
        return parsed


class StrictBooleanFilter(filters.Filter):
    field_class = StrictBooleanField


class TournamentFilterSet(filters.FilterSet):
    """ANG-01f — ``?state=`` / ``?format=`` / ``?team_assembly=`` (bad choice ⇒
    400) and ``?embedded=true|false`` (in-League vs sandbox; also ``1`` / ``0``,
    case-insensitive; any other value ⇒ 400 ``invalid`` on ``embedded``)."""

    state = filters.ChoiceFilter(choices=Tournament.STATE_CHOICES)
    format = filters.ChoiceFilter(choices=Tournament.FORMAT_CHOICES)
    team_assembly = filters.ChoiceFilter(choices=Tournament.TEAM_ASSEMBLY_CHOICES)
    embedded = StrictBooleanFilter(method="filter_embedded")

    class Meta:
        model = Tournament
        fields = ["state", "format", "team_assembly", "embedded"]

    def filter_embedded(
        self, queryset: QuerySet[Tournament], name: str, value: bool
    ) -> QuerySet[Tournament]:
        """Embedded = referenced by a SeasonPhase or carrying a season_phase."""
        embedded_q = Q(season_phase__isnull=False) | Exists(
            SeasonPhase.objects.filter(tournament=OuterRef("pk"))
        )
        return queryset.filter(embedded_q) if value else queryset.exclude(embedded_q)
