"""ANG-01c -- the map write Forms (``core/forms.py``) and the API read-param
Forms (``core/api_forms.py``). Seam contract sections 2 and 5.

Pure Form validation: ``SimpleTestCase``, no database. Every message asserted
here is pinned by the contract (several are byte-identical to the pre-ANG-01c
HTML views).
"""

from __future__ import annotations

from django.test import SimpleTestCase

from core.api_forms import HeatmapParamsForm, SingleCellParamsForm, ZoneSizeParamsForm
from core.forms import (
    ComputeSightLinesForm,
    MapUploadForm,
    ParsedJSONField,
    SightLinesSaveForm,
    StrongSpotsForm,
    ZoneConfigForm,
)
from core.map_test_fixtures import (
    BLUE_BASE,
    GRID,
    RED_BASE,
    SIGHT,
    corrupt_upload,
    png_upload,
)
from core.models import VALID_BASE_TYPES

ZONES_MSG = "zones must be a non-empty list of equal-length rows of cell values 0-5."
WALL_META_MSG = (
    'wall_meta must map "r,c" keys to {"facing": N/S/E/W, "height": number >= 0}.'
)
ELEV_A = "Invalid elevation: must be a list of rows"
ELEV_B = "Invalid elevation: each row must be a list"
BASES_MSG = (
    'bases must be a list of {"type", "x_px", "y_px"} objects with a valid base '
    "type and integer pixel coordinates."
)
BASES_REPEAT_MSG = "bases must not repeat a base type."
SIGHT_DATA_MSG = 'sight_data must map "r,c" keys to lists of "r,c" strings.'
BASE_SIGHTS_MSG = "base_sights must map base types to lists of [int, int] pairs."
CELLS_MSG = "cells must be a list of [int, int] pairs"


def spawn_msg(field: str) -> str:
    return f"{field} must be a list of [int, int] pairs"


def elev_c(val: object) -> str:
    return f"Elevation value out of range [0.0, 10.0]: {val}"


class _FormAsserts(SimpleTestCase):
    def errors_for(self, form, field: str) -> list[tuple[str, str]]:
        """``[(code, message), ...]`` for ``field`` on a bound, validated form."""
        self.assertFalse(form.is_valid(), f"{field} unexpectedly valid")
        data = form.errors.as_data()
        self.assertIn(field, data, form.errors)
        out = []
        for err in data[field]:
            for message in err.messages:
                out.append((err.code, message))
        return out

    def assert_field_error(
        self, form, field: str, code: str, message: str | None = None
    ) -> None:
        errors = self.errors_for(form, field)
        self.assertEqual(errors[0][0], code, errors)
        if message is not None:
            self.assertEqual(errors[0][1], message)

    def assert_valid(self, form) -> dict:
        self.assertTrue(form.is_valid(), form.errors)
        return form.cleaned_data


# ---------------------------------------------------------------------------
# ParsedJSONField
# ---------------------------------------------------------------------------


class TestParsedJSONField(SimpleTestCase):
    def test_string_is_not_json_decoded(self) -> None:
        self.assertEqual(ParsedJSONField(required=False).clean("[1]"), "[1]")

    def test_empty_list_and_dict_survive(self) -> None:
        field = ParsedJSONField(required=False)
        self.assertEqual(field.clean([]), [])
        self.assertEqual(field.clean({}), {})

    def test_none_is_empty(self) -> None:
        self.assertIsNone(ParsedJSONField(required=False).clean(None))

    def test_decoded_values_pass_through_unchanged(self) -> None:
        value = {"a": [1, 2, {"b": None}]}
        self.assertEqual(ParsedJSONField(required=False).clean(value), value)


# ---------------------------------------------------------------------------
# MapUploadForm
# ---------------------------------------------------------------------------


