"""ANG-01b — strict query-param Forms for the read-only Match / Round API actions.

Each is bound as ``Form(data=plain_form_data(Form, request.query_params))`` then
``validate_form_or_raise(form)``. They take **ints**; the views resolve rows via
``get_owned_or_404`` *after* validation, so the same id twice is a 400 even when
the id is unknown. Defaults are applied in ``clean_<field>`` so ``cleaned_data``
is always complete. Unlike the lenient HTML parsing, bad values are 400s
(ADR-0039 §3).
"""

from __future__ import annotations

from django import forms

from matches.league_services import STANDINGS_SORT_KEYS

_PROVENANCE_CHOICES = [("all", "all"), ("real", "real"), ("sim", "sim")]
_ROLE_CHOICES = [
    ("commander", "commander"),
    ("heavy", "heavy"),
    ("scout", "scout"),
    ("medic", "medic"),
    ("ammo", "ammo"),
]


def _add_date_range(form_cls: type[forms.Form]) -> type[forms.Form]:
    """Add the optional ``from`` / ``to`` ``YYYY-MM-DD`` fields.

    ``from`` is a Python keyword, so the fields cannot be declared in the class
    body; they are injected into ``base_fields`` after the class is defined.
    """
    for name in ("from", "to"):
        form_cls.base_fields[name] = forms.DateField(
            required=False, input_formats=["%Y-%m-%d"]
        )
    return form_cls


class CompareRoundsParamsForm(forms.Form):
    """RV-01 — ``?round_a=&round_b=``."""

    round_a = forms.IntegerField(min_value=1)
    round_b = forms.IntegerField(min_value=1)

    def clean(self) -> dict:
        cleaned = super().clean()
        a = cleaned.get("round_a")
        b = cleaned.get("round_b")
        if a is not None and b is not None and a == b:
            raise forms.ValidationError(
                "Pick two different rounds to compare.", code="invalid"
            )
        return cleaned


class TeamH2HParamsForm(forms.Form):
    """HX-03 — ``?team_a=&team_b=&provenance=&from=&to=``."""

    team_a = forms.IntegerField(min_value=1)
    team_b = forms.IntegerField(min_value=1)
    provenance = forms.ChoiceField(choices=_PROVENANCE_CHOICES, required=False)

    def clean_provenance(self) -> str:
        return self.cleaned_data.get("provenance") or "all"

    def clean(self) -> dict:
        cleaned = super().clean()
        a = cleaned.get("team_a")
        b = cleaned.get("team_b")
        if a is not None and b is not None and a == b:
            raise forms.ValidationError(
                "Pick two different teams to compare.", code="invalid"
            )
        return cleaned


class PlayerH2HParamsForm(forms.Form):
    """HX-04 — ``?player_a=&player_b=&role=&provenance=&from=&to=``."""

    player_a = forms.IntegerField(min_value=1)
    player_b = forms.IntegerField(min_value=1)
    role = forms.ChoiceField(choices=_ROLE_CHOICES, required=False)
    provenance = forms.ChoiceField(choices=_PROVENANCE_CHOICES, required=False)

    def clean_role(self) -> str | None:
        return self.cleaned_data.get("role") or None

    def clean_provenance(self) -> str:
        return self.cleaned_data.get("provenance") or "all"

    def clean(self) -> dict:
        cleaned = super().clean()
        a = cleaned.get("player_a")
        b = cleaned.get("player_b")
        if a is not None and b is not None and a == b:
            raise forms.ValidationError(
                "Pick two different players to compare.", code="invalid"
            )
        return cleaned


_add_date_range(TeamH2HParamsForm)
_add_date_range(PlayerH2HParamsForm)


class StandingsParamsForm(forms.Form):
    """ANG-01d-1 — ``?sort=&dir=`` for the Season standings endpoint.

    Defaults ``rank`` / ``asc`` are applied in ``clean_<field>``; a bad value is
    a 400 ``invalid_choice`` (unlike the forgiving HTML coercion).
    """

    sort = forms.ChoiceField(
        choices=[(k, k) for k in sorted(STANDINGS_SORT_KEYS)], required=False
    )
    dir = forms.ChoiceField(choices=[("asc", "asc"), ("desc", "desc")], required=False)

    def clean_sort(self) -> str:
        return self.cleaned_data.get("sort") or "rank"

    def clean_dir(self) -> str:
        return self.cleaned_data.get("dir") or "asc"
