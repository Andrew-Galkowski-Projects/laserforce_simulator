from collections import Counter
from typing import TYPE_CHECKING

from django import forms
from django.db.models import QuerySet
from django.utils import timezone
from accounts.permissions import owned_queryset
from teams.models import Team
from .finance import MAX_LEVEL
from .league_templates import LEAGUE_TEMPLATES
from .models import MIN_BRACKET_PARTICIPANTS, League, Match, Season
from .phase_composer import parse_phase_composition
from core.forms import ParsedJSONField
from core.models import ArenaMap

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser


def _valid_owned_teams(user: "AbstractBaseUser | None") -> QuerySet[Team]:
    """ANG-01b — the valid-roster subset of the Account's competitive Teams.

    Own + Unmanaged Teams via ``owned_queryset`` over ``Team.objects.regular()``
    (so free-agent pools are excluded); ``user=None`` sees Unmanaged Teams only.
    A foreign / pool / invalid-roster id therefore fails as ``invalid_choice``.
    """
    from .services import _scope_user

    # The roster check reads all six slot FKs; join them so the loop issues one
    # query instead of one per slot per Team.
    owned = owned_queryset(Team.objects.regular(), _scope_user(user)).select_related(
        "slot_commander",
        "slot_heavy",
        "slot_scout_1",
        "slot_scout_2",
        "slot_medic",
        "slot_ammo",
    )
    return Team.objects.filter(
        id__in=[team.id for team in owned if team.is_valid_roster]
    )


def _maps_with_confirmed_config() -> QuerySet[ArenaMap]:
    """Simulation-ready maps: a confirmed zone config plus a red AND a blue base.

    UXP-10 — ``load_map_context`` refuses a map missing either team base at
    simulate time, so every simulation map picker offers only these. The map
    editor / map list keep showing every map.
    """
    return (
        ArenaMap.objects.filter(zone_configs__confirmed=True)
        .filter(base_configs__base_type="red")
        .filter(base_configs__base_type="blue")
        .distinct()
    )


def parse_rotation_ids(
    raw: "str | None", valid_map_ids: "set[int]"
) -> "tuple[list[int], list[str]]":
    """Parse a comma-separated ArenaMap-id string into an AUTHOR-ORDERED id
    list plus a list of human error strings (pure; no ORM, no form state).

    SUB-01 authored this loop inline in ``CreateLeagueForm.clean``; CONF-06
    lifted it out verbatim so the per-Conference rotation composer on
    ``manage_conferences`` parses its rows through the same locked algorithm
    and the same two error strings. Order is preserved (never sorted),
    duplicate ids are kept, empty tokens are skipped silently, and each
    offending token contributes exactly one error string.
    """
    ids: list[int] = []
    errors: list[str] = []
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            map_id = int(token)
        except (TypeError, ValueError):
            errors.append("Map rotation contains an invalid id.")
            continue
        if map_id not in valid_map_ids:
            errors.append("Map rotation contains an unknown map id.")
            continue
        ids.append(map_id)
    return ids, errors