class TestMapUploadForm(_FormAsserts):
    def bind(self, name: object = "Arena", image: object = "png") -> MapUploadForm:
        data = {} if name is None else {"name": name}
        files = {}
        if image == "png":
            files["image"] = png_upload()
        elif image is not None:
            files["image"] = image
        return MapUploadForm(data=data, files=files)

    def test_valid_upload(self) -> None:
        cleaned = self.assert_valid(self.bind())
        self.assertEqual(cleaned["name"], "Arena")
        self.assertIsNotNone(cleaned["image"])

    def test_name_is_stripped(self) -> None:
        self.assertEqual(
            self.assert_valid(self.bind(name="  Arena  "))["name"], "Arena"
        )

    def test_missing_name_required(self) -> None:
        self.assert_field_error(self.bind(name=None), "name", "required")

    def test_blank_name_required(self) -> None:
        self.assert_field_error(self.bind(name="   "), "name", "required")

    def test_name_over_100_is_max_length(self) -> None:
        self.assert_field_error(self.bind(name="x" * 101), "name", "max_length")

    def test_name_of_exactly_100_is_valid(self) -> None:
        self.assert_valid(self.bind(name="x" * 100))

    def test_missing_image_required(self) -> None:
        self.assert_field_error(self.bind(image=None), "image", "required")

    def test_corrupt_image_is_invalid_image(self) -> None:
        form = self.bind(image=corrupt_upload())
        self.assert_field_error(
            form,
            "image",
            "invalid_image",
            MapUploadForm.base_fields["image"].error_messages["invalid_image"],
        )


# ---------------------------------------------------------------------------
# zone_size (shared by the four write Forms)
# ---------------------------------------------------------------------------


class TestZoneSizeOnWriteForms(_FormAsserts):
    FORMS = (ZoneConfigForm, ComputeSightLinesForm, SightLinesSaveForm, StrongSpotsForm)

    def extra(self, form_class) -> dict:
        return {"cells": []} if form_class is StrongSpotsForm else {}

    def test_required(self) -> None:
        for form_class in self.FORMS:
            with self.subTest(form=form_class.__name__):
                form = form_class(data=self.extra(form_class))
                self.assert_field_error(form, "zone_size", "required")

    def test_null_is_required(self) -> None:
        for form_class in self.FORMS:
            with self.subTest(form=form_class.__name__):
                form = form_class(data={"zone_size": None, **self.extra(form_class)})
                self.assert_field_error(form, "zone_size", "required")

    def test_bounds_and_type(self) -> None:
        for form_class in self.FORMS:
            for value, code in (
                (9, "min_value"),
                (201, "max_value"),
                ("abc", "invalid"),
            ):
                with self.subTest(form=form_class.__name__, value=value):
                    form = form_class(
                        data={"zone_size": value, **self.extra(form_class)}
                    )
                    self.assert_field_error(form, "zone_size", code)

    def test_bounds_inclusive(self) -> None:
        for form_class in self.FORMS:
            for value in (10, 200):
                with self.subTest(form=form_class.__name__, value=value):
                    form = form_class(
                        data={"zone_size": value, **self.extra(form_class)}
                    )
                    self.assertEqual(self.assert_valid(form)["zone_size"], value)


# ---------------------------------------------------------------------------
# ZoneConfigForm
# ---------------------------------------------------------------------------


