from django import forms
from .models import Team, Player, ROLE_CHOICES

_STAT_WIDGET_ATTRS = {"class": "form-control", "min": "0", "max": "100"}


class TeamForm(forms.ModelForm):
    class Meta:
        model = Team
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Enter team name"}
            )
        }


class PlayerForm(forms.ModelForm):
    preferred_roles = forms.MultipleChoiceField(
        choices=ROLE_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Preferred Roles",
        help_text="Roles this player excels at (used for future stat boosts)",
    )

    class Meta:
        model = Player
        fields = [
            "name",
            "preferred_roles",
            "age",
            "started_playing_age",
            "total_games",
            "home_site",
            "height",
            "player_awareness",
            "game_awareness",
            "resource_awareness",
            "decision_making",
            "positioning",
            "stamina",
            "speed",
            "flexibility",
            "adaptability",
            "communication",
            "teamwork",
            "Offensive_synergy",
            "defensive_synergy",
            "midfield_synergy",
            "resupply_synergy",
            "resupply_efficiency",
            "accuracy",
            "survival",
            "special_usage",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Enter player name"}
            ),
            "age": forms.NumberInput(attrs={"class": "form-control", "min": "0"}),
            "started_playing_age": forms.NumberInput(
                attrs={"class": "form-control", "min": "0"}
            ),
            "total_games": forms.NumberInput(
                attrs={"class": "form-control", "min": "0"}
            ),
            "home_site": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. Ultrazone Chicago"}
            ),
            "height": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. 5'11\""}
            ),
            "player_awareness": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "game_awareness": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "resource_awareness": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "decision_making": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "positioning": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "stamina": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "speed": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "flexibility": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "adaptability": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "communication": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "teamwork": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "Offensive_synergy": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "defensive_synergy": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "midfield_synergy": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "resupply_synergy": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "resupply_efficiency": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "accuracy": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "survival": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
            "special_usage": forms.NumberInput(attrs=_STAT_WIDGET_ATTRS),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.preferred_roles:
            self.initial["preferred_roles"] = self.instance.preferred_roles

    def clean_preferred_roles(self):
        return list(self.cleaned_data.get("preferred_roles", []))


class TeamSlotForm(forms.ModelForm):
    """Assign players to role slots on a team."""

    class Meta:
        model = Team
        fields = [
            "slot_commander",
            "slot_heavy",
            "slot_scout_1",
            "slot_scout_2",
            "slot_medic",
            "slot_ammo",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        team = kwargs.get("instance")
        if team:
            qs = team.players.all()
            for field_name in self.fields:
                self.fields[field_name].queryset = qs
                self.fields[field_name].required = field_name != "slot_scout_2"
                self.fields[field_name].widget.attrs["class"] = "form-select"
