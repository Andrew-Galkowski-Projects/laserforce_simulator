"""ANG-01a — normalise ``request.data`` for binding an existing Django Form.

API writes bind the same ``Form`` the HTML view binds (ADR-0039). These helpers
turn a JSON ``dict`` or a ``QueryDict`` into the plain ``dict`` a Form accepts,
restricted to the Form's own fields (unknown keys are dropped silently).
"""

from __future__ import annotations

from collections.abc import Mapping

from django import forms
from django.db.models import Model
from django.forms.models import model_to_dict
from rest_framework.exceptions import ParseError

# Fields whose QueryDict value is the full list, not the last item.
_MULTI_VALUE_FIELDS: tuple[type[forms.Field], ...] = (
    forms.MultipleChoiceField,
    forms.ModelMultipleChoiceField,
)


def plain_form_data(
    form_class: type[forms.BaseForm], data: Mapping
) -> dict[str, object]:
    """``data`` as a plain dict holding only keys in ``form_class.base_fields``.

    A body that is not a mapping (e.g. a JSON array) is a 400 ``parse_error``.
    """
    if not hasattr(data, "items"):
        raise ParseError("Expected a JSON object.")
    fields = form_class.base_fields
    if hasattr(data, "getlist"):
        out: dict[str, object] = {}
        for key in data.keys():
            field = fields.get(key)
            if field is None:
                continue
            if isinstance(field, _MULTI_VALUE_FIELDS):
                out[key] = data.getlist(key)
            else:
                out[key] = data[key]
        return out
    return {key: value for key, value in data.items() if key in fields}


def merged_form_data(
    form_class: type[forms.ModelForm], instance: Model, data: Mapping
) -> dict[str, object]:
    """PATCH semantics: the instance's current Form values overlaid with ``data``."""
    return {
        **model_to_dict(instance, fields=list(form_class.base_fields)),
        **plain_form_data(form_class, data),
    }
