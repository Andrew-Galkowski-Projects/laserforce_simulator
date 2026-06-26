"""Tests for the round-playback map overlay on the events screen.

The overlay reconstructs each player's corridor-faithful route from the
persisted ``event_type="movement"`` ``GameEvent`` rows (MOVE-01 stores only the
Advance's start + end cell) using the simulator's own A* (``astar_path``), and
ships ``{"zone_size", "players": [...]}`` to ``game_round_events.html`` for the
canvas overlay drawn on the processed map PNG. Map-less rounds (3-zone
fallback) yield ``None`` and fall back to the scoreboard + live feed only.
"""

from __future__ import annotations

from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    ArenaMap,
    BaseSightLineConfig,
    MapBaseConfig,
    MapZoneConfig,
    SightLineConfig,
)
from matches.models import GameEvent, GameRound, PlayerRoundState
from matches.tests.conftest import make_team_with_slots
from matches.views import _build_playback_map


def _make_arena_map(name: str = "PbArena") -> tuple[ArenaMap, int]:
    """A minimal fully-configured ``ArenaMap`` (4x4 all-floor grid) so
    ``load_map_context`` resolves without raising. Mirrors the helper in
    ``test_heatmap.py`` / ``test_simulation_view_paths.py``.
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
        arena_map=arena_map, base_type="red", x_px=zone_size // 2, y_px=zone_size // 2
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map, zone_size=zone_size, sight_data={}
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
    )
    return arena_map, zone_size


def _round(arena_map, zone_size, *, prefix: str):
    red, red_players = make_team_with_slots(prefix + "R")
    blue, blue_players = make_team_with_slots(prefix + "B")
    gr = GameRound.objects.create(
        round_number=1,
        team_red=red,
        team_blue=blue,
        arena_map=arena_map,
        zone_size=zone_size,
    )
    return gr, red_players, blue_players


def _move(gr, actor, ts, start, end, route=None):
    metadata = {
        "actor_role": "commander",
        "start_row": start[0],
        "start_col": start[1],
        "end_row": end[0],
        "end_col": end[1],
        "cell_row": end[0],
        "cell_col": end[1],
        "new_zone": 1,
    }
    if route is not None:
        metadata["route"] = route
    GameEvent.objects.create(
        game_round=gr,
        timestamp=ts,
        event_type="movement",
        actor=actor,
        points_awarded=0,
        description=f"{actor.name} moves to cell ({end[0]}, {end[1]})",
        metadata=metadata,
    )


class TestBuildPlaybackMap(TestCase):
    def test_returns_none_without_map(self) -> None:
        gr, _r, _b = _round(None, None, prefix="PbNoMap")
        self.assertIsNone(_build_playback_map(gr))

    def test_reconstructs_corridor_faithful_routes(self) -> None:
        arena_map, zone_size = _make_arena_map("PbRoutes")
        gr, red_players, _b = _round(arena_map, zone_size, prefix="PbRoutes")
        commander = red_players["commander"]
        PlayerRoundState.objects.create(
            game_round=gr,
            player=commander,
            team_color="red",
            role="commander",
            cell_row=0,
            cell_col=0,
        )
        # Two Advances along the top row: (0,0)->(0,2)->(2,2).
        _move(gr, commander, 4, (0, 0), (0, 2))
        _move(gr, commander, 10, (0, 2), (2, 2))

        payload = _build_playback_map(gr)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["zone_size"], zone_size)

        players = {p["id"]: p for p in payload["players"]}
        p = players[commander.id]
        self.assertEqual(p["team"], "red")
        self.assertEqual(p["role"], "commander")
        self.assertEqual(p["spawn"], [0, 0])

        # moves: [[ts, polyline], ...]; polyline excludes the start cell and
        # ends at the end cell, following the 4-connected grid corridor.
        self.assertEqual(len(p["moves"]), 2)
        ts0, poly0 = p["moves"][0]
        self.assertEqual(ts0, 4)
        self.assertEqual(poly0[-1], [0, 2])
        self.assertNotIn([0, 0], poly0)  # start excluded
        ts1, poly1 = p["moves"][1]
        self.assertEqual(ts1, 10)
        self.assertEqual(poly1[-1], [2, 2])


class TestStoredRoutePreferred(TestCase):
    def test_uses_stored_route_verbatim_over_astar(self) -> None:
        """When a movement event carries ``metadata["route"]`` (the true cells
        the sim walked), the overlay uses it verbatim rather than re-deriving
        a path with A* — even a winding route that A* would never produce."""
        arena_map, zone_size = _make_arena_map("PbStored")
        gr, red_players, _b = _round(arena_map, zone_size, prefix="PbStored")
        commander = red_players["commander"]
        PlayerRoundState.objects.create(
            game_round=gr,
            player=commander,
            team_color="red",
            role="commander",
            cell_row=0,
            cell_col=0,
        )
        winding = [[1, 0], [1, 1], [0, 1], [0, 2], [0, 3]]
        _move(gr, commander, 4, (0, 0), (0, 3), route=winding)

        payload = _build_playback_map(gr)
        p = {pl["id"]: pl for pl in payload["players"]}[commander.id]
        self.assertEqual(p["moves"], [[4, winding]])


class TestSimulationPersistsRoute(TestCase):
    def test_movement_events_carry_route_metadata(self) -> None:
        """An end-to-end map round persists ``metadata["route"]`` on its
        movement events: the exact cells walked, ending at the end cell —
        wired PlayerState.movement_routes -> _move_player_in_memory -> flush."""
        import random
        from unittest.mock import patch

        from core.map_processing import compute_sight_lines
        from core.models import SightLineConfig
        from matches.models import GameEvent as GE
        from matches.simulation import BatchSimulator

        arena_map, zone_size = _make_arena_map("PbSim")
        # Real sight lines so role positioning + movement actually fire.
        SightLineConfig.objects.filter(arena_map=arena_map).update(
            sight_data=compute_sight_lines([[1] * 4 for _ in range(4)])
        )
        red, _r = make_team_with_slots("PbSimR")
        blue, _b = make_team_with_slots("PbSimB")

        random.seed(42)
        with patch.object(BatchSimulator, "ROUND_TICKS", 120):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map
            )

        moves = list(GE.objects.filter(game_round=gr, event_type="movement"))
        self.assertTrue(moves, "expected movement events on a map round")
        routed = [m for m in moves if m.metadata.get("route")]
        self.assertTrue(routed, "expected at least one movement event with a route")
        for m in routed:
            route = m.metadata["route"]
            self.assertIsInstance(route, list)
            self.assertEqual(route[-1], [m.metadata["end_row"], m.metadata["end_col"]])


class TestEventsScreenOverlay(TestCase):
    def test_map_round_renders_canvas_and_data(self) -> None:
        arena_map, zone_size = _make_arena_map("PbView")
        gr, red_players, _b = _round(arena_map, zone_size, prefix="PbView")
        commander = red_players["commander"]
        PlayerRoundState.objects.create(
            game_round=gr,
            player=commander,
            team_color="red",
            role="commander",
            cell_row=0,
            cell_col=0,
        )
        _move(gr, commander, 4, (0, 0), (0, 2))

        url = reverse("game_round_events", kwargs={"round_id": gr.id})
        body = Client().get(url).content.decode("utf-8")
        self.assertIn('id="pb-map-canvas"', body)
        self.assertIn('id="pb-map-bg"', body)
        self.assertIn('id="pb-map-data"', body)
        self.assertIn('id="pb-live-feed"', body)
        # Scoreboard + feed are matched-height columns (sized client-side).
        self.assertIn('id="pb-scoreboard-col"', body)
        self.assertIn('id="pb-feed-card"', body)

    def test_map_less_round_shows_notice_no_canvas(self) -> None:
        gr, _r, _b = _round(None, None, prefix="PbViewNoMap")
        url = reverse("game_round_events", kwargs={"round_id": gr.id})
        body = Client().get(url).content.decode("utf-8")
        self.assertIn('id="pb-no-map-notice"', body)
        self.assertNotIn('id="pb-map-canvas"', body)
        # The live feed is still present for map-less rounds.
        self.assertIn('id="pb-live-feed"', body)
        self.assertIn('id="pb-feed-card"', body)
