"""ANG-01g — the params Form bound by the Accounts API Self-insert ``PUT``.

Bound as ``Form(data=plain_form_data(Form, request.data))`` then
``validate_form_or_raise(form)`` (the ``core/api_forms.py`` idiom). Codes are
Django's own (``required`` / ``invalid``) on ``field: "player"``.
"""

from __future__ import annotations

from django import forms


class SelfInsertParamsForm(forms.Form):
    """``{"player": <id>}``.

    An integer or an integer string (``"5"``) is accepted, as on the HTML
    route. A missing key is ``required``; JSON ``null``, a bool, a float with a
    fraction, a list or a non-numeric string is ``invalid``. The field is
    ``required=False`` only so that ``clean_player`` -- not the field -- decides
    between ``required`` (key absent) and ``invalid`` (key present but null).
    The raw-bool check is defensive: ``IntegerField`` already fails on
    ``int("True")``, but a JSON bool must never be read as ``1`` / ``0``.
    """

    player = forms.IntegerField(required=False)

    def clean_player(self) -> int:
        field = self.fields["player"]
        if "player" not in self.data:
            raise forms.ValidationError(
                field.error_messages["required"], code="required"
            )
        value = self.cleaned_data["player"]
        if value is None or isinstance(self.data["player"], bool):
            raise forms.ValidationError(field.error_messages["invalid"], code="invalid")
        return value
