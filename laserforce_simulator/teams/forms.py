from django import forms
from .models import Team, Player


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
    class Meta:
        model = Player
        fields = ["name", "role"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Enter player name"}
            ),
            "role": forms.Select(attrs={"class": "form-control"}),
        }
