"""Export one Round's Playback data as a JSON file, for a throwaway prototype page.

RV-06 and RV-07 each picked their Playback map look from a published prototype built on a
seeded Round, and each hand-wrote this export (RV-07 retro). The file holds what the
Playback screen loads, in one place a prototype page can inline:

- ``round``: the GameRound id.
- ``playback``: ``round_playback`` (snapshots per player, the map routes and role limits).
- ``events``: the timeline's events without ``movement`` (tags, misses, missiles,
  resupplies, base captures, specials), as ``/api/rounds/<id>/timeline/`` sends them.
- ``bases``, ``zone_size``: the map's zone config (``[]`` and ``null`` for a map-less Round).
- ``image``: the processed map PNG as a ``data:`` URI, ``null`` when the map has no image.

Usage::

    python manage.py seed_browser_check --syracuse      # prints the review Round id
    python manage.py export_playback_round --round 5 --out <scratchpad>/round.json
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.core.serializers.json import DjangoJSONEncoder

from core.services import get_zone_config, processed_image_path
from matches.models import GameRound
from matches.playback import round_playback
from matches.services import round_timeline


def _image_uri(game_round: GameRound) -> str | None:
    arena_map = game_round.arena_map
    if arena_map is None or not arena_map.image:
        return None
    png = processed_image_path(arena_map).read_bytes()
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


class Command(BaseCommand):
    help = (
        "Write one Round's Playback data (snapshots, events, bases, map image) as JSON."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--round", type=int, required=True, help="the GameRound id")
        parser.add_argument("--out", required=True, help="the JSON file to write")

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            game_round = GameRound.objects.select_related("arena_map").get(
                pk=options["round"]
            )
        except GameRound.DoesNotExist:
            raise CommandError(f"No Round {options['round']}.") from None
        arena_map = game_round.arena_map
        zone = get_zone_config(arena_map) if arena_map is not None else None
        data = {
            "round": game_round.pk,
            "playback": round_playback(game_round),
            "events": [
                e
                for e in round_timeline(game_round)["events"]
                if e["event_type"] != "movement"
            ],
            "bases": zone["bases"] if zone else [],
            "zone_size": zone["zone_size"] if zone else None,
            "image": _image_uri(game_round),
        }
        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, cls=DjangoJSONEncoder), encoding="utf-8")
        self.stdout.write(
            f"Round {game_round.pk}: {len(data['events'])} events, "
            f"{len(data['bases'])} bases -> {out}"
        )