class MatchSetupForm(forms.Form):
    team_red = forms.ModelChoiceField(
        queryset=Team.objects.all(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Red Team",
    )
    team_blue = forms.ModelChoiceField(
        queryset=Team.objects.all(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Blue Team",
    )
    match_type = forms.ChoiceField(
        choices=Match.MATCH_TYPES,
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Match Type",
        initial="friendly",
    )
    arena_map = forms.ModelChoiceField(
        queryset=ArenaMap.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Arena Map",
        required=False,
        empty_label="No map (3-zone fallback)",
    )

    def __init__(self, *args, user: "AbstractBaseUser | None" = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        valid_teams = _valid_owned_teams(user)
        self.fields["team_red"].queryset = valid_teams
        self.fields["team_blue"].queryset = valid_teams
        self.fields["arena_map"].queryset = _maps_with_confirmed_config()


class SingleRoundSetupForm(forms.Form):
    team_red = forms.ModelChoiceField(
        queryset=Team.objects.all(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Red Team",
    )
    team_blue = forms.ModelChoiceField(
        queryset=Team.objects.all(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Blue Team",
    )
    arena_map = forms.ModelChoiceField(
        queryset=ArenaMap.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Arena Map",
        required=False,
        empty_label="No map (3-zone fallback)",
    )

    def __init__(self, *args, user: "AbstractBaseUser | None" = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        valid_teams = _valid_owned_teams(user)
        self.fields["team_red"].queryset = valid_teams
        self.fields["team_blue"].queryset = valid_teams
        self.fields["arena_map"].queryset = _maps_with_confirmed_config()


class CreateLeagueForm(forms.Form):
    """LG-01b — Create-League form.

    Seven fields that drive the LG-01b create-League flow: a League
    name, a Season name, a start date, a team count, a (locked)
    schedule format, and the LG-00 stat mean / std-dev used to
    generate the rosters.
    """

    NUM_TEAMS_CHOICES = (
        (4, "4"),
        (8, "8"),
        (12, "12"),
        (16, "16"),
    )
    SCHEDULE_FORMAT_CHOICES = (("single_round_robin", "Single round-robin"),)
    # CRE-01 — transient difficulty selector. Consumed at create time only to
    # pick which of the N generated teams becomes the manager's current_team
    # (easy → strongest, medium → middle, hard → weakest). No League field, no
    # migration.
    DIFFICULTY_CHOICES = (("easy", "Easy"), ("medium", "Medium"), ("hard", "Hard"))
    # CRE-02 — transient League spread selector. Consumed at create time only to
    # build the per-team tier-mean vector handed to ``_generate_teams``. No League
    # field, no migration. Advanced form only — the template chooser is always Even.
    LEAGUE_SPREAD_CHOICES = (
        ("even", "Even"),
        ("tiered", "Tiered"),
        ("steep", "Steep"),
    )

    league_name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(
            attrs={
                "id": "league-create-league-name",
                "class": "form-control",
                "autocomplete": "off",
            }
        ),
        label="League name",
    )
    manager_team_name = forms.CharField(
        max_length=100,
        required=False,
        label="Your team name",
        widget=forms.TextInput(
            attrs={
                "id": "league-create-manager-team-name",
                "class": "form-control",
                "autocomplete": "off",
            }
        ),
    )
    # CRE-01 — transient difficulty (consumed at create time only). Shared by
    # both the chooser-assembled form and the Advanced form. ``required=False``
    # so an Advanced POST that omits it stays valid (defaults Medium); the
    # manager-team pick coerces a falsy value to "medium".
    difficulty = forms.ChoiceField(
        choices=DIFFICULTY_CHOICES,
        initial="medium",
        required=False,
        widget=forms.Select(
            attrs={"id": "league-create-difficulty", "class": "form-select"}
        ),
        label="Difficulty",
    )
    # CRE-02 — transient League spread (consumed at create time only).
    # ``required=False`` so an Advanced POST that omits it stays valid (defaults
    # Even); the tier-vector build coerces a falsy/unknown value to "even".
    league_spread = forms.ChoiceField(
        choices=LEAGUE_SPREAD_CHOICES,
        initial="even",
        required=False,
        widget=forms.Select(
            attrs={"id": "league-create-league-spread", "class": "form-select"}
        ),
        label="League spread",
    )
    season_name = forms.CharField(
        max_length=100,
        initial="Season 1",
        widget=forms.TextInput(
            attrs={
                "id": "league-create-season-name",
                "class": "form-control",
                "autocomplete": "off",
            }
        ),
        label="Season name",
    )
    start_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(
            attrs={
                "id": "league-create-start-date",
                "class": "form-control",
                "type": "date",
            }
        ),
        label="Start date",
    )
    num_teams = forms.TypedChoiceField(
        choices=NUM_TEAMS_CHOICES,
        coerce=int,
        empty_value=None,
        initial=4,
        widget=forms.Select(
            attrs={
                "id": "league-create-num-teams",
                "class": "form-select",
            }
        ),
        label="Number of teams",
    )
    schedule_format = forms.ChoiceField(
        choices=SCHEDULE_FORMAT_CHOICES,
        disabled=True,
        initial="single_round_robin",
        widget=forms.Select(
            attrs={
                "id": "league-create-schedule-format",
                "class": "form-select",
                "disabled": "disabled",
            }
        ),
        label="Schedule format",
    )
    mean = forms.IntegerField(
        min_value=0,
        max_value=100,
        initial=50,
        widget=forms.NumberInput(
            attrs={
                "id": "league-create-mean",
                "class": "form-control",
                "min": "0",
                "max": "100",
                "autocomplete": "off",
            }
        ),
        label="Stat mean",
    )
    std_dev = forms.IntegerField(
        min_value=1,
        max_value=40,
        initial=15,
        widget=forms.NumberInput(
            attrs={
                "id": "league-create-std-dev",
                "class": "form-control",
                "min": "1",
                "max": "40",
                "autocomplete": "off",
            }
        ),
        label="Stat standard deviation",
    )
    # LG-01j — per-Season arena map configuration. Two new fields appended
    # to the locked LG-01b 7-field block; total field count is now 9 in
    # the locked order league_name → season_name → start_date → num_teams
    # → schedule_format → mean → std_dev → map_mode → map_pool.
    map_mode = forms.ChoiceField(
        choices=Season._meta.get_field("map_mode").choices,
        initial="none",
        required=True,
        widget=forms.Select(
            attrs={
                "id": "league-create-map-mode",
                "class": "form-select",
            }
        ),
        label="Map mode",
    )
    map_pool = forms.ModelMultipleChoiceField(
        queryset=_maps_with_confirmed_config(),
        required=False,
        widget=forms.SelectMultiple(
            attrs={
                "id": "league-create-map-pool",
                "class": "form-select",
            }
        ),
        label="Map pool",
    )
    # SUB-01 — hidden author-ordered rotation list (comma-joined ArenaMap ids
    # in row order). The create.html rotation composer serializes the ordered
    # rows into this field; ``clean()`` parses + validates it.
    map_rotation = forms.CharField(
        widget=forms.HiddenInput(attrs={"id": "league-create-map-rotation"}),
        required=False,
    )
    # FIN-01 — per-League team finance toggle (default OFF). Gates the whole
    # finance subsystem ON TOP of career mode.
    finance_enabled = forms.BooleanField(
        required=False,
        initial=False,
        label="Enable team finances",
        widget=forms.CheckboxInput(attrs={"id": "league-create-finance-enabled"}),
    )
    # FIN-05 — luxury-tax challenge-mode firing toggle (default OFF). Only takes
    # effect with team finances enabled.
    challenge_fired_luxury_tax = forms.BooleanField(
        required=False,
        initial=False,
        label="Fire on luxury tax",
        widget=forms.CheckboxInput(attrs={"id": "league-create-challenge-luxury-tax"}),
    )
    # CONF-05 — optional Conference partition. When > 0 the create-League flow
    # pre-creates that many Conferences (the generated Teams auto-split evenly)
    # and redirects to the Manage Conferences composer; 0 = single flat league.
    number_of_conferences = forms.TypedChoiceField(
        choices=(
            (0, "None (single league)"),
            (2, "2"),
            (3, "3"),
            (4, "4"),
        ),
        coerce=int,
        empty_value=0,
        initial=0,
        required=False,
        widget=forms.Select(
            attrs={
                "id": "league-create-number-of-conferences",
                "class": "form-select",
            }
        ),
        label="Conferences",
    )
    # UX-01 — DORMANT: authored at create time, read by nothing this slice.
    # The forward-compatible marker for who may join a League once League
    # membership exists (ADR-0038). ``required=False`` so a POST that omits it
    # stays valid; the create path coerces a falsy value to "closed".
    visibility = forms.ChoiceField(
        choices=League.VISIBILITY_CHOICES,
        initial="closed",
        required=False,
        widget=forms.Select(
            attrs={"id": "league-create-visibility", "class": "form-select"}
        ),
        label="League visibility",
    )
    # LG-02-Part2b — hidden composer serialization. The create.html JS
    # serializes the ordered phase rows into this field as a comma-joined
    # list of phase-type tokens; ``clean()`` parses it into phase specs.
    phases = forms.CharField(
        widget=forms.HiddenInput(attrs={"id": "league-create-phases"}),
        required=False,
    )

    def clean(self):
        """LG-01j — cross-field mode-vs-pool count rules.

        Three rules (locked error messages):
            * ``none`` ⇒ pool MUST be empty.
            * ``single`` ⇒ pool MUST contain exactly 1 map.
            * ``random_per_round`` ⇒ pool MUST contain ≥ 1 map.

        Errors attach to ``map_pool`` (NOT ``map_mode``) so the help
        text co-locates with the field the user clicked wrong. When
        ``map_mode`` failed its own field-level validation, skip the
        cross-field rule (defensive — ``cleaned_data["map_mode"]`` is
        absent in that case).
        """
        cleaned_data = super().clean()
        # LG-02-Part2b — parse the composer wire format into phase specs.
        # Empty/blank ``phases`` falls back to a single round_robin phase
        # (the Part2a default). ``schedule_format`` is the disabled field's
        # locked ``"single_round_robin"``; default defensively.
        try:
            specs = parse_phase_composition(
                cleaned_data.get("phases", "") or "",
                season_schedule_format=cleaned_data.get("schedule_format")
                or "single_round_robin",
            )
        except ValueError as exc:
            self.add_error("phases", forms.ValidationError(str(exc)))
        else:
            cleaned_data["phase_specs"] = specs
        # CONF-05 — a Conference partition needs >= 2 teams per Conference, so
        # the team count must cover ``2 * N``. Error attaches to the dropdown.
        #
        # CONF-02 — when the composition also carries a ``tournament`` phase,
        # every Conference fields its OWN Regional playoff bracket, so the floor
        # rises to the bracket engine's participant minimum: ``4 * N``. Without
        # this, e.g. 4 teams across 2 Conferences yields two 2-team brackets,
        # and the build raises out of the atomic activation and rolls back the
        # play-week that triggered it, leaving the Season permanently unable to
        # finish its round-robin.
        n_conf = cleaned_data.get("number_of_conferences") or 0
        num_teams = cleaned_data.get("num_teams")
        specs = cleaned_data.get("phase_specs") or []
        has_tournament_phase = any(
            getattr(spec, "phase_type", None) == "tournament" for spec in specs
        )
        min_per_conf = MIN_BRACKET_PARTICIPANTS if has_tournament_phase else 2
        if n_conf and num_teams is not None and num_teams < min_per_conf * n_conf:
            if has_tournament_phase:
                message = (
                    f"{n_conf} conferences with a tournament phase need at least "
                    f"{min_per_conf * n_conf} teams "
                    f"({min_per_conf} per conference to field a playoff bracket)."
                )
            else:
                message = f"{n_conf} conferences need at least {2 * n_conf} teams."
            self.add_error("number_of_conferences", forms.ValidationError(message))
        mode = cleaned_data.get("map_mode")
        if mode is None:
            return cleaned_data
        # SUB-01 — order-preserving parse of the rotation wire string into an
        # ordered list[int] (NOT sorted). Validate every id is a confirmed map.
        # CONF-06 — the loop now lives in the module-level ``parse_rotation_ids``
        # so ``manage_conferences`` parses its per-Conference rows identically.
        valid_map_ids = set(_maps_with_confirmed_config().values_list("id", flat=True))
        rotation_ids, rotation_errors = parse_rotation_ids(
            cleaned_data.get("map_rotation", ""), valid_map_ids
        )
        for message in rotation_errors:
            self.add_error("map_rotation", forms.ValidationError(message))
        cleaned_data["map_rotation_ids"] = rotation_ids
        pool = cleaned_data.get("map_pool") or []
        pool_count = len(pool)
        rotation_count = len(cleaned_data.get("map_rotation_ids") or [])
        # SUB-01 — full 4×2 mode-vs-(pool, rotation) cross-guard matrix.
        if mode == "none":
            if pool_count > 0:
                raise forms.ValidationError(
                    {
                        "map_pool": (
                            "Map pool must be empty when Map mode is "
                            "'3-zone fallback'."
                        )
                    }
                )
            if rotation_count > 0:
                raise forms.ValidationError(
                    {
                        "map_rotation": (
                            "Map rotation must be empty when Map mode is "
                            "'3-zone fallback'."
                        )
                    }
                )
        if mode == "single":
            if pool_count != 1:
                raise forms.ValidationError(
                    {
                        "map_pool": (
                            "Map pool must contain exactly 1 map when Map "
                            "mode is 'Single map'."
                        )
                    }
                )
            if rotation_count > 0:
                raise forms.ValidationError(
                    {
                        "map_rotation": (
                            "Map rotation must be empty when Map mode is "
                            "'Single map'."
                        )
                    }
                )
        if mode == "random_per_round":
            if pool_count < 1:
                raise forms.ValidationError(
                    {
                        "map_pool": (
                            "Map pool must contain at least 1 map when Map "
                            "mode is 'Random per Round'."
                        )
                    }
                )
            if rotation_count > 0:
                raise forms.ValidationError(
                    {
                        "map_rotation": (
                            "Map rotation must be empty when Map mode is "
                            "'Random per Round'."
                        )
                    }
                )
        if mode == "rotate_by_matchday":
            if pool_count > 0:
                raise forms.ValidationError(
                    {
                        "map_pool": (
                            "Map pool must be empty when Map mode is "
                            "'Rotate by matchday'."
                        )
                    }
                )
            if rotation_count < 1:
                raise forms.ValidationError(
                    {
                        "map_rotation": (
                            "Map rotation must contain at least 1 map when Map "
                            "mode is 'Rotate by matchday'."
                        )
                    }
                )
        # CONF-06 — the per-Conference rotation is authored on the
        # Manage-Conferences page, not here, so BOTH Season-level lists must be
        # empty; ``add_error`` (not ``raise``) so all three surface together.
        if mode == "rotate_by_conference":
            if pool_count > 0:
                self.add_error(
                    "map_pool",
                    forms.ValidationError(
                        "Map pool must be empty when Map mode is "
                        "'Rotate by Conference'."
                    ),
                )
            if rotation_count > 0:
                self.add_error(
                    "map_rotation",
                    forms.ValidationError(
                        "Map rotation must be empty when Map mode is "
                        "'Rotate by Conference'."
                    ),
                )
            if (cleaned_data.get("number_of_conferences") or 0) < 2:
                self.add_error(
                    "number_of_conferences",
                    forms.ValidationError(
                        "Map mode 'Rotate by Conference' requires at least "
                        "2 conferences."
                    ),
                )
        return cleaned_data


class BatchSimulateForm(forms.Form):
    N_CHOICES = [("10", "10"), ("50", "50"), ("100", "100"), ("500", "500")]

    team_red = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Red Team",
    )
    team_blue = forms.ModelChoiceField(
        queryset=Team.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Blue Team",
    )
    n = forms.ChoiceField(
        choices=N_CHOICES,
        initial="100",
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Number of simulations",
    )
    arena_map = forms.ModelChoiceField(
        queryset=ArenaMap.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
        label="Arena Map",
        required=False,
        empty_label="No map (3-zone fallback)",
    )

    def __init__(self, *args, user: "AbstractBaseUser | None" = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        valid_teams = _valid_owned_teams(user)
        self.fields["team_red"].queryset = valid_teams
        self.fields["team_blue"].queryset = valid_teams
        self.fields["arena_map"].queryset = _maps_with_confirmed_config()


class SaveBatchGamesForm(forms.Form):
    """ANG-01b — API-only write Form for ``POST /api/simulate-batch/<job_id>/save/``.

    API-03 has no HTML Form for the save step, so ADR-0039's Form-bound-write
    rule is met with this one. ``n`` is strict here (the HTML view clamps).
    """

    team_red = forms.IntegerField(min_value=1)
    team_blue = forms.IntegerField(min_value=1)
    arena_map = forms.IntegerField(min_value=1, required=False)
    game_type = forms.ChoiceField(choices=[("avg", "avg"), ("outlier", "outlier")])
    n = forms.IntegerField(min_value=1, max_value=10)


# ---------------------------------------------------------------------------
# ANG-01d-1 — Conference partition + League lifecycle write Forms
# ---------------------------------------------------------------------------

_CONFERENCE_KEYS: frozenset[str] = frozenset({"name", "team_ids", "map_rotation_ids"})
_CONFERENCE_SHAPE_ERROR = (
    'conferences must be a list of {"name", "team_ids", "map_rotation_ids"} objects.'
)


def _is_json_int(value: object) -> bool:
    """A JSON integer — ``bool`` is an ``int`` subclass and is excluded."""
    return isinstance(value, int) and not isinstance(value, bool)


def _conference_shape_ok(conf: object) -> bool:
    """One submitted Conference object has the locked step-1 shape."""
    if not isinstance(conf, dict) or not set(conf) <= _CONFERENCE_KEYS:
        return False
    if not isinstance(conf.get("name"), str):
        return False
    team_ids = conf.get("team_ids")
    if not isinstance(team_ids, list) or not all(_is_json_int(t) for t in team_ids):
        return False
    rotation = conf.get("map_rotation_ids")
    return rotation is None or isinstance(rotation, (list, str))


class ConferencePartitionForm(forms.Form):
    """ANG-01d-1 — validate a Season's Conference partition (shared HTML + API).

    ``conferences`` is already-decoded JSON: a list of
    ``{"name", "team_ids", "map_rotation_ids"}`` objects (``map_rotation_ids``
    optional, a comma-joined id string or a list of ints). ``clean_conferences``
    runs, in order: the shape check, the membership check (duplicate /
    non-enrolled team ids — unreachable from the HTML composer), the CONF-05
    partition rules (CONF-02 bracket floor when the Season has a tournament
    phase), the CONF-06 rotation parse, and the rotate-by-conference guard.
    Returns ``[(name, sorted_team_ids, rotation_ids), ...]`` (``[]`` clears).
    """

    conferences = ParsedJSONField()

    def __init__(self, *args: object, season: Season, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.season = season

    def clean_conferences(self) -> "list[tuple[str, list[int], list[int]]]":
        # Lazy: ``league_services`` imports models / teams.views; importing it
        # at module load would create a ``forms`` -> ``league_services`` edge.
        from .league_services import _validate_conference_partition

        value = self.cleaned_data["conferences"]

        # 1. Shape.
        if not isinstance(value, list) or not all(
            _conference_shape_ok(c) for c in value
        ):
            raise forms.ValidationError(_CONFERENCE_SHAPE_ERROR, code="invalid")

        # 2. Membership — duplicates, then non-enrolled ids, each ascending.
        enrolled = {t.id for t in self.season.teams.all()}
        counts = Counter(tid for conf in value for tid in conf["team_ids"])
        membership = [
            f"Team {tid} is in more than one conference."
            for tid in sorted(t for t, n in counts.items() if n > 1)
        ] + [
            f"Team {tid} is not enrolled in this Season."
            for tid in sorted(t for t in counts if t not in enrolled)
        ]
        if membership:
            raise forms.ValidationError(
                [forms.ValidationError(m, code="invalid") for m in membership]
            )

        # 3. Partition (the CONF-05 validator, strings unchanged).
        names = [conf["name"] for conf in value]
        team_to_conf_idx = {
            tid: idx for idx, conf in enumerate(value) for tid in conf["team_ids"]
        }
        has_tournament_phase = self.season.phases.filter(
            phase_type="tournament"
        ).exists()
        errors, normalized = _validate_conference_partition(
            names,
            team_to_conf_idx,
            enrolled,
            min_per_conference=(
                MIN_BRACKET_PARTICIPANTS if has_tournament_phase else 2
            ),
        )

        # 4. Rotation — a str goes through ``parse_rotation_ids``; a list keeps
        # its order + duplicates, each item a known JSON int.
        valid_map_ids = set(_maps_with_confirmed_config().values_list("id", flat=True))
        rotations: list[list[int]] = []
        for conf in value:
            raw = conf.get("map_rotation_ids")
            if raw is None:
                raw = ""
            if isinstance(raw, str):
                ids, row_errors = parse_rotation_ids(raw, valid_map_ids)
            else:
                ids, row_errors = [], []
                for item in raw:
                    if not _is_json_int(item):
                        row_errors.append("Map rotation contains an invalid id.")
                    elif item not in valid_map_ids:
                        row_errors.append("Map rotation contains an unknown map id.")
                    else:
                        ids.append(item)
            rotations.append(ids)
            errors.extend(row_errors)

        # 5. Guard — appended once, not per offending Conference.
        if (
            self.season.map_mode == "rotate_by_conference"
            and value
            and any(not ids for ids in rotations)
        ):
            errors.append("Each conference needs at least 1 rotation map.")

        if errors:
            raise forms.ValidationError(
                [forms.ValidationError(m, code="invalid") for m in errors]
            )
        return [
            (name, team_ids, rotations[idx])
            for idx, (name, team_ids) in enumerate(normalized)
        ]


class LeagueTemplateForm(forms.Form):
    """ANG-01d-1 — API-only: the League-template key for the template-path create.

    Unknown key ⇒ ``invalid_choice``; empty / absent ⇒ ``required``. The HTML
    chooser keeps its own ``LEAGUE_TEMPLATES_BY_KEY`` lookup.
    """

    template = forms.ChoiceField(choices=[(t.key, t.label) for t in LEAGUE_TEMPLATES])


class ReassignTeamForm(forms.Form):
    """ANG-01d-1 — API-only: the fired Manager's pick among the eligible teams.

    Choices are the eligible (worst-N) team ids; a non-int or ineligible id ⇒
    ``invalid_choice``, absent ⇒ ``required``. The HTML ``reassign_team`` keeps
    its own int parse and 400 strings.
    """

    team_id = forms.TypedChoiceField(coerce=int, choices=())

    def __init__(
        self, *args: object, eligible_team_ids: "list[int]", **kwargs: object
    ) -> None:
        super().__init__(*args, **kwargs)
        self.fields["team_id"].choices = [(i, str(i)) for i in eligible_team_ids]


MEMBER_NIGHT_NO_SITES_ERROR = "Pick at least one Site for the member night."


class MemberNightSetupForm(forms.Form):
    """ANG-01d-2 — the Sites to draw member-night games for (HTML + API).

    ``sites`` is a multiple choice over the Season's VIABLE Sites (``>=
    member_night.MIN_POOL`` available players). An empty selection is
    ``required``; a non-viable / unknown Site is ``invalid_choice``.
    """

    sites = forms.MultipleChoiceField(
        choices=(), error_messages={"required": MEMBER_NIGHT_NO_SITES_ERROR}
    )

    def __init__(self, *args: object, season: Season, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        # Lazy: ``league_play_services`` imports models / simulation; importing
        # it at module load would create a ``forms`` -> services edge.
        from .league_play_services import viable_member_night_sites

        self.season = season
        self.fields["sites"].choices = [
            (site, site) for site, _count in viable_member_night_sites(season)
        ]


def _budget_level_field(label: str) -> forms.IntegerField:
    return forms.IntegerField(min_value=1, max_value=MAX_LEVEL, label=label)


class TeamFinancesForm(forms.ModelForm):
    """ANG-01e — the managed Team's budgets, ticket price and injury policy.

    Bound by the HTML Team Finances POST and the API PATCH (via
    ``merged_form_data``). Out-of-range values are rejected, never clamped:
    budgets are integers ``1..finance.MAX_LEVEL``, ``ticket_price`` is ``>= 0``,
    ``injury_policy`` is one of ``Team.INJURY_POLICY_CHOICES``. Field labels are
    the human names the HTML error list shows (the API keeps field names).
    """

    budget_scouting = _budget_level_field("Scouting")
    budget_coaching = _budget_level_field("Coaching")
    budget_facilities = _budget_level_field("Facilities")
    budget_health = _budget_level_field("Health")
    ticket_price = forms.FloatField(min_value=0, label="Ticket price")

    class Meta:
        model = Team
        fields = [
            "budget_scouting",
            "budget_coaching",
            "budget_facilities",
            "budget_health",
            "injury_policy",
            "ticket_price",
        ]
        labels = {"injury_policy": "Injury policy"}
