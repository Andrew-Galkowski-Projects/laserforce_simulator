"""ANG-01a — the error half of the REST envelope (ADR-0039).

Every 4xx raised through DRF leaves the API as
``{"errors": [{"code", "message", "field"}, ...], "meta": {}}``. Views never
hand-build a 4xx ``Response``: they raise — ``validate_form_or_raise(form)``
for a bound Django ``Form``, ``EnvelopeValidationError`` for ``full_clean`` /
roster-import failures, or any DRF ``APIException`` — and
``envelope_exception_handler`` does the shaping.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypedDict

from django.core.exceptions import NON_FIELD_ERRORS
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.forms import BaseForm
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response
from rest_framework.settings import api_settings


class ErrorEntry(TypedDict):
    code: str
    message: str
    field: str | None


class EnvelopeValidationError(exceptions.APIException):
    """A 400 whose envelope ``errors`` list is supplied verbatim by the caller."""

    status_code = 400
    default_code = "invalid"
    default_detail = "Invalid input."

    def __init__(self, errors: list[ErrorEntry]) -> None:
        super().__init__()
        self.errors = errors


# Dict keys that mean "not attached to one field" in DRF / Django error trees.
_NON_FIELD_KEYS: frozenset[str] = frozenset(
    {api_settings.NON_FIELD_ERRORS_KEY, NON_FIELD_ERRORS}
)


def _join(prefix: str | None, key: object) -> str:
    return f"{prefix}.{key}" if prefix else str(key)


def _flatten(
    detail: object, field: str | None, default_code: str
) -> Iterator[ErrorEntry]:
    if isinstance(detail, dict):
        for key, value in detail.items():
            child = field if key in _NON_FIELD_KEYS else _join(field, key)
            yield from _flatten(value, child, default_code)
    elif isinstance(detail, (list, tuple)):
        for idx, item in enumerate(detail):
            if isinstance(item, dict):
                # A nested serializer's per-item errors: index joins the field
                # path (``"a.0"``); at top level the item's own keys name it.
                item_field = _join(field, idx) if field is not None else None
                yield from _flatten(item, item_field, default_code)
            else:
                yield from _flatten(item, field, default_code)
    else:
        yield {
            "code": getattr(detail, "code", None) or default_code,
            "message": str(detail),
            "field": field,
        }


def errors_from_detail(
    detail: object, *, default_code: str = "invalid"
) -> list[ErrorEntry]:
    """Flatten a DRF ``ErrorDetail`` tree (str / list / dict) into envelope entries."""
    return list(_flatten(detail, None, default_code))


def envelope_exception_handler(exc: Exception, context: dict) -> Response | None:
    """DRF ``EXCEPTION_HANDLER``: DRF's default handling, then the envelope.

    Returns ``None`` (Django's un-enveloped 500) for anything DRF itself would
    not handle. Status code and every header DRF set (``Allow``,
    ``WWW-Authenticate``, ``Retry-After``) are preserved.
    """
    # Deferred: importing rest_framework.views at module load loads DRF's
    # DEFAULT_RENDERER_CLASSES, whose EnvelopeJSONRenderer imports this module
    # back -- a circular import whenever this module is imported first.
    from rest_framework.views import exception_handler

    response = exception_handler(exc, context)
    if response is None:
        return None

    # Mirror DRF's own Http404 / PermissionDenied translation so the code and
    # message come from the APIException DRF actually responded with.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound(*exc.args)
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied(*exc.args)

    if isinstance(exc, EnvelopeValidationError):
        errors = exc.errors
    elif isinstance(exc, exceptions.ValidationError):
        errors = errors_from_detail(exc.detail)
    elif isinstance(exc, exceptions.APIException):
        detail = exc.detail
        if isinstance(detail, (dict, list)):
            errors = errors_from_detail(detail, default_code=exc.default_code)
        else:
            errors = [
                {
                    "code": getattr(detail, "code", None) or exc.default_code,
                    "message": str(detail),
                    "field": None,
                }
            ]
    else:  # pragma: no cover - DRF only answers for APIException subclasses
        errors = errors_from_detail(response.data)

    if not errors:
        # The envelope guarantees a non-empty ``errors`` list on every 4xx.
        errors = [
            {
                "code": getattr(exc, "default_code", "error"),
                "message": str(getattr(exc, "default_detail", "Error.")),
                "field": None,
            }
        ]

    response.data = {"errors": errors, "meta": {}}
    return response


def _entries_for(
    field: str | None, errors: list[DjangoValidationError]
) -> Iterator[ErrorEntry]:
    out_field = None if field == NON_FIELD_ERRORS else field
    for error in errors:
        for message in error.messages:
            yield {
                "code": error.code or "invalid",
                "message": message,
                "field": out_field,
            }


def form_errors_to_envelope(form: BaseForm) -> list[ErrorEntry]:
    """``form.errors`` as envelope entries — one per message, in form order.

    Messages are the same rendered strings ``form.errors`` shows, so the HTML
    and API surfaces report byte-identical text.
    """
    entries: list[ErrorEntry] = []
    for field, errors in form.errors.as_data().items():
        entries.extend(_entries_for(field, errors))
    return entries


def django_validation_error_to_envelope(
    exc: DjangoValidationError,
) -> list[ErrorEntry]:
    """A ``Model.full_clean()`` failure as envelope entries."""
    if hasattr(exc, "error_dict"):
        entries: list[ErrorEntry] = []
        for field, errors in exc.error_dict.items():
            entries.extend(_entries_for(field, errors))
        return entries
    return list(_entries_for(None, exc.error_list))


def validate_form_or_raise(form: BaseForm) -> None:
    """Raise ``EnvelopeValidationError`` (400) unless ``form`` is valid."""
    if not form.is_valid():
        raise EnvelopeValidationError(form_errors_to_envelope(form))
