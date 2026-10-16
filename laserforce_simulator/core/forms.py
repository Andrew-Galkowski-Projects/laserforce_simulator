"""ANG-01c — strict write Forms for the map editor, shared by HTML and API.

The API binds these directly (``Form(data=plain_form_data(Form, request.data))``).
The HTML AJAX views keep their lenient parse first (``json.loads``, ``int``
zone_size, the ``[10, 200]`` clamp, defaults) and only then bind the Form with
the already-normalised values, so valid editor payloads produce the same writes
and byte-identical success bodies.

Every JSON-valued field is a ``ParsedJSONField``: the value is ALREADY decoded
JSON, so a ``str`` stays a ``str`` (never ``json.loads``'d) and ``[]`` / ``{}``
are real values, not "empty" (``red_spawn: []`` means "explicitly erased").
"""

from __future__ import annotations

import re
from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from .models import VALID_BASE_TYPES

_CELL_KEY_RE = re.compile(r"[0-9]+,[0-9]+")
_FACINGS: frozenset[str] = frozenset({"N", "S", "E", "W"})
_WALL_META_KEYS: frozenset[str] = frozenset({"facing", "height"})
_ZONE_VALUES: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5})
_BASE_KEYS: frozenset[str] = frozenset({"type", "x_px", "y_px"})

ZONES_MESSAGE = (
    "zones must be a non-empty list of equal-length rows of cell values 0-5."
)
WALL_META_MESSAGE = (
    'wall_meta must map "r,c" keys to {"facing": N/S/E/W, "height": number >= 0}.'
)
ELEVATION_ROWS_MESSAGE = "Invalid elevation: must be a list of rows"
ELEVATION_ROW_MESSAGE = "Invalid elevation: each row must be a list"
BASES_MESSAGE = (
    'bases must be a list of {"type", "x_px", "y_px"} objects with a valid base '
    "type and integer pixel coordinates."
)
BASES_REPEAT_MESSAGE = "bases must not repeat a base type."
SIGHT_DATA_MESSAGE = 'sight_data must map "r,c" keys to lists of "r,c" strings.'
BASE_SIGHTS_MESSAGE = "base_sights must map base types to lists of [int, int] pairs."
CELLS_MESSAGE = "cells must be a list of [int, int] pairs"


class ParsedJSONField(forms.JSONField):
    """A value that is ALREADY decoded JSON (DRF ``JSONParser`` / ``json.loads``).

    ``to_python`` returns the value unchanged and ``empty_values = [None]`` so
    ``[]`` and ``{}`` survive cleaning.
    """

    empty_values = [None]

    def to_python(self, value: Any) -> Any:
        return value

    def bound_data(self, data: Any, initial: Any) -> Any:
        if self.disabled:
            return initial
        return data


# ---------------------------------------------------------------------------
# Value predicates and validators
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_cell_key(value: Any) -> bool:
    return isinstance(value, str) and _CELL_KEY_RE.fullmatch(value) is not None


def _is_int_pair(value: Any, *, allow_tuple: bool = False) -> bool:
    if isinstance(value, list) or (allow_tuple and isinstance(value, tuple)):
        return len(value) == 2 and all(_is_int(v) for v in value)
    return False


def _invalid(message: str) -> ValidationError:
    return ValidationError(message, code="invalid")


def _zone_size_field() -> forms.IntegerField:
    return forms.IntegerField(min_value=10, max_value=200)


def clean_zones_value(value: Any) -> list | None:
    """``None`` / ``[]`` ⇒ ``None`` (auto-detect); else a rectangular 0-5 grid."""
    if value is None or (isinstance(value, list) and not value):
        return None
    if not isinstance(value, list):
        raise _invalid(ZONES_MESSAGE)
    width: int | None = None
    for row in value:
        if not isinstance(row, list) or not row:
            raise _invalid(ZONES_MESSAGE)
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise _invalid(ZONES_MESSAGE)
        for cell in row:
            if not _is_int(cell) or cell not in _ZONE_VALUES:
                raise _invalid(ZONES_MESSAGE)
    return value


def clean_wall_meta_value(value: Any) -> dict:
    """``None`` ⇒ ``{}``; else ``{"r,c": {"facing"?, "height"?}}``."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid(WALL_META_MESSAGE)
    for key, meta in value.items():
        if not _is_cell_key(key) or not isinstance(meta, dict):
            raise _invalid(WALL_META_MESSAGE)
        if not set(meta) <= _WALL_META_KEYS:
            raise _invalid(WALL_META_MESSAGE)
        if "facing" in meta and meta["facing"] not in _FACINGS:
            raise _invalid(WALL_META_MESSAGE)
        if "height" in meta:
            height = meta["height"]
            if not _is_number(height) or not height >= 0:
                raise _invalid(WALL_META_MESSAGE)
    return value


def clean_elevation_value(value: Any) -> list | None:
    """``None`` ⇒ ``None`` (carry forward); else rows of numbers in [0.0, 10.0]."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise _invalid(ELEVATION_ROWS_MESSAGE)
    for row in value:
        if not isinstance(row, list):
            raise _invalid(ELEVATION_ROW_MESSAGE)
        for val in row:
            if not _is_number(val) or not (0.0 <= float(val) <= 10.0):
                raise _invalid(f"Elevation value out of range [0.0, 10.0]: {val}")
    return value


