"""ANG-01b — strict query-param Form for the HX-01 / HX-02 API actions.

Bound as ``BenchmarkParamsForm(data=plain_form_data(BenchmarkParamsForm,
request.query_params))`` then ``validate_form_or_raise(form)``. Unlike the
lenient HTML ``_coerce_threshold`` / ``_coerce_display`` parsing, a bad value is
a 400 (a negative threshold is ``min_value``; the HTML page clamps it to 0).
Defaults are applied in ``clean_<field>`` so ``cleaned_data`` is complete.
"""

from __future__ import annotations

from django import forms

DEFAULT_THRESHOLD = 5
DEFAULT_DISPLAY = "mean"


class BenchmarkParamsForm(forms.Form):
    """``?threshold=<int>&display=mean|median`` for career + benchmarks."""

    threshold = forms.IntegerField(min_value=0, required=False)
    display = forms.ChoiceField(
        choices=[("mean", "mean"), ("median", "median")], required=False
    )

    def clean_threshold(self) -> int:
        value = self.cleaned_data.get("threshold")
        return DEFAULT_THRESHOLD if value is None else value

    def clean_display(self) -> str:
        return self.cleaned_data.get("display") or DEFAULT_DISPLAY
