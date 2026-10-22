"""RV-05 T2: tests for the pure ``pdf_charts`` renderer (#285).

The seam is ``matches.sim_helpers.pdf_charts``: ``CHARTS``,
``render_chart_png`` and ``chart_flowable``. Fixtures are hand-built in the
series-port shape from #283 (``players`` plus ``shots`` / ``lives`` /
``points`` / ``sp`` keyed by ``str(player id)``); the port itself is not
imported.
"""

from __future__ import annotations

import struct
import unittest
from importlib.metadata import version
from pathlib import Path

from reportlab.platypus import Image

from laserforce_simulator.testing import run_project_python
from matches.sim_helpers.pdf_charts import (
    CHARTS,
    chart_flowable,
    player_colour,
    render_chart_png,
)

PAGE_TITLES = [
    "Shots Over Time (per player)",
    "Lives Over Time (per player)",
    "Points Over Time (per player)",
    "Special Points (SP) Over Time (per player)",
]

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: The requirements file the Dockerfile installs from (the Django project dir).
REQUIREMENTS = Path(__file__).resolve().parents[2] / "requirements.txt"


def _png_pixel_size(png: bytes) -> tuple[int, int]:
    """(width, height) from the PNG IHDR chunk: bytes 16..24, big-endian."""
    return struct.unpack(">II", png[16:24])


def _populated_series() -> dict:
    """One red and one blue player with a few value changes on every series.

    Hand-built in the #283 series-port shape; values are arbitrary literals.
    """
    return {
        "players": [
            {"id": 1, "name": "RedHvy", "team": "red"},
            {"id": 2, "name": "BluSct", "team": "blue"},
        ],
        "shots": {
            "1": [(0, 40), (12.5, 39), (30, 20)],
            "2": [(0, 30), (7, 29), (45.5, 10)],
        },
        "lives": {
            "1": [(0, 15), (60, 14), (120, 13)],
            "2": [(0, 15), (90, 14)],
        },
        "points": {
            "1": [(0, 0), (12.5, 100), (30, 250)],
            "2": [(0, 0), (7, 100), (45.5, -50)],
        },
        "sp": {
            "1": [(0, 0), (12.5, 5), (30, 99)],
            "2": [(0, 0), (7, 10)],
        },
    }


class TestPlayerColour(unittest.TestCase):
    """The events-page hue rule: ``hsl(base + 40*i/n, 70%, 45%)`` with base 350
    for red and 210 for blue (``pickColors`` in game_round_events.html)."""

    @staticmethod
    def _hsl(hue: float) -> tuple[float, float, float]:
        import colorsys

        return colorsys.hls_to_rgb(hue / 360.0, 0.45, 0.70)

    def test_red_and_blue_bases(self) -> None:
        self.assertEqual(player_colour("red", 0, 1), self._hsl(350))
        self.assertEqual(player_colour("blue", 0, 1), self._hsl(210))

    def test_hue_spreads_40_degrees_across_the_team_and_wraps(self) -> None:
        # red, second of two: 350 + 40 * 1/2 = 370 -> 10 (wraps like CSS)
        self.assertEqual(player_colour("red", 1, 2), self._hsl(10))
        # blue, third of four: 210 + 40 * 2/4 = 230
        self.assertEqual(player_colour("blue", 2, 4), self._hsl(230))


class TestChartDefinitions(unittest.TestCase):
    """``CHARTS`` is the ordered four-chart contract the PDF iterates."""

    def test_four_keys_in_page_order_with_page_titles(self) -> None:
        self.assertEqual(
            [chart.key for chart in CHARTS], ["shots", "lives", "points", "sp"]
        )
        self.assertEqual([chart.title for chart in CHARTS], PAGE_TITLES)

    def test_sp_is_fixed_0_to_99_with_cap_at_99(self) -> None:
        sp = CHARTS[3]
        self.assertEqual(sp.y_min, 0)
        self.assertEqual(sp.y_max, 99)
        self.assertEqual(sp.cap, 99)

    def test_shots_lives_points_begin_at_zero_with_no_ceiling(self) -> None:
        for chart in CHARTS[:3]:
            with self.subTest(chart=chart.key):
                self.assertEqual(chart.y_min, 0)
                self.assertIsNone(chart.y_max)
                self.assertIsNone(chart.cap)


