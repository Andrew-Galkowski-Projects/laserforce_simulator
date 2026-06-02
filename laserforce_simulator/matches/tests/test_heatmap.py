"""Tests for the movement heatmap (RES-04): pure cell-occupancy reconstruction
and the per-round / multi-round aggregate heatmap views.
"""

from __future__ import annotations

import json
from typing import Any

from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    ArenaMap,
    BaseSightLineConfig,
    MapBaseConfig,
    MapZoneConfig,
    SightLineConfig,
)
from matches.models import GameRound, PlayerRoundState
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_arena_map(name: str = "Res04Arena") -> tuple[ArenaMap, int]:
    """Build a minimal ``ArenaMap`` with a confirmed ``MapZoneConfig`` and
    the supporting sight-line / base-sight rows. Mirrors the helper in
    ``test_simulation_view_paths.py`` so the two suites stay shape-aligned.
    """
    zone_size = 50
    zone_data = [[1] * 4 for _ in range(4)]
    arena_map = ArenaMap.objects.create(
        name=name, img_width=4 * zone_size, img_height=4 * zone_size
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data,
        confirmed=True,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="red",
        x_px=zone_size // 2,
        y_px=zone_size // 2,
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        sight_data={},
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map,
        base_type="red",
        zone_size=zone_size,
        visible_cells=[],
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        zone_size=zone_size,
        visible_cells=[],
    )
    return arena_map, zone_size


def _make_round(
    *,
    arena_map: ArenaMap | None,
    zone_size: int | None,
    cell_occupancy_json: dict[str, Any] | None,
    red_prefix: str,
    blue_prefix: str,
) -> tuple[GameRound, dict, dict]:
    """Create a ``GameRound`` with two slotted teams and (optionally) a
    populated ``cell_occupancy_json``. Returns the round plus the two
    role→Player dicts so callers can wire ``PlayerRoundState`` rows
    against specific players.
    """
    red, red_players = make_team_with_slots(red_prefix)
    blue, blue_players = make_team_with_slots(blue_prefix)
    gr = GameRound.objects.create(
        round_number=1,
        team_red=red,
        team_blue=blue,
        arena_map=arena_map,
        zone_size=zone_size,
        cell_occupancy_json=cell_occupancy_json,
    )
    return gr, red_players, blue_players


# ---------------------------------------------------------------------------
# Section A — Per-round movement_heatmap view
# ---------------------------------------------------------------------------


class TestRoundHeatmapView(TestCase):
    """``GET /matches/game-round/<id>/heatmap/`` — the per-round view."""

    def test_round_heatmap_view_200(self) -> None:
        """Happy path: canvas + the three filter dropdowns + the
        ``cell-occupancy-data`` json_script id are all in the body."""
        arena_map, zone_size = _make_arena_map("Res04View200")
        gr, _red, _blue = _make_round(
            arena_map=arena_map,
            zone_size=zone_size,
            cell_occupancy_json={"101": {"5,5": 100, "6,6": 50}, "102": {"7,7": 30}},
            red_prefix="Res04View200R",
            blue_prefix="Res04View200B",
        )
        url = reverse("movement_heatmap", kwargs={"round_id": gr.id})
        resp = Client().get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn('id="heatmap-canvas"', body)
        self.assertIn('id="heatmap-filter-player"', body)
        self.assertIn('id="heatmap-filter-role"', body)
        self.assertIn('id="heatmap-filter-team"', body)
        self.assertIn('id="cell-occupancy-data"', body)

    def test_round_heatmap_view_missing_map_notice(self) -> None:
        """``arena_map=None`` → response carries the
        ``heatmap-no-map-notice`` id and a ``"No map"`` substring."""
        gr, _red, _blue = _make_round(
            arena_map=None,
            zone_size=None,
            cell_occupancy_json=None,
            red_prefix="Res04ViewNoMapR",
            blue_prefix="Res04ViewNoMapB",
        )
        url = reverse("movement_heatmap", kwargs={"round_id": gr.id})
        resp = Client().get(url)
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode("utf-8")
        self.assertIn('id="heatmap-no-map-notice"', body)
        self.assertIn("No map", body)

    def test_round_heatmap_view_404_for_missing_round(self) -> None:
        """Bogus round PK → 404 via ``get_object_or_404``."""
        url = reverse("movement_heatmap", kwargs={"round_id": 999999})
        resp = Client().get(url)
        self.assertEqual(resp.status_code, 404)

    def test_round_heatmap_view_405_non_get(self) -> None:
        """POST is not allowed; method-not-allowed → 405."""
        arena_map, zone_size = _make_arena_map("Res04View405")
        gr, _red, _blue = _make_round(
            arena_map=arena_map,
            zone_size=zone_size,
            cell_occupancy_json={},
            red_prefix="Res04View405R",
            blue_prefix="Res04View405B",
        )
        url = reverse("movement_heatmap", kwargs={"round_id": gr.id})
        resp = Client().post(url)
        self.assertEqual(resp.status_code, 405)


