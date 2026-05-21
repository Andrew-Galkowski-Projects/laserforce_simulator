"""RES-04 — DB / view tests for the movement-heatmap surfaces.

Pins §7.2 of the RES-04 seam contract: the per-round heatmap view
(``movement_heatmap``) and the map-aggregate JSON endpoint
(``map_heatmap_data``). Both depend on ``GameRound.cell_occupancy_json``,
the new ``JSONField`` introduced by migration ``0026_*``; the Code agent
adds the column and the views — until then these tests will fail at
collection (no URL name / no model field).
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
    ``test_sim09_consolidation.py`` so the two suites stay shape-aligned.
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
