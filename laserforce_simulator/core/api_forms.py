"""ANG-01c — strict query-param Forms for the read-only map API actions.

Each is bound as ``Form(data=plain_form_data(Form, request.query_params))`` then
``validate_form_or_raise(form)``. Unlike the lenient HTML GETs (default 50,
clamp to ``[10, 200]``), ``zone_size`` is required and out-of-range is a 400
(ADR-0039 §3). Codes are Django's own (``required`` / ``invalid`` /
``min_value`` / ``max_value`` / ``invalid_choice``).
"""

from __future__ import annotations

from django import forms

_TEAM_COLOR_CHOICES = [("red", "red"), ("blue", "blue")]


class ZoneSizeParamsForm(forms.Form):
    """``?zone_size=``."""

    zone_size = forms.IntegerField(min_value=10, max_value=200)


class SingleCellParamsForm(forms.Form):
    """``?zone_size=&r=&c=``."""

    zone_size = forms.IntegerField(min_value=10, max_value=200)
    r = forms.IntegerField(min_value=0)
    c = forms.IntegerField(min_value=0)


class HeatmapParamsForm(forms.Form):
    """``?zone_size=&team_color=``."""

    zone_size = forms.IntegerField(min_value=10, max_value=200)
    team_color = forms.ChoiceField(choices=_TEAM_COLOR_CHOICES, required=False)

    def clean_team_color(self) -> str | None:
        return self.cleaned_data.get("team_color") or None
