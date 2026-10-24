"""Tests for the repo-level ``scripts/check_sqlite_json_lookups.py`` check.

The script lives outside the Django project, so it is loaded by path.
"""

import importlib.util
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_SCRIPT = (
    Path(__file__).resolve().parent.parent / "scripts" / "check_sqlite_json_lookups.py"
)


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_sqlite_json_lookups", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_script()


class JsonFieldNamesTests(TestCase):
    def test_collects_json_field_assignments_only(self) -> None:
        source = (
            "class Season(models.Model):\n"
            "    starting_team_ids_json = models.JSONField(null=True)\n"
            "    metadata = JSONField(default=dict)\n"
            "    name = models.CharField(max_length=10)\n"
        )
        self.assertEqual(
            check.json_field_names(source), {"starting_team_ids_json", "metadata"}
        )


class HitsTests(TestCase):
    names = {"starting_team_ids_json", "metadata"}

    def test_flags_contains_and_contained_by_on_a_json_field(self) -> None:
        source = (
            "Season.objects.filter(starting_team_ids_json__contains=[1])\n"  # check-sqlite-json: ignore
            "Match.objects.filter(season__metadata__contained_by={})\n"  # check-sqlite-json: ignore
            'q = {"starting_team_ids_json__contains": [2]}\n'  # check-sqlite-json: ignore
        )
        self.assertEqual(
            [line for line, _ in check.hits(source, self.names)], [1, 2, 3]
        )

    def test_ignores_other_fields_and_lookups(self) -> None:
        source = (
            "Team.objects.filter(name__contains='x')\n"
            "Team.objects.filter(name__icontains='x')\n"
            "Season.objects.filter(metadata__has_key='a')\n"
            "def __contains__(self, key): ...\n"
        )
        self.assertEqual(check.hits(source, self.names), [])

    def test_a_marked_line_opts_out(self) -> None:
        marker = check.IGNORE_MARKER
        source = f"Season.objects.filter(metadata__contains=1)  # {marker}\n"  # check-sqlite-json: ignore
        self.assertEqual(check.hits(source, self.names), [])


class MainTests(TestCase):
    """``main`` reads the real models, so ``starting_team_ids_json`` counts."""

    def _main_on(self, source: str) -> int:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(source, encoding="utf-8")
            return check.main([str(path)])

    def test_exits_one_on_a_hit(self) -> None:
        source = "Season.objects.filter(starting_team_ids_json__contains=[1])\n"  # check-sqlite-json: ignore
        self.assertEqual(self._main_on(source), 1)

    def test_exits_zero_on_a_clean_file(self) -> None:
        source = "Season.objects.filter(teams=team, name__contains='x')\n"
        self.assertEqual(self._main_on(source), 0)