class TestRenderChartPng(unittest.TestCase):
    """``render_chart_png`` draws one chart of a series into PNG bytes."""

    def test_each_chart_key_renders_png_bytes_on_populated_series(self) -> None:
        series = _populated_series()
        for chart in CHARTS:
            with self.subTest(chart=chart.key):
                png = render_chart_png(chart.key, series)
                self.assertIsInstance(png, bytes)
                self.assertTrue(png.startswith(PNG_MAGIC))

    def test_empty_series_with_no_players_renders_png(self) -> None:
        empty = {"players": [], "shots": {}, "lives": {}, "points": {}, "sp": {}}
        for chart in CHARTS:
            with self.subTest(chart=chart.key):
                self.assertTrue(
                    render_chart_png(chart.key, empty).startswith(PNG_MAGIC)
                )

    def test_players_with_only_start_points_render_png(self) -> None:
        start_only = {
            "players": [
                {"id": 1, "name": "RedHvy", "team": "red"},
                {"id": 2, "name": "BluSct", "team": "blue"},
            ],
            "shots": {"1": [(0, 40)], "2": [(0, 30)]},
            "lives": {"1": [(0, 15)], "2": [(0, 15)]},
            "points": {"1": [(0, 0)], "2": [(0, 0)]},
            "sp": {"1": [(0, 0)], "2": [(0, 0)]},
        }
        for chart in CHARTS:
            with self.subTest(chart=chart.key):
                png = render_chart_png(chart.key, start_only)
                self.assertTrue(png.startswith(PNG_MAGIC))

    def test_unknown_chart_key_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            render_chart_png("accuracy", _populated_series())


class TestChartFlowable(unittest.TestCase):
    """``chart_flowable`` wraps PNG bytes as a ReportLab ``Image`` flowable."""

    def test_image_at_requested_width_keeps_png_aspect_ratio(self) -> None:
        png = render_chart_png("shots", _populated_series())
        pixel_w, pixel_h = _png_pixel_size(png)
        self.assertGreater(pixel_w, pixel_h)  # a landscape chart, not square

        flowable = chart_flowable(png, 300.0)

        self.assertIsInstance(flowable, Image)
        self.assertEqual(flowable.drawWidth, 300.0)
        self.assertAlmostEqual(flowable.drawHeight, 300.0 * pixel_h / pixel_w, places=6)


class TestModulePurity(unittest.TestCase):
    """Subprocess fresh-import ``matches.sim_helpers.pdf_charts`` and assert
    nothing global leaked: no ``matplotlib.pyplot`` (object-oriented API only,
    no backend switch) and no ``django`` module (the event-log precedent).
    """

    def test_import_does_not_load_pyplot(self) -> None:
        code = (
            "import sys\n"
            "import matches.sim_helpers.pdf_charts\n"
            "assert 'matplotlib.pyplot' not in sys.modules, 'pyplot was imported'\n"
        )
        result = run_project_python(code, timeout=30)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_import_leaks_no_django(self) -> None:
        code = (
            "import sys\n"
            "import matches.sim_helpers.pdf_charts\n"
            "leaked = [m for m in sys.modules if m == 'django' or m.startswith('django.')]\n"
            "assert not leaked, f'leaked django modules: {leaked}'\n"
        )
        result = run_project_python(code, timeout=30)
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


class TestDependencyPin(unittest.TestCase):
    """``matplotlib>=3.8`` is pinned where the Dockerfile installs from and the
    venv running this suite satisfies it."""

    def test_requirements_pin_matplotlib_3_8(self) -> None:
        lines = REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        self.assertIn("matplotlib>=3.8", [line.strip() for line in lines])

    def test_installed_matplotlib_is_at_least_3_8(self) -> None:
        major, minor = (int(part) for part in version("matplotlib").split(".")[:2])
        self.assertGreaterEqual((major, minor), (3, 8))


if __name__ == "__main__":
    unittest.main()
