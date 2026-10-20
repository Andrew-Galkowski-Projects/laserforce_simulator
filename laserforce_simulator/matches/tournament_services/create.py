"""ANG-01f-3 -- creating a Tournament: the strict create Form and the service.

Shared by the HTML create page and ``POST /api/tournaments/`` (ADR-0039), so
both surfaces reject bad input the same way. The Form keeps the HTML POST
field names (``rrde_combo`` such as ``"8/4"``, ``final_series_length``, ...).

Strictness rule: an ABSENT (or blank) optional field takes its documented
default, but a PRESENT value that is not valid is a field error, never silently
changed to a default.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from django import forms
from django.db import transaction

from matches.bracket import default_seed_order
from matches.forms import _valid_owned_teams
from matches.models import Tournament, TournamentParticipant
from matches.tournament_services.seeding import mean_ratings_by_team
from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Team
from teams.services import _generate_teams

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

# LG-02c (RR->DE) -- the 6 locked (wb, lb) advancer shape combos, in select
# order. Value string format "wb/lb" (e.g. "4/0"); lb is 0 or wb//2.
RRDE_COMBOS: tuple[tuple[str, int, int], ...] = (
    ("4/0", 4, 0),
    ("4/2", 4, 2),
    ("8/0", 8, 0),
    ("8/4", 8, 4),
    ("16/0", 16, 0),
    ("16/8", 16, 8),
)
_RRDE_COMBO_BY_VALUE: dict[str, tuple[int, int]] = {
    value: (wb, lb) for value, wb, lb in RRDE_COMBOS
}
_DEFAULT_RRDE_COMBO = "4/0"

_SERIES_LENGTH_CHOICES = ((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5"))
SERIES_LENGTH_FIELDS: tuple[str, ...] = (
    "final_series_length",
    "semifinal_series_length",
    "quarterfinal_series_length",
    "earlier_series_length",
)

MIN_PRESET_TEAMS = 4
# A tournament Team must field a full 6-player roster, so generated Teams get
# at least 6 players: a smaller request is a ``min_value`` error (ANG-01f
# review; absent means 6).
_MIN_GENERATED_PLAYERS = 6
_GENERATED_MEAN = 50
_GENERATED_STD_DEV = 15


def _series_length_field() -> forms.TypedChoiceField:
    return forms.TypedChoiceField(
        choices=_SERIES_LENGTH_CHOICES, coerce=int, required=False, empty_value=1
    )


class TournamentCreateForm(forms.Form):
    """The strict create Form: all five formats, preset or Player-pool draw.

    ``teams`` offers only the requester's own and Unmanaged regular Teams with
    a valid Roster, so another Account's Team (or an incomplete one) is an
    ``invalid_choice``. A preset Tournament needs at least 4 Teams, counting
    selected and generated ones; a Player-pool draw starts with an empty pool
    and ignores ``teams`` / ``generate_*``.
    """

    name = forms.CharField(
        max_length=100,
        error_messages={"required": "Tournament name is required."},
    )
    format = forms.ChoiceField(choices=Tournament.FORMAT_CHOICES, required=False)
    team_assembly = forms.ChoiceField(
        choices=Tournament.TEAM_ASSEMBLY_CHOICES, required=False
    )
    teams = forms.ModelMultipleChoiceField(queryset=Team.objects.none(), required=False)
    generate_count = forms.IntegerField(min_value=0, required=False)
    generate_ppt = forms.IntegerField(min_value=_MIN_GENERATED_PLAYERS, required=False)
    rrde_combo = forms.ChoiceField(
        choices=[(value, value) for value, _, _ in RRDE_COMBOS], required=False
    )
    swiss_rounds = forms.IntegerField(min_value=0, required=False)
    role_assignment_mode = forms.ChoiceField(
        choices=Tournament.ROLE_ASSIGNMENT_CHOICES, required=False
    )
    final_series_length = _series_length_field()
    semifinal_series_length = _series_length_field()
    quarterfinal_series_length = _series_length_field()
    earlier_series_length = _series_length_field()

    def __init__(
        self,
        data: Any = None,
        *,
        user: "AbstractBaseUser | None",
        **kwargs: Any,
    ) -> None:
        super().__init__(data, **kwargs)
        self.fields["teams"].queryset = _valid_owned_teams(user).order_by("name")

    # Absent / blank optional fields take their documented defaults.

    def clean_format(self) -> str:
        return self.cleaned_data["format"] or "single_elimination"

    def clean_team_assembly(self) -> str:
        return self.cleaned_data["team_assembly"] or "preset"

    def clean_role_assignment_mode(self) -> str:
        return self.cleaned_data["role_assignment_mode"] or "random"

    def clean_rrde_combo(self) -> str:
        return self.cleaned_data["rrde_combo"] or _DEFAULT_RRDE_COMBO

    def clean_generate_count(self) -> int:
        return self.cleaned_data["generate_count"] or 0

    def clean_generate_ppt(self) -> int:
        value = self.cleaned_data["generate_ppt"]
        return _MIN_GENERATED_PLAYERS if value is None else value

    def clean_swiss_rounds(self) -> int:
        return self.cleaned_data["swiss_rounds"] or 0

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        if cleaned.get("team_assembly") != "preset":
            return cleaned
        if "teams" not in cleaned or "generate_count" not in cleaned:
            # A field error already explains the problem; skip the count rule.
            return cleaned
        if len(cleaned["teams"]) + cleaned["generate_count"] < MIN_PRESET_TEAMS:
            self.add_error(
                "teams",
                forms.ValidationError(
                    "A tournament requires at least %(min)d teams.",
                    code="too_few_teams",
                    params={"min": MIN_PRESET_TEAMS},
                ),
            )
        return cleaned


@transaction.atomic
def create_tournament(
    form: TournamentCreateForm, *, user: "AbstractBaseUser | None"
) -> Tournament:
    """Create the Tournament from a valid ``form``, stamped with ``user``.

    A preset Tournament gets its selected plus generated Teams as participants
    with the default Seeding (mean active-player rating). A Player-pool draw is
    created in setup with an empty pool; the draw seeds it later.
    """
    data = form.cleaned_data
    manager = user if user is not None and user.is_authenticated else None
    tournament_format = data["format"]
    if tournament_format == "round_robin_double_elim":
        wb_advancers, lb_advancers = _RRDE_COMBO_BY_VALUE[data["rrde_combo"]]
    else:
        wb_advancers, lb_advancers = 0, 0

    tournament = Tournament.objects.create(
        name=data["name"],
        state="setup",
        format=tournament_format,
        wb_advancers=wb_advancers,
        lb_advancers=lb_advancers,
        swiss_rounds=data["swiss_rounds"],
        team_assembly=data["team_assembly"],
        role_assignment_mode=data["role_assignment_mode"],
        manager=manager,
        **{field: data[field] for field in SERIES_LENGTH_FIELDS},
    )
    if data["team_assembly"] == "preset":
        _add_preset_participants(tournament, _preset_teams(data, manager))
    return tournament


def _preset_teams(
    data: dict[str, Any], manager: "AbstractBaseUser | None"
) -> list[Team]:
    teams = list(data["teams"])
    if data["generate_count"]:
        teams.extend(
            _generate_teams(
                data["generate_count"],
                data["generate_ppt"],
                rng=random.Random(),
                mean=_GENERATED_MEAN,
                std_dev=_GENERATED_STD_DEV,
                team_names_pool=list(TEAM_NAMES),
                player_names_pool=list(PLAYER_NAMES),
                manager=manager,
            )
        )
    return teams


def _add_preset_participants(tournament: Tournament, teams: list[Team]) -> None:
    """Enter ``teams`` with the default Seeding (dense seeds 1..N)."""
    rating_by_id = mean_ratings_by_team(t.id for t in teams)
    seed_order = default_seed_order([(t.id, rating_by_id[t.id]) for t in teams])
    team_by_id = {t.id: t for t in teams}
    for idx, team_id in enumerate(seed_order, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team_by_id[team_id], seed=idx
        )
