from django import forms
from django.utils import timezone
from teams.models import Team
from .models import Match, Season
from .phase_composer import parse_phase_composition
from core.models import ArenaMap


def _maps_with_confirmed_config():
    return ArenaMap.objects.filter(zone_configs__confirmed=True).distinct()


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        valid_teams = Team.objects.filter(
            id__in=[team.id for team in Team.objects.all() if team.is_valid_roster]
        )
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        valid_teams = Team.objects.filter(
            id__in=[team.id for team in Team.objects.all() if team.is_valid_roster]
        )
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
        mode = cleaned_data.get("map_mode")
        if mode is None:
            return cleaned_data
        pool = cleaned_data.get("map_pool") or []
        pool_count = len(pool)
        if mode == "none" and pool_count > 0:
            raise forms.ValidationError(
                {
                    "map_pool": (
                        "Map pool must be empty when Map mode is " "'3-zone fallback'."
                    )
                }
            )
        if mode == "single" and pool_count != 1:
            raise forms.ValidationError(
                {
                    "map_pool": (
                        "Map pool must contain exactly 1 map when Map "
                        "mode is 'Single map'."
                    )
                }
            )
        if mode == "random_per_round" and pool_count < 1:
            raise forms.ValidationError(
                {
                    "map_pool": (
                        "Map pool must contain at least 1 map when Map "
                        "mode is 'Random per Round'."
                    )
                }
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        valid_teams = Team.objects.filter(
            id__in=[team.id for team in Team.objects.all() if team.is_valid_roster]
        )
        self.fields["team_red"].queryset = valid_teams
        self.fields["team_blue"].queryset = valid_teams
        self.fields["arena_map"].queryset = _maps_with_confirmed_config()
