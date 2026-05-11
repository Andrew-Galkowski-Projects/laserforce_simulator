from django import forms
from .models import Team, Player, ROLE_CHOICES


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
        fields = ["name", "preferred_roles"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Enter player name"}
            ),
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