# ---------------------------------------------------------------------------
# Section B — Map-aggregate map_heatmap_data endpoint
# ---------------------------------------------------------------------------


class TestMapHeatmapDataEndpoint(TestCase):
    """``GET /maps/<id>/heatmap-data/?zone_size=<n>[&team_color=...]`` —
    the cross-round aggregation endpoint."""

    def test_map_heatmap_data_endpoint_returns_merged(self) -> None:
        """Two rounds on the same map+zone_size → ``round_count == 2`` and
        per-cell ticks summed across both rounds."""
        arena_map, zone_size = _make_arena_map("Res04AggMap")
        gr1, _r, _b = _make_round(
            arena_map=arena_map,
            zone_size=zone_size,
            cell_occupancy_json={"101": {"5,5": 60}, "102": {"7,7": 10}},
            red_prefix="Res04AggR1",
            blue_prefix="Res04AggB1",
        )
        gr2, _r2, _b2 = _make_round(
            arena_map=arena_map,
            zone_size=zone_size,
            cell_occupancy_json={"201": {"5,5": 40}, "202": {"8,8": 20}},
            red_prefix="Res04AggR2",
            blue_prefix="Res04AggB2",
        )
        # Use both rounds so the test reads as a real two-row aggregation.
        _ = (gr1, gr2)

        url = reverse("map_heatmap_data", kwargs={"map_id": arena_map.id})
        resp = Client().get(url, {"zone_size": zone_size})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["round_count"], 2)
        # "5,5" appears in both rounds → 60 + 40 = 100
        self.assertEqual(data["cell_occupancy"]["5,5"], 100)
        # "7,7" appears only in round 1
        self.assertEqual(data["cell_occupancy"].get("7,7"), 10)
        # "8,8" appears only in round 2
        self.assertEqual(data["cell_occupancy"].get("8,8"), 20)

    def test_map_heatmap_data_team_color_filter(self) -> None:
        """``team_color=red`` → only red players' cells contribute."""
        arena_map, zone_size = _make_arena_map("Res04FilterMap")
        gr, red_players, blue_players = _make_round(
            arena_map=arena_map,
            zone_size=zone_size,
            cell_occupancy_json=None,
            red_prefix="Res04FilterR",
            blue_prefix="Res04FilterB",
        )
        # Wire keys against the actual Player PKs so the team-color join
        # inside the view resolves cleanly.
        gr.cell_occupancy_json = {
            str(red_players["scout"].id): {"0,0": 100},
            str(blue_players["scout"].id): {"1,1": 50},
        }
        gr.save(update_fields=["cell_occupancy_json"])
        # Persist PlayerRoundState rows so the view can resolve
        # player_id -> team_color.
        PlayerRoundState.objects.create(
            game_round=gr,
            player=red_players["scout"],
            team_color="red",
            role="scout",
        )
        PlayerRoundState.objects.create(
            game_round=gr,
            player=blue_players["scout"],
            team_color="blue",
            role="scout",
        )

        url = reverse("map_heatmap_data", kwargs={"map_id": arena_map.id})
        resp = Client().get(url, {"zone_size": zone_size, "team_color": "red"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("0,0", data["cell_occupancy"])
        self.assertEqual(data["cell_occupancy"]["0,0"], 100)
        self.assertNotIn("1,1", data["cell_occupancy"])

    def test_map_heatmap_data_404_for_missing_map(self) -> None:
        """Bogus map_id → 404."""
        url = reverse("map_heatmap_data", kwargs={"map_id": 999999})
        resp = Client().get(url, {"zone_size": 50})
        self.assertEqual(resp.status_code, 404)

    def test_map_heatmap_data_400_missing_zone_size(self) -> None:
        """No ``zone_size`` query param → 400, body contains the locked
        error string ``"zone_size required"``."""
        arena_map, _zs = _make_arena_map("Res04Bad400Zone")
        url = reverse("map_heatmap_data", kwargs={"map_id": arena_map.id})
        resp = Client().get(url)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("zone_size required", resp.content.decode("utf-8"))

    def test_map_heatmap_data_400_invalid_team_color(self) -> None:
        """``team_color=purple`` → 400, body contains the locked error
        string ``"invalid team_color"``."""
        arena_map, zone_size = _make_arena_map("Res04Bad400Team")
        url = reverse("map_heatmap_data", kwargs={"map_id": arena_map.id})
        resp = Client().get(url, {"zone_size": zone_size, "team_color": "purple"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("invalid team_color", resp.content.decode("utf-8"))

    def test_map_heatmap_data_405_non_get(self) -> None:
        """POST is not allowed; method-not-allowed → 405."""
        arena_map, zone_size = _make_arena_map("Res04Bad405")
        url = reverse("map_heatmap_data", kwargs={"map_id": arena_map.id})
        resp = Client().post(url, {"zone_size": zone_size})
        self.assertEqual(resp.status_code, 405)


# Silence "unused import" for json — kept so future tests can decode payloads
# directly when needed (resp.json() is the canonical path used above).
_ = json


# ===== Cell-occupancy reconstruction (pure unit) =====
import unittest

from matches.sim_helpers.cell_occupancy import reconstruct_cell_occupancy


class TestReconstructCellOccupancy(unittest.TestCase):
    """Pure-unit coverage of the apportionment / rounding algorithm.

    Each test builds an inline ``adj`` dict so the A* expansion inside
    ``reconstruct_cell_occupancy`` walks the exact route the test cares
    about; we do not call ``build_movement_adjacency`` from this file.
    """

    # ----- Empty-trail edge cases ----------------------------------------

    def test_empty_trail_survived_credits_spawn(self) -> None:
        """Survived (sentinel 1801) + no Advances → spawn cell credited the
        full ``round_ticks``."""
        spawn = (3, 4)
        result = reconstruct_cell_occupancy(
            movement_trail=[],
            spawn_cell=spawn,
            round_ticks=1800,
            eliminated_at=1801,
            adj={spawn: []},
        )
        self.assertEqual(result, {spawn: 1800})

    def test_empty_trail_eliminated_at_zero_yields_empty(self) -> None:
        """Eliminated at tick 0 + no Advances → no credit; output is empty."""
        spawn = (0, 0)
        result = reconstruct_cell_occupancy(
            movement_trail=[],
            spawn_cell=spawn,
            round_ticks=1800,
            eliminated_at=0,
            adj={spawn: []},
        )
        self.assertEqual(result, {})

    # ----- 1-cell Advance (worked example) -------------------------------

    def test_single_one_cell_advance(self) -> None:
        """Worked example from the seam contract (§2 / §7.1 #3).

        Trail: ``[((0,0),(0,1),10)]``, ``round_ticks=20``,
        ``eliminated_at=1801``.

        Float math (per the contract algorithm):
          * Stationary `[0, 10)` → +10 to (0,0).
          * Advance at ts=10 → 0.5 to (0,0), 0.5 to (0,1).
          * Trailing stationary `[11, 20)` → +9 to (0,1).
          * Totals: (0,0)=10.5, (0,1)=9.5.

        Banker's rounding (Python ``round``): both round to 10.
        """
        adj = {(0, 0): [(0, 1)], (0, 1): [(0, 0)]}
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 1), 10)],
            spawn_cell=(0, 0),
            round_ticks=20,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertEqual(result, {(0, 0): 10, (0, 1): 10})

    # ----- Multi-cell Advance --------------------------------------------

    def test_multi_cell_advance_apportions_evenly(self) -> None:
        """Multi-cell Advance with too-little surrounding time: all credit
        rounds to zero and the result is empty.

        Trail: ``[((0,0),(0,3),0)]``, route ``(0,0)→(0,1)→(0,2)→(0,3)``.
        ``round_ticks=1``, ``eliminated_at=1801``. ``N=4`` cells walked,
        each gets ``1/4 = 0.25`` tick → all round to 0 → omitted.
        """
        adj = {
            (0, 0): [(0, 1)],
            (0, 1): [(0, 0), (0, 2)],
            (0, 2): [(0, 1), (0, 3)],
            (0, 3): [(0, 2)],
        }
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 3), 0)],
            spawn_cell=(0, 0),
            round_ticks=1,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertEqual(result, {})

    def test_multi_cell_advance_with_long_run(self) -> None:
        """Same multi-cell route as above but the player rests on the
        destination for many ticks afterward.

        ``round_ticks=100``: only (0,3) crosses the rounding threshold
        because trailing stationary credits it 99 + 0.25. The other route
        cells stay at 0.25 each (round to 0, omitted).
        """
        adj = {
            (0, 0): [(0, 1)],
            (0, 1): [(0, 0), (0, 2)],
            (0, 2): [(0, 1), (0, 3)],
            (0, 3): [(0, 2)],
        }
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 3), 0)],
            spawn_cell=(0, 0),
            round_ticks=100,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertIn((0, 3), result)
        self.assertEqual(result[(0, 3)], 99)
        # The fractional 0.25-credit cells must be omitted (round() to 0).
        self.assertNotIn((0, 0), result)
        self.assertNotIn((0, 1), result)
        self.assertNotIn((0, 2), result)

    # ----- Stationary between two Advances -------------------------------

    def test_stationary_between_two_advances(self) -> None:
        """Two 1-cell Advances with a 9-tick rest on the middle cell.

        Trail: ``[((0,0),(0,1),5), ((0,1),(0,2),15)]``, ``round_ticks=20``.
        Per-cell float totals (algorithm):
          * (0,0): 5 (stationary) + 0.5 (Advance 1 split) = 5.5
          * (0,1): 0.5 (Advance 1 split) + 9 (stationary 6..14)
                  + 0.5 (Advance 2 split) = 10.0
          * (0,2): 0.5 (Advance 2 split) + 4 (trailing 16..19) = 4.5

        Banker's rounding: 5.5→6, 10.0→10, 4.5→4.
        """
        adj = {
            (0, 0): [(0, 1)],
            (0, 1): [(0, 0), (0, 2)],
            (0, 2): [(0, 1)],
        }
        result = reconstruct_cell_occupancy(
            movement_trail=[((0, 0), (0, 1), 5), ((0, 1), (0, 2), 15)],
            spawn_cell=(0, 0),
            round_ticks=20,
            eliminated_at=1801,
            adj=adj,
        )
        self.assertEqual(result.get((0, 1)), 10)
        self.assertEqual(result.get((0, 0)), 6)
        self.assertEqual(result.get((0, 2)), 4)

    # ----- Elimination cutoff --------------------------------------------

    def test_post_elimination_cutoff(self) -> None:
        """No credit accumulates past ``eliminated_at`` for an early-out.

        Trail empty, ``eliminated_at=50``, ``round_ticks=1800``. The spawn
        cell gets exactly 50 ticks (the early cutoff). Sum equals 50.
        """
        spawn = (7, 7)
        result = reconstruct_cell_occupancy(
            movement_trail=[],
            spawn_cell=spawn,
            round_ticks=1800,
            eliminated_at=50,
            adj={spawn: []},
        )
        self.assertEqual(result, {spawn: 50})
        self.assertEqual(sum(result.values()), 50)

    # ----- Rounding-slack reconciliation ---------------------------------

    def test_sum_reconciliation_within_rounding_slack(self) -> None:
        """For a realistic deterministic trail the integer cell sum cannot
        exceed ``min(round_ticks, eliminated_at)``, and the rounding-slack
        deviation is bounded by ``len(result)`` (≤ 0.5 per cell).

        Fixture: a 6-cell loop walked across 5 Advances at evenly-spaced
        ticks across a 60-tick window (no elimination — survived sentinel).
        """
        loop = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5)]
        # 4-connected linear adj (only neighbours)
        adj = {
            loop[i]: (
                [loop[i - 1]]
                if i == len(loop) - 1
                else ([loop[i + 1]] if i == 0 else [loop[i - 1], loop[i + 1]])
            )
            for i in range(len(loop))
        }
        trail = [(loop[i], loop[i + 1], (i + 1) * 10) for i in range(5)]
        round_ticks = 60
        result = reconstruct_cell_occupancy(
            movement_trail=trail,
            spawn_cell=loop[0],
            round_ticks=round_ticks,
            eliminated_at=1801,
            adj=adj,
        )
        cap = min(round_ticks, 1801)
        total = sum(result.values())
        self.assertLessEqual(total, cap)
        # Rounding slack: cumulative |delta| ≤ 0.5 per cell.
        self.assertLessEqual(abs(total - cap), len(result))

    # ----- Module-purity guard -------------------------------------------

    def test_pure_no_django_imports(self) -> None:
        """The helper module must not leak ``django`` or ``models`` names.

        This pins the seam contract's "no Django imports" requirement
        (§2 — "Pure Python. No Django imports.").
        """
        import matches.sim_helpers.cell_occupancy as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))


if __name__ == "__main__":
    unittest.main()
