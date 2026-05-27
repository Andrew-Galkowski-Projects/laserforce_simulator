from django import forms
from django.utils import timezone
from teams.models import Team
from .models import Match
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
