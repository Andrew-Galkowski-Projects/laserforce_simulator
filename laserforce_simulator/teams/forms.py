from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator

from .models import Team, Player, ROLE_CHOICES

# PD-2: every text/number input carries autocomplete (a11y) — these are
# gameplay stats / identifiers, not browser-autofillable personal data.
_STAT_WIDGET_ATTRS = {
    "class": "form-control",
    "min": "0",
    "max": "100",
    "autocomplete": "off",
}

# CT-2: sensible bounds for the profile number fields (previously
# unbounded — reported valuemin=0 valuemax=0 and accepted negatives).
_PROFILE_BOUNDS = {
    "age": (5, 100),
    "started_playing_age": (3, 100),
    "total_games": (0, 100_000),
}


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
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter player name",
                    "autocomplete": "off",
                }
            ),
            "age": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "5",
                    "max": "100",
                    "autocomplete": "off",
                }
            ),
            "started_playing_age": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "3",
                    "max": "100",
                    "autocomplete": "off",
                }
            ),
            "total_games": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": "0",
                    "max": "100000",
                    "autocomplete": "off",
                }
            ),
            "home_site": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. Ultrazone Chicago",
                    "autocomplete": "off",
                }
            ),
            "height": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 5'11\"",
                    "autocomplete": "off",
                }
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
        # CT-2: enforce the profile bounds server-side too (widget min/max
        # alone is bypassable). Model fields are unchanged → no migration.
        for name, (lo, hi) in _PROFILE_BOUNDS.items():
            self.fields[name].validators.append(MinValueValidator(lo))
            self.fields[name].validators.append(MaxValueValidator(hi))

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
                # CT-1: slots are null/blank on the model — keep every slot
                # optional so partial rosters can be saved incrementally
                # (and server-side validation is reachable past the browser).
                self.fields[field_name].required = False
                self.fields[field_name].widget.attrs["class"] = "form-select"
