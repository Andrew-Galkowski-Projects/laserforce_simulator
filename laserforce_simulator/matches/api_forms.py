"""ANG-01b — strict query-param Forms for the read-only Match / Round API actions.

Each is bound as ``Form(data=plain_form_data(Form, request.query_params))`` then
``validate_form_or_raise(form)``. They take **ints**; the views resolve rows via
``get_owned_or_404`` *after* validation, so the same id twice is a 400 even when
the id is unknown. Defaults are applied in ``clean_<field>`` so ``cleaned_data``
is always complete. Unlike the lenient HTML parsing, bad values are 400s
(ADR-0039 §3).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django import forms

from matches.league_services import STANDINGS_SORT_KEYS
from matches.models import League
from matches.season_player_stats import DERIVED_KEYS, STAT_KEYS

_PROVENANCE_CHOICES = [("all", "all"), ("real", "real"), ("sim", "sim")]
_ROLE_CHOICES = [
    ("commander", "commander"),
    ("heavy", "heavy"),
    ("scout", "scout"),
    ("medic", "medic"),
    ("ammo", "ammo"),
]


def _add_date_range(form_cls: type[forms.Form]) -> type[forms.Form]:
    """Add the optional ``from`` / ``to`` ``YYYY-MM-DD`` fields.

    ``from`` is a Python keyword, so the fields cannot be declared in the class
    body; they are injected into ``base_fields`` after the class is defined.
    """
    for name in ("from", "to"):
        form_cls.base_fields[name] = forms.DateField(
            required=False, input_formats=["%Y-%m-%d"]
        )
    return form_cls


class CompareRoundsParamsForm(forms.Form):
    """RV-01 — ``?round_a=&round_b=``."""

    round_a = forms.IntegerField(min_value=1)
    round_b = forms.IntegerField(min_value=1)

    def clean(self) -> dict:
        cleaned = super().clean()
        a = cleaned.get("round_a")
        b = cleaned.get("round_b")
        if a is not None and b is not None and a == b:
            raise forms.ValidationError(
                "Pick two different rounds to compare.", code="invalid"
            )
        return cleaned


class TeamH2HParamsForm(forms.Form):
    """HX-03 — ``?team_a=&team_b=&provenance=&from=&to=``."""

    team_a = forms.IntegerField(min_value=1)
    team_b = forms.IntegerField(min_value=1)
    provenance = forms.ChoiceField(choices=_PROVENANCE_CHOICES, required=False)

    def clean_provenance(self) -> str:
        return self.cleaned_data.get("provenance") or "all"

    def clean(self) -> dict:
        cleaned = super().clean()
        a = cleaned.get("team_a")
        b = cleaned.get("team_b")
        if a is not None and b is not None and a == b:
            raise forms.ValidationError(
                "Pick two different teams to compare.", code="invalid"
            )
        return cleaned


class PlayerH2HParamsForm(forms.Form):
    """HX-04 — ``?player_a=&player_b=&role=&provenance=&from=&to=``."""

    player_a = forms.IntegerField(min_value=1)
    player_b = forms.IntegerField(min_value=1)
    role = forms.ChoiceField(choices=_ROLE_CHOICES, required=False)
    provenance = forms.ChoiceField(choices=_PROVENANCE_CHOICES, required=False)

    def clean_role(self) -> str | None:
        return self.cleaned_data.get("role") or None

    def clean_provenance(self) -> str:
        return self.cleaned_data.get("provenance") or "all"

    def clean(self) -> dict:
        cleaned = super().clean()
        a = cleaned.get("player_a")
        b = cleaned.get("player_b")
        if a is not None and b is not None and a == b:
            raise forms.ValidationError(
                "Pick two different players to compare.", code="invalid"
            )
        return cleaned


_add_date_range(TeamH2HParamsForm)
_add_date_range(PlayerH2HParamsForm)


class StandingsParamsForm(forms.Form):
    """ANG-01d-1 — ``?sort=&dir=`` for the Season standings endpoint.

    Defaults ``rank`` / ``asc`` are applied in ``clean_<field>``; a bad value is
    a 400 ``invalid_choice`` (unlike the forgiving HTML coercion).
    """

    sort = forms.ChoiceField(
        choices=[(k, k) for k in sorted(STANDINGS_SORT_KEYS)], required=False
    )
    dir = forms.ChoiceField(choices=[("asc", "asc"), ("desc", "desc")], required=False)

    def clean_sort(self) -> str:
        return self.cleaned_data.get("sort") or "rank"

    def clean_dir(self) -> str:
        return self.cleaned_data.get("dir") or "asc"


# ---------------------------------------------------------------------------
# ANG-01e -- League screen params (``season`` / ``rate`` / ``ordering``)
# ---------------------------------------------------------------------------

RATE_CHOICES: list[tuple[str, str]] = [
    ("total", "total"),
    ("per_game", "per_game"),
    ("per_10", "per_10"),
]

# The Player-Stats / Watch-List sort keys the HTML screens allow.
PLAYER_STAT_SORT_KEYS: tuple[str, ...] = (
    *STAT_KEYS,
    *DERIVED_KEYS,
    "name",
    "team",
    "games",
)


def ordering_choices(keys: Iterable[str]) -> list[tuple[str, str]]:
    """``<key>`` and ``-<key>`` choices for an ``ordering`` ChoiceField."""
    out: list[tuple[str, str]] = []
    for key in keys:
        out.append((key, key))
        out.append((f"-{key}", f"-{key}"))
    return out


def parse_ordering(ordering: str) -> tuple[str, str]:
    """Split a validated ``ordering`` value into ``(sort_key, "asc" | "desc")``."""
    if ordering.startswith("-"):
        return ordering[1:], "desc"
    return ordering, "asc"


class LeagueScreenParamsForm(forms.Form):
    """Base for the League screen param Forms; bind with ``league=``.

    Subclasses declare the fields they take, from ``season``, ``rate`` and
    ``ordering`` (plus their own); the ``clean_<field>`` hooks here apply the
    shared rules and defaults, so ``cleaned_data`` is always complete:

    - ``season``: blank -> ``None`` (the displayed Season); a Season id of this
      League -> that id as a string; ``"career"`` when ``allow_career``.
      Anything else is a 400 ``invalid_choice``. The value feeds
      ``league_screen_services.resolve_season_scope`` unchanged.
    - ``rate``: blank -> ``"total"``.
    - ``ordering``: blank -> ``ordering_default``; split it with
      :func:`parse_ordering`.
    """

    allow_career: bool = True
    ordering_default: str = ""

    def __init__(self, *args: Any, league: League, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.league = league

    def clean_season(self) -> str | None:
        raw = (self.cleaned_data.get("season") or "").strip()
        if not raw:
            return None
        if raw == "career" and self.allow_career:
            return raw
        try:
            season_id = int(raw)
        except ValueError:
            season_id = None
        if season_id is None or not self.league.seasons.filter(pk=season_id).exists():
            raise forms.ValidationError(
                "Select a valid choice. %(value)s is not one of the available choices.",
                code="invalid_choice",
                params={"value": raw},
            )
        return str(season_id)

    def clean_rate(self) -> str:
        return self.cleaned_data.get("rate") or "total"

    def clean_ordering(self) -> str:
        return self.cleaned_data.get("ordering") or self.ordering_default


class WatchListParamsForm(LeagueScreenParamsForm):
    """ANG-01e -- ``?season=&rate=&ordering=`` for ``GET watch-list/``."""

    ordering_default = "-points_scored"

    season = forms.CharField(required=False)
    rate = forms.ChoiceField(choices=RATE_CHOICES, required=False)
    ordering = forms.ChoiceField(
        choices=ordering_choices(PLAYER_STAT_SORT_KEYS), required=False
    )


# --- feats + playoffs (T6) ---------------------------------------------------


class StatisticalFeatsParamsForm(LeagueScreenParamsForm):
    """ANG-01e T6 -- ``?season=&team_id=&ordering=`` for ``statistical-feats/``.

    ``team_id`` must be a Team enrolled in the displayed Season (the HTML
    filter's option set); anything else is a 400 ``invalid_choice``.
    """

    ordering_default = "-round"

    season = forms.CharField(required=False)
    team_id = forms.IntegerField(required=False)
    ordering = forms.ChoiceField(required=False)

    def __init__(self, *args: Any, league: League, **kwargs: Any) -> None:
        super().__init__(*args, league=league, **kwargs)
        # Deferred: league_screen_services imports the ORM-heavy modules.
        from matches.league_screen_services import FEATS_SORT_KEYS

        self.fields["ordering"].choices = ordering_choices(sorted(FEATS_SORT_KEYS))

    def clean_team_id(self) -> int | None:
        from matches.league_screen_services import feats_enrolled_teams
        from matches.league_services import pick_displayed_season

        team_id = self.cleaned_data.get("team_id")
        if team_id is None:
            return None
        enrolled = {
            t.id for t in feats_enrolled_teams(pick_displayed_season(self.league))
        }
        if team_id not in enrolled:
            raise forms.ValidationError(
                "Select a valid choice. %(value)s is not one of the available choices.",
                code="invalid_choice",
                params={"value": team_id},
            )
        return team_id


class PlayoffsParamsForm(LeagueScreenParamsForm):
    """ANG-01e T6 -- ``?season=`` for ``playoffs/``; no ``career`` option (a
    bracket has no cross-Season aggregate), so ``career`` is a 400."""

    allow_career = False

    season = forms.CharField(required=False)