class TestZoneConfigForm(_FormAsserts):
    def bind(self, **fields) -> ZoneConfigForm:
        return ZoneConfigForm(data={"zone_size": 50, **fields})

    # --- happy path / defaults -------------------------------------------

    def test_full_valid_payload(self) -> None:
        payload = {
            "zones": GRID,
            "wall_meta": {"0,1": {"facing": "N", "height": 1.5}, "3,3": {}},
            "elevation": [[0, 0.5, 10.0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0] * 4],
            "red_spawn": [[1, 1]],
            "blue_spawn": [[2, 2], [2, 1]],
            "bases": [RED_BASE, BLUE_BASE],
        }
        cleaned = self.assert_valid(self.bind(**payload))
        self.assertEqual(cleaned["zone_size"], 50)
        for key, value in payload.items():
            self.assertEqual(cleaned[key], value, key)

    def test_absent_optional_fields_take_defaults(self) -> None:
        cleaned = self.assert_valid(self.bind())
        self.assertIsNone(cleaned["zones"])
        self.assertEqual(cleaned["wall_meta"], {})
        self.assertIsNone(cleaned["elevation"])
        self.assertIsNone(cleaned["red_spawn"])
        self.assertIsNone(cleaned["blue_spawn"])
        self.assertEqual(cleaned["bases"], [])

    def test_null_optional_fields_take_defaults(self) -> None:
        cleaned = self.assert_valid(
            self.bind(
                zones=None,
                wall_meta=None,
                elevation=None,
                red_spawn=None,
                blue_spawn=None,
                bases=None,
            )
        )
        self.assertIsNone(cleaned["zones"])
        self.assertEqual(cleaned["wall_meta"], {})
        self.assertIsNone(cleaned["elevation"])
        self.assertIsNone(cleaned["red_spawn"])
        self.assertIsNone(cleaned["blue_spawn"])
        self.assertEqual(cleaned["bases"], [])

    def test_cleaned_keys_are_the_save_service_kwargs(self) -> None:
        cleaned = self.assert_valid(self.bind())
        self.assertEqual(
            set(cleaned),
            {
                "zone_size",
                "zones",
                "wall_meta",
                "elevation",
                "red_spawn",
                "blue_spawn",
                "bases",
            },
        )

    # --- zones -----------------------------------------------------------

    def test_empty_zones_means_auto_detect(self) -> None:
        self.assertIsNone(self.assert_valid(self.bind(zones=[]))["zones"])

    def test_every_cell_value_0_to_5_accepted(self) -> None:
        zones = [[0, 1, 2], [3, 4, 5]]
        self.assertEqual(self.assert_valid(self.bind(zones=zones))["zones"], zones)

    def test_zones_rejected(self) -> None:
        cases = {
            "ragged": [[0, 1], [1]],
            "out_of_set": [[0, 6]],
            "negative": [[-1, 1]],
            "bool": [[True, 1]],
            "float": [[1.0, 1]],
            "string_cell": [["1", 1]],
            "empty_row": [[]],
            "row_not_list": [1, 2],
            "not_list": "GRID",
            "dict": {"0": [1]},
        }
        for label, zones in cases.items():
            with self.subTest(label):
                self.assert_field_error(
                    self.bind(zones=zones), "zones", "invalid", ZONES_MSG
                )

    # --- wall_meta -------------------------------------------------------

    def test_wall_meta_accepts_facing_and_height(self) -> None:
        wall_meta = {
            "1,2": {"facing": "S"},
            "10,20": {"height": 0},
            "0,0": {"facing": "W", "height": 2},
        }
        cleaned = self.assert_valid(self.bind(wall_meta=wall_meta))
        self.assertEqual(cleaned["wall_meta"], wall_meta)

    def test_wall_meta_rejected(self) -> None:
        cases = {
            "bad_key": {"1-2": {"facing": "N"}},
            "key_not_numeric": {"a,b": {"facing": "N"}},
            "bad_facing": {"1,2": {"facing": "X"}},
            "lowercase_facing": {"1,2": {"facing": "n"}},
            "negative_height": {"1,2": {"height": -0.5}},
            "bool_height": {"1,2": {"height": True}},
            "string_height": {"1,2": {"height": "1"}},
            "extra_key": {"1,2": {"facing": "N", "color": "red"}},
            "value_not_dict": {"1,2": "N"},
            "not_dict": [["1,2"]],
        }
        for label, wall_meta in cases.items():
            with self.subTest(label):
                self.assert_field_error(
                    self.bind(wall_meta=wall_meta),
                    "wall_meta",
                    "invalid",
                    WALL_META_MSG,
                )

    # --- elevation -------------------------------------------------------

    def test_elevation_bounds_inclusive(self) -> None:
        elevation = [[0, 0.0], [10, 10.0]]
        self.assertEqual(
            self.assert_valid(self.bind(elevation=elevation))["elevation"], elevation
        )

    def test_elevation_not_a_list_is_message_a(self) -> None:
        for value in ("flat", {"0": [0]}, 3):
            with self.subTest(value=value):
                self.assert_field_error(
                    self.bind(elevation=value), "elevation", "invalid", ELEV_A
                )

    def test_elevation_row_not_a_list_is_message_b(self) -> None:
        self.assert_field_error(
            self.bind(elevation=[[0, 0], 5]), "elevation", "invalid", ELEV_B
        )

    def test_elevation_out_of_range_is_message_c_with_value(self) -> None:
        self.assert_field_error(
            self.bind(elevation=[[0, 11.5]]), "elevation", "invalid", elev_c(11.5)
        )
        self.assert_field_error(
            self.bind(elevation=[[-1]]), "elevation", "invalid", elev_c(-1)
        )

    def test_elevation_reports_first_offending_value(self) -> None:
        self.assert_field_error(
            self.bind(elevation=[[0, 12], [13]]), "elevation", "invalid", elev_c(12)
        )

    def test_elevation_non_number_is_message_c(self) -> None:
        self.assert_field_error(
            self.bind(elevation=[["x"]]), "elevation", "invalid", elev_c("x")
        )

    def test_elevation_bool_is_message_c(self) -> None:
        self.assert_field_error(
            self.bind(elevation=[[True]]), "elevation", "invalid", elev_c(True)
        )

    # --- spawns ----------------------------------------------------------

    def test_empty_spawn_kept_as_empty_list(self) -> None:
        cleaned = self.assert_valid(self.bind(red_spawn=[], blue_spawn=[]))
        self.assertEqual(cleaned["red_spawn"], [])
        self.assertEqual(cleaned["blue_spawn"], [])

    def test_one_side_sent_other_absent(self) -> None:
        cleaned = self.assert_valid(self.bind(red_spawn=[]))
        self.assertEqual(cleaned["red_spawn"], [])
        self.assertIsNone(cleaned["blue_spawn"])

    def test_spawn_rejected(self) -> None:
        cases = {
            "short_pair": [[1]],
            "long_pair": [[1, 2, 3]],
            "bool": [[1, True]],
            "float": [[1.0, 2]],
            "string": [["1", "2"]],
            "pair_not_list": ["1,2"],
            "not_list": "1,2",
        }
        for field in ("red_spawn", "blue_spawn"):
            for label, value in cases.items():
                with self.subTest(field=field, case=label):
                    self.assert_field_error(
                        self.bind(**{field: value}), field, "invalid", spawn_msg(field)
                    )

    # --- bases -----------------------------------------------------------

    def test_every_valid_base_type_accepted(self) -> None:
        bases = [
            {"type": btype, "x_px": i * 10, "y_px": i}
            for i, btype in enumerate(sorted(VALID_BASE_TYPES))
        ]
        self.assertEqual(self.assert_valid(self.bind(bases=bases))["bases"], bases)

    def test_empty_bases(self) -> None:
        self.assertEqual(self.assert_valid(self.bind(bases=[]))["bases"], [])

    def test_bases_rejected(self) -> None:
        cases = {
            "unknown_type": [{"type": "green", "x_px": 1, "y_px": 1}],
            "missing_key": [{"type": "red", "x_px": 1}],
            "extra_key": [{"type": "red", "x_px": 1, "y_px": 1, "z": 0}],
            "string_px": [{"type": "red", "x_px": "1", "y_px": 1}],
            "float_px": [{"type": "red", "x_px": 1.5, "y_px": 1}],
            "bool_px": [{"type": "red", "x_px": True, "y_px": 1}],
            "item_not_dict": [["red", 1, 1]],
            "not_list": {"type": "red", "x_px": 1, "y_px": 1},
        }
        for label, bases in cases.items():
            with self.subTest(label):
                self.assert_field_error(
                    self.bind(bases=bases), "bases", "invalid", BASES_MSG
                )

    def test_duplicate_base_type(self) -> None:
        bases = [RED_BASE, {"type": "red", "x_px": 5, "y_px": 5}]
        self.assert_field_error(
            self.bind(bases=bases), "bases", "invalid", BASES_REPEAT_MSG
        )

    # --- error ordering --------------------------------------------------

    def test_errors_follow_field_order(self) -> None:
        form = self.bind(zones="bad", bases="bad", red_spawn="bad")
        self.assertFalse(form.is_valid())
        self.assertEqual(list(form.errors), ["zones", "red_spawn", "bases"])


