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


class GenerateLeagueForm(forms.Form):
    """LG-00 bulk player/team generation form.

    Two dropdowns + two integer fields. The cross-field rule between
    ``num_teams`` and ``players_per_team`` is enforced in `clean()` (no
    JS-driven dependent toggle for v1).
    """

    # 22 entries: "0", "2"..."20", "random_2_10". "1" is intentionally omitted.
    NUM_TEAMS_CHOICES = (
        ("0", "0 (free-agent pool)"),
        ("2", "2"),
        ("3", "3"),
        ("4", "4"),
        ("5", "5"),
        ("6", "6"),
        ("7", "7"),
        ("8", "8"),
        ("9", "9"),
        ("10", "10"),
        ("11", "11"),
        ("12", "12"),
        ("13", "13"),
        ("14", "14"),
        ("15", "15"),
        ("16", "16"),
        ("17", "17"),
        ("18", "18"),
        ("19", "19"),
        ("20", "20"),
        ("random_2_10", "Random (2–10)"),
    )

    # 98 entries: team-mode 6..9 + "random_team", pool-mode 12..100 +
    # "random_pool". The wide superset renders always; `clean()` enforces
    # which subset is legal for the chosen `num_teams`.
    _TEAM_MODE_PPT = (("6", "6"), ("7", "7"), ("8", "8"), ("9", "9"))
    _POOL_MODE_PPT = tuple((str(n), str(n)) for n in range(12, 101))
    PLAYERS_PER_TEAM_CHOICES = (
        *_TEAM_MODE_PPT,
        ("random_team", "Random (6–8)"),
        *_POOL_MODE_PPT,
        ("random_pool", "Random (12–100)"),
    )

    num_teams = forms.CharField(
        widget=forms.Select(
            choices=NUM_TEAMS_CHOICES,
            attrs={"id": "generate-players-num-teams", "class": "form-select"},
        ),
        label="Number of teams",
        initial="random_2_10",
    )
    players_per_team = forms.CharField(
        widget=forms.Select(
            choices=PLAYERS_PER_TEAM_CHOICES,
            attrs={"id": "generate-players-per-team", "class": "form-select"},
        ),
        label="Players per team",
        initial="random_team",
    )
    mean = forms.IntegerField(
        min_value=0,
        max_value=100,
        initial=50,
        widget=forms.NumberInput(
            attrs={
                "id": "generate-players-mean",
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
                "id": "generate-players-std-dev",
                "class": "form-control",
                "min": "1",
                "max": "40",
                "autocomplete": "off",
            }
        ),
        label="Stat standard deviation",
    )

    def clean(self):
        cleaned = super().clean()
        nt = cleaned.get("num_teams")
        ppt = cleaned.get("players_per_team")
        # Short-circuit when either field failed its own validation —
        # otherwise the cross-field message masks the field-level
        # "required" / "invalid choice" error the user actually needs to fix.
        if not nt or not ppt:
            return cleaned
        if nt == "0":
            if ppt != "random_pool" and not (ppt.isdigit() and 12 <= int(ppt) <= 100):
                raise forms.ValidationError(
                    "Players per team must be 12–100 when generating a free-agent pool"
                )
        else:
            if ppt not in {"6", "7", "8", "9", "random_team"}:
                raise forms.ValidationError(
                    "Players per team must be 6–9 when generating teams"
                )
        return cleaned


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


class RosterImportForm(forms.Form):
    """LG-00b roster-import form: single CSV file upload.

    ``clean_csv_file`` decodes the upload as UTF-8 (BOM tolerated via
    ``utf-8-sig``) and stores the decoded text on
    ``cleaned_data["csv_file"]`` so the view consumes a ``str``, not an
    ``UploadedFile``. Row-cap enforcement is the pure parser's job; this
    form only enforces the defensive byte ceiling.
    """

    MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB — comfortably > 1000 rows.

    csv_file = forms.FileField(
        label="Roster CSV",
        widget=forms.ClearableFileInput(
            attrs={
                "id": "roster-import-file",
                "accept": ".csv,text/csv",
                "class": "form-control",
            }
        ),
    )

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        if uploaded.size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"CSV file is too large (max {self.MAX_UPLOAD_BYTES} bytes)"
            )
        raw = uploaded.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError("CSV file must be UTF-8 encoded") from exc
        return text
