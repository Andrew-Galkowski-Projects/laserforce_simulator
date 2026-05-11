from django import forms
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        valid_teams = Team.objects.filter(
            id__in=[team.id for team in Team.objects.all() if team.is_valid_roster]
        )
        self.fields["team_red"].queryset = valid_teams
        self.fields["team_blue"].queryset = valid_teams