# ---------------------------------------------------------------------------
# ComputeSightLinesForm
# ---------------------------------------------------------------------------


class TestComputeSightLinesForm(_FormAsserts):
    def test_zone_size_only(self) -> None:
        cleaned = self.assert_valid(ComputeSightLinesForm(data={"zone_size": 50}))
        self.assertEqual(cleaned["zone_size"], 50)
        self.assertIsNone(cleaned["red_spawn"])
        self.assertIsNone(cleaned["blue_spawn"])

    def test_spawns_kept(self) -> None:
        cleaned = self.assert_valid(
            ComputeSightLinesForm(
                data={"zone_size": 50, "red_spawn": [[1, 1]], "blue_spawn": []}
            )
        )
        self.assertEqual(cleaned["red_spawn"], [[1, 1]])
        self.assertEqual(cleaned["blue_spawn"], [])

    def test_bad_spawn(self) -> None:
        form = ComputeSightLinesForm(data={"zone_size": 50, "blue_spawn": [[1, "x"]]})
        self.assert_field_error(form, "blue_spawn", "invalid", spawn_msg("blue_spawn"))


# ---------------------------------------------------------------------------
# SightLinesSaveForm
# ---------------------------------------------------------------------------


class TestSightLinesSaveForm(_FormAsserts):
    def bind(self, **fields) -> SightLinesSaveForm:
        return SightLinesSaveForm(data={"zone_size": 50, **fields})

    def test_valid_payload(self) -> None:
        base_sights = {btype: [[1, 1]] for btype in VALID_BASE_TYPES}
        cleaned = self.assert_valid(
            self.bind(sight_data=SIGHT, base_sights=base_sights)
        )
        self.assertEqual(cleaned["sight_data"], SIGHT)
        self.assertEqual(cleaned["base_sights"], base_sights)

    def test_defaults(self) -> None:
        cleaned = self.assert_valid(self.bind())
        self.assertEqual(cleaned["sight_data"], {})
        self.assertEqual(cleaned["base_sights"], {})

    def test_empty_values_kept(self) -> None:
        cleaned = self.assert_valid(
            self.bind(sight_data={"1,1": []}, base_sights={"red": []})
        )
        self.assertEqual(cleaned["sight_data"], {"1,1": []})
        self.assertEqual(cleaned["base_sights"], {"red": []})

    def test_sight_data_rejected(self) -> None:
        cases = {
            "bad_key": {"1-1": ["1,2"]},
            "bad_item": {"1,1": ["1;2"]},
            "int_item": {"1,1": [12]},
            "value_not_list": {"1,1": "1,2"},
            "not_dict": [["1,1", "1,2"]],
        }
        for label, sight_data in cases.items():
            with self.subTest(label):
                self.assert_field_error(
                    self.bind(sight_data=sight_data),
                    "sight_data",
                    "invalid",
                    SIGHT_DATA_MSG,
                )

    def test_base_sights_rejected(self) -> None:
        cases = {
            "unknown_type": {"green": [[1, 1]]},
            "bad_pair": {"red": [[1]]},
            "bool_pair": {"red": [[True, 1]]},
            "string_pair": {"red": ["1,1"]},
            "value_not_list": {"red": "1,1"},
            "not_dict": [["red", [1, 1]]],
        }
        for label, base_sights in cases.items():
            with self.subTest(label):
                self.assert_field_error(
                    self.bind(base_sights=base_sights),
                    "base_sights",
                    "invalid",
                    BASE_SIGHTS_MSG,
                )