def clean_spawn_value(value: Any, field: str) -> list | None:
    """``None`` ⇒ ``None`` ("not sent"); ``[]`` kept; else ``[int, int]`` pairs."""
    if value is None:
        return None
    if not isinstance(value, list) or not all(_is_int_pair(rc) for rc in value):
        raise _invalid(f"{field} must be a list of [int, int] pairs")
    return value


def clean_bases_value(value: Any) -> list[dict]:
    """``None`` ⇒ ``[]``; else ``{"type", "x_px", "y_px"}`` dicts, no repeated type."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise _invalid(BASES_MESSAGE)
    for base in value:
        if (
            not isinstance(base, dict)
            or set(base) != _BASE_KEYS
            or base["type"] not in VALID_BASE_TYPES
            or not _is_int(base["x_px"])
            or not _is_int(base["y_px"])
        ):
            raise _invalid(BASES_MESSAGE)
    types = [base["type"] for base in value]
    if len(set(types)) != len(types):
        raise _invalid(BASES_REPEAT_MESSAGE)
    return value


def clean_sight_data_value(value: Any) -> dict:
    """``None`` ⇒ ``{}``; else ``{"r,c": ["r,c", ...]}``."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid(SIGHT_DATA_MESSAGE)
    for key, cells in value.items():
        if not _is_cell_key(key) or not isinstance(cells, list):
            raise _invalid(SIGHT_DATA_MESSAGE)
        if not all(_is_cell_key(cell) for cell in cells):
            raise _invalid(SIGHT_DATA_MESSAGE)
    return value


def clean_base_sights_value(value: Any) -> dict:
    """``None`` ⇒ ``{}``; else ``{base_type: [[int, int], ...]}``."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid(BASE_SIGHTS_MESSAGE)
    for btype, cells in value.items():
        if btype not in VALID_BASE_TYPES or not isinstance(cells, list):
            raise _invalid(BASE_SIGHTS_MESSAGE)
        if not all(_is_int_pair(rc) for rc in cells):
            raise _invalid(BASE_SIGHTS_MESSAGE)
    return value


def clean_cells_value(value: Any) -> list:
    """A list of ``[int, int]`` pairs (list or tuple)."""
    if not isinstance(value, list) or not all(
        _is_int_pair(rc, allow_tuple=True) for rc in value
    ):
        raise _invalid(CELLS_MESSAGE)
    return value


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


class MapUploadForm(forms.Form):
    """Upload a new ArenaMap (HTML ``upload_map`` + ``POST /api/maps/``)."""

    name = forms.CharField(max_length=100)
    image = forms.ImageField()


class ZoneConfigForm(forms.Form):
    """Save a zone config + bases (HTML ``save_zone_config`` + API zone-config PUT)."""

    zone_size = _zone_size_field()
    zones = ParsedJSONField(required=False)
    wall_meta = ParsedJSONField(required=False)
    elevation = ParsedJSONField(required=False)
    red_spawn = ParsedJSONField(required=False)
    blue_spawn = ParsedJSONField(required=False)
    bases = ParsedJSONField(required=False)

    def clean_zones(self) -> list | None:
        return clean_zones_value(self.cleaned_data.get("zones"))

    def clean_wall_meta(self) -> dict:
        return clean_wall_meta_value(self.cleaned_data.get("wall_meta"))

    def clean_elevation(self) -> list | None:
        return clean_elevation_value(self.cleaned_data.get("elevation"))

    def clean_red_spawn(self) -> list | None:
        return clean_spawn_value(self.cleaned_data.get("red_spawn"), "red_spawn")

    def clean_blue_spawn(self) -> list | None:
        return clean_spawn_value(self.cleaned_data.get("blue_spawn"), "blue_spawn")

    def clean_bases(self) -> list[dict]:
        return clean_bases_value(self.cleaned_data.get("bases"))


class ComputeSightLinesForm(forms.Form):
    """Run the all-pairs compute (HTML ``compute_sight_lines_view`` + API compute)."""

    zone_size = _zone_size_field()
    red_spawn = ParsedJSONField(required=False)
    blue_spawn = ParsedJSONField(required=False)

    def clean_red_spawn(self) -> list | None:
        return clean_spawn_value(self.cleaned_data.get("red_spawn"), "red_spawn")

    def clean_blue_spawn(self) -> list | None:
        return clean_spawn_value(self.cleaned_data.get("blue_spawn"), "blue_spawn")


class SightLinesSaveForm(forms.Form):
    """Save a (batch of) sight lines (HTML ``save_sight_lines`` + API PUT / PATCH)."""

    zone_size = _zone_size_field()
    sight_data = ParsedJSONField(required=False)
    base_sights = ParsedJSONField(required=False)

    def clean_sight_data(self) -> dict:
        return clean_sight_data_value(self.cleaned_data.get("sight_data"))

    def clean_base_sights(self) -> dict:
        return clean_base_sights_value(self.cleaned_data.get("base_sights"))


class StrongSpotsForm(forms.Form):
    """Save Heavy strong spots (HTML ``save_strong_spots`` + API strong-spots PUT)."""

    zone_size = _zone_size_field()
    cells = ParsedJSONField(required=True)

    def clean_cells(self) -> list:
        return clean_cells_value(self.cleaned_data.get("cells"))
