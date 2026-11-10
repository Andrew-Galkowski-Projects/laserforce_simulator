"""``export_playback_round``: one Round's Playback data as a JSON file, for a prototype page
(RV-07 retro: RV-06 and RV-07 each hand-wrote this export to build their prototypes)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase

from matches.models import GameEvent
from matches.tests.test_playback_map import _make_arena_map, _move, _round


class ExportPlaybackRoundTests(TestCase):
    def setUp(self) -> None:
        arena_map, zone_size = _make_arena_map("ExportArena")
        self.round, red, blue = _round(arena_map, zone_size, prefix="Export")
        commander, scout = red["commander"], blue["scout"]
        _move(self.round, commander, 1, (0, 0), (0, 1))
        GameEvent.objects.create(
            game_round=self.round,
            timestamp=4,
            event_type="tag",
            actor=commander,
            target=scout,
            points_awarded=100,
            description="tag",
            metadata={"actor_role": "commander", "target_role": "scout"},
        )
        self.out = Path(tempfile.mkdtemp()) / "round.json"

    def _export(self) -> dict:
        call_command(
            "export_playback_round",
            "--round",
            str(self.round.pk),
            "--out",
            str(self.out),
        )
        return json.loads(self.out.read_text(encoding="utf-8"))

    def test_writes_the_playback_the_events_and_the_bases(self) -> None:
        data = self._export()
        self.assertEqual(
            set(data), {"round", "playback", "events", "bases", "zone_size", "image"}
        )
        self.assertEqual(data["round"], self.round.pk)
        self.assertIn("players", data["playback"])
        self.assertEqual({b["type"] for b in data["bases"]}, {"red", "blue"})
        self.assertEqual(data["zone_size"], 50)

    def test_events_leave_out_movement(self) -> None:
        types = [e["event_type"] for e in self._export()["events"]]
        self.assertEqual(types, ["tag"])

    def test_a_map_with_no_image_exports_none(self) -> None:
        self.assertIsNone(self._export()["image"])

    def test_an_unknown_round_is_a_command_error(self) -> None:
        with self.assertRaises(CommandError):
            call_command(
                "export_playback_round", "--round", "999999", "--out", str(self.out)
            )