# ---------------------------------------------------------------------------
# StrongSpotsForm
# ---------------------------------------------------------------------------


class TestStrongSpotsForm(_FormAsserts):
    def bind(self, **fields) -> StrongSpotsForm:
        return StrongSpotsForm(data={"zone_size": 50, **fields})

    def test_valid_cells(self) -> None:
        cleaned = self.assert_valid(self.bind(cells=[[1, 1], [2, 2]]))
        self.assertEqual(cleaned["cells"], [[1, 1], [2, 2]])

    def test_tuple_pairs_accepted(self) -> None:
        self.assert_valid(self.bind(cells=[(1, 1)]))

    def test_empty_list_is_valid(self) -> None:
        self.assertEqual(self.assert_valid(self.bind(cells=[]))["cells"], [])

    def test_absent_cells_required(self) -> None:
        self.assert_field_error(self.bind(), "cells", "required")

    def test_null_cells_required(self) -> None:
        self.assert_field_error(self.bind(cells=None), "cells", "required")

    def test_cells_rejected_with_pinned_message(self) -> None:
        cases = {
            "string": "not-a-list",
            "dict": {"1": 1},
            "short_pair": [[1]],
            "bool_pair": [[1, True]],
            "float_pair": [[1.0, 2]],
            "pair_not_list": ["1,1"],
        }
        for label, cells in cases.items():
            with self.subTest(label):
                self.assert_field_error(
                    self.bind(cells=cells), "cells", "invalid", CELLS_MSG
                )


