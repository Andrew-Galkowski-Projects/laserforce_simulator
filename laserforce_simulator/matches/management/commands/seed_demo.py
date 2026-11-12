"""A demo Account that owns one simulated map Round, for showing a live site.

Usage::

    python manage.py seed_demo <map fixture.json> [--email demo@laserforce.demo]

The map is the ``ArenaMap`` named in the fixture (``data/maps/syracuse.json``,
or one written by ``scripts/lfstats_import/export_map_fixture.py``). A missing
map is created from the fixture; an existing one with no zone config (a live
site's uploaded map) gets the fixture's configs; a configured one is used as
it is. The Account is created on the first run, and every run sets it a new
password, generates its two Teams if it has fewer, plays one more Round at
full fidelity, and prints::

    Email: <email>
    Password: <password>
    Round: <id> on <map name>

Steps in ``docs/self-hosting.md`` (*Day two*). CAL-04 retro.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import ArenaMap, MapZoneConfig
from matches.lfstats_calibration import load_map_fixture
from matches.services import create_sandbox_round
from matches.simulation import BatchSimulator
from teams.forms import GenerateLeagueForm
from teams.models import Team
from teams.services import run_player_generation

DEMO_EMAIL = "demo@laserforce.demo"
_TEAM_FORM = {"num_teams": "2", "players_per_team": "6", "mean": 50, "std_dev": 15}


def _demo_map(path: Path) -> ArenaMap:
    name = json.loads(path.read_text(encoding="utf-8"))["name"]
    arena_map = ArenaMap.objects.filter(name=name).order_by("pk").first()
    if arena_map is None:
        return load_map_fixture(path)
    if not MapZoneConfig.objects.filter(arena_map=arena_map, confirmed=True).exists():
        return load_map_fixture(path, arena_map=arena_map)
    return arena_map


class Command(BaseCommand):
    help = "Create or refresh a demo Account with one simulated map Round."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "fixture", help="map fixture JSON (load_map_fixture format)"
        )
        parser.add_argument("--email", default=DEMO_EMAIL)

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["fixture"])
        if not path.is_file():
            raise CommandError(f"No map fixture at {path}")
        email = options["email"]
        password = secrets.token_urlsafe(12)
        with transaction.atomic():
            arena_map = _demo_map(path)
            demo = get_user_model().objects.filter(email=email).first()
            if demo is None:
                demo = get_user_model().objects.create_user(email=email)
            demo.set_password(password)
            demo.save()
            if Team.objects.filter(manager=demo).count() < 2:
                form = GenerateLeagueForm(_TEAM_FORM)
                if not form.is_valid():
                    raise CommandError(f"Team generation form rejected: {form.errors}")
                run_player_generation(form.cleaned_data, manager=demo)
        red, blue = Team.objects.filter(manager=demo).order_by("id")[:2]
        game_round = BatchSimulator().ensure_fidelity(
            create_sandbox_round(red, blue, arena_map=arena_map, user=demo), "full"
        )
        self.stdout.write(f"Email: {email}")
        self.stdout.write(f"Password: {password}")
        self.stdout.write(f"Round: {game_round.pk} on {arena_map.name}")