# ---------------------------------------------------------------------------
# core/api_forms.py -- read params (query-string strings)
# ---------------------------------------------------------------------------


class TestZoneSizeParamsForm(_FormAsserts):
    def test_parses_string(self) -> None:
        cleaned = self.assert_valid(ZoneSizeParamsForm(data={"zone_size": "50"}))
        self.assertEqual(cleaned["zone_size"], 50)

    def test_codes(self) -> None:
        for data, code in (
            ({}, "required"),
            ({"zone_size": ""}, "required"),
            ({"zone_size": "abc"}, "invalid"),
            ({"zone_size": "9"}, "min_value"),
            ({"zone_size": "201"}, "max_value"),
        ):
            with self.subTest(data=data):
                self.assert_field_error(
                    ZoneSizeParamsForm(data=data), "zone_size", code
                )


class TestSingleCellParamsForm(_FormAsserts):
    def test_parses(self) -> None:
        cleaned = self.assert_valid(
            SingleCellParamsForm(data={"zone_size": "50", "r": "0", "c": "3"})
        )
        self.assertEqual((cleaned["zone_size"], cleaned["r"], cleaned["c"]), (50, 0, 3))

    def test_large_coordinates_are_valid(self) -> None:
        """No grid-bounds check in the Form -- off-grid is the service's ``[]``."""
        self.assert_valid(
            SingleCellParamsForm(data={"zone_size": "50", "r": "99", "c": "99"})
        )

    def test_r_and_c_codes(self) -> None:
        for field in ("r", "c"):
            other = "c" if field == "r" else "r"
            for value, code in (
                (None, "required"),
                ("x", "invalid"),
                ("-1", "min_value"),
            ):
                with self.subTest(field=field, value=value):
                    data = {"zone_size": "50", other: "1"}
                    if value is not None:
                        data[field] = value
                    self.assert_field_error(
                        SingleCellParamsForm(data=data), field, code
                    )

    def test_zone_size_required(self) -> None:
        self.assert_field_error(
            SingleCellParamsForm(data={"r": "1", "c": "1"}), "zone_size", "required"
        )


class TestHeatmapParamsForm(_FormAsserts):
    def test_team_color_blank_is_none(self) -> None:
        cleaned = self.assert_valid(
            HeatmapParamsForm(data={"zone_size": "50", "team_color": ""})
        )
        self.assertIsNone(cleaned["team_color"])

    def test_team_color_absent_is_none(self) -> None:
        cleaned = self.assert_valid(HeatmapParamsForm(data={"zone_size": "50"}))
        self.assertIsNone(cleaned["team_color"])

    def test_team_color_values(self) -> None:
        for color in ("red", "blue"):
            with self.subTest(color=color):
                cleaned = self.assert_valid(
                    HeatmapParamsForm(data={"zone_size": "50", "team_color": color})
                )
                self.assertEqual(cleaned["team_color"], color)

    def test_bad_team_color_is_invalid_choice(self) -> None:
        self.assert_field_error(
            HeatmapParamsForm(data={"zone_size": "50", "team_color": "green"}),
            "team_color",
            "invalid_choice",
        )

    def test_zone_size_required(self) -> None:
        self.assert_field_error(HeatmapParamsForm(data={}), "zone_size", "required")
