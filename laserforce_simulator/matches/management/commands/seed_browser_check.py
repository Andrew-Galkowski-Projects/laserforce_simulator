"""Seed throwaway data for a browser check, and remove it again.

Two Accounts: A owns four valid Teams, a simulated sandbox Match, two
full-fidelity Single Rounds between the same Teams (one map-less, one on a
generated, fully configured ``ArenaMap``, "Browser Check Arena") and an
unfinished Match (round 1 only,
like a League Match mid-Season); B owns nothing, for cross-Account checks.
``--league`` also gives A "Browser Check League" (career mode, finance on):
two completed Seasons and one active Season, every played Round with player
stats, hand-built by ``_browser_check_league`` (no simulator).
``--clean`` deletes both Accounts, every row they own, the League and its
Matches, the map and its media files. For a worktree's disposable database
(ADR-0004).
"""

import io
from typing import Any

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.db.models import Q, QuerySet
from PIL import Image as PILImage
from PIL import ImageDraw

from core.models import ArenaMap, BaseSightLineConfig
from core.services import (
    compute_and_store_sight_lines,
    delete_map,
    save_zone_config,
    upload_map,
)
from matches.management.commands._browser_check_league import (
    LEAGUE_NAME,
    seed_browser_check_league,
)
from matches.models import GameRound, League, Match
from matches.services import create_sandbox_match, create_sandbox_round
from matches.simulation import BatchSimulator
from teams.forms import GenerateLeagueForm
from teams.models import Player, Team
from teams.services import run_player_generation

EMAIL_A = "browser-a@example.com"
EMAIL_B = "browser-b@example.com"
PASSWORD = "browser-check-pass"

MAP_NAME = "Browser Check Arena"
ZONE_SIZE = 20
MAP_ROWS = 20
MAP_COLS = 30
# Interior wall blocks as (row0, col0, row1, col1), inclusive cell ranges.
WALL_BLOCKS = [
    (4, 8, 7, 9),
    (12, 8, 15, 9),
    (8, 14, 11, 15),
    (4, 20, 7, 21),
    (12, 20, 15, 21),
]
RED_BASE_CELL = (10, 3)
BLUE_BASE_CELL = (9, 26)
BASE_SIGHT_RADIUS = 3


def _zone_grid() -> list[list[int]]:
    """``MAP_ROWS`` x ``MAP_COLS`` cells: 1 = floor, 0 = high wall (border + blocks)."""
    grid = [
        [
            0 if r in (0, MAP_ROWS - 1) or c in (0, MAP_COLS - 1) else 1
            for c in range(MAP_COLS)
        ]
        for r in range(MAP_ROWS)
    ]
    for r0, c0, r1, c1 in WALL_BLOCKS:
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                grid[r][c] = 0
    return grid


def _map_png(grid: list[list[int]]) -> bytes:
    """A PNG of ``grid``: white floor, black walls, one ``ZONE_SIZE`` square per cell."""
    img = PILImage.new("RGB", (MAP_COLS * ZONE_SIZE, MAP_ROWS * ZONE_SIZE), "white")
    draw = ImageDraw.Draw(img)
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if cell == 0:
                x, y = c * ZONE_SIZE, r * ZONE_SIZE
                draw.rectangle(
                    [x, y, x + ZONE_SIZE - 1, y + ZONE_SIZE - 1], fill="black"
                )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _base_px(cell: tuple[int, int]) -> tuple[int, int]:
    """``(x_px, y_px)`` of a cell's centre."""
    r, c = cell
    return c * ZONE_SIZE + ZONE_SIZE // 2, r * ZONE_SIZE + ZONE_SIZE // 2


def _cells_near(grid: list[list[int]], cell: tuple[int, int]) -> list[list[int]]:
    """Floor cells within ``BASE_SIGHT_RADIUS`` (Manhattan) of ``cell``."""
    br, bc = cell
    return [
        [r, c]
        for r, row in enumerate(grid)
        for c, value in enumerate(row)
        if value == 1 and abs(r - br) + abs(c - bc) <= BASE_SIGHT_RADIUS
    ]


def create_browser_check_map() -> ArenaMap:
    """Upload the generated PNG and configure zones, bases and sight lines."""
    grid = _zone_grid()
    upload = SimpleUploadedFile(
        "browser_check_arena.png", _map_png(grid), content_type="image/png"
    )
    arena_map = upload_map(MAP_NAME, upload)
    bases = []
    for base_type, cell in (("red", RED_BASE_CELL), ("blue", BLUE_BASE_CELL)):
        x_px, y_px = _base_px(cell)
        bases.append({"type": base_type, "x_px": x_px, "y_px": y_px})
    save_zone_config(
        arena_map,
        zone_size=ZONE_SIZE,
        zones=grid,
        wall_meta={},
        elevation=None,
        red_spawn=None,
        blue_spawn=None,
        bases=bases,
    )
    compute_and_store_sight_lines(arena_map, ZONE_SIZE)
    for base_type, cell in (("red", RED_BASE_CELL), ("blue", BLUE_BASE_CELL)):
        BaseSightLineConfig.objects.update_or_create(
            arena_map=arena_map,
            base_type=base_type,
            zone_size=ZONE_SIZE,
            defaults={"visible_cells": _cells_near(grid, cell)},
        )
    return arena_map


def _deleted(queryset: QuerySet) -> int:
    """Delete ``queryset`` and return how many of its own model's rows went.

    ``delete()[0]`` is the total across every cascaded model (a GameRound
    takes its GameEvents and PlayerRoundStates along), not the labelled one.
    """
    return queryset.delete()[1].get(queryset.model._meta.label, 0)


class Command(BaseCommand):
    help = "Seed (or --clean) two throwaway Accounts and Account A's Teams and Matches."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--clean", action="store_true", help="delete the seeded data instead"
        )
        parser.add_argument(
            "--league",
            action="store_true",
            help="also seed Account A a League (2 completed Seasons, 1 active)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["clean"]:
            self._clean()
        else:
            self._seed(league=options["league"])

    def _seed(self, *, league: bool = False) -> None:
        user_model = get_user_model()
        if (
            user_model.objects.filter(email__in=[EMAIL_A, EMAIL_B]).exists()
            or ArenaMap.objects.filter(name=MAP_NAME).exists()
            or (league and League.objects.filter(name=LEAGUE_NAME).exists())
        ):
            raise CommandError("Already seeded: run seed_browser_check --clean first.")

        a = user_model.objects.create_user(email=EMAIL_A, password=PASSWORD)
        user_model.objects.create_user(email=EMAIL_B, password=PASSWORD)

        form = GenerateLeagueForm(
            {"num_teams": "4", "players_per_team": "6", "mean": 50, "std_dev": 15}
        )
        if not form.is_valid():
            raise CommandError(f"Team generation form rejected: {form.errors}")
        run_player_generation(form.cleaned_data, manager=a)
        red, blue, third, fourth = Team.objects.filter(manager=a).order_by("id")[:4]

        match = create_sandbox_match(red, blue, "friendly", arena_map=None, user=a)
        # A sandbox Round persists at "scores"; the GEN-01 lazy upgrade replays
        # it off its seed to "full". Both Single Rounds get events, so Compare
        # Rounds has a points chart on both sides; the mapped one also feeds
        # Playback's map and the heatmap.
        game_round = BatchSimulator().ensure_fidelity(
            create_sandbox_round(third, fourth, arena_map=None, user=a), "full"
        )
        arena_map = create_browser_check_map()
        mapped_round = BatchSimulator().ensure_fidelity(
            create_sandbox_round(third, fourth, arena_map=arena_map, user=a), "full"
        )
        unfinished = Match.objects.create(
            team_red=red,
            team_blue=fourth,
            match_type="friendly",
            red_round1_points=41000,
            blue_round1_points=39000,
            manager=a,
        )
        # save() derives a winner and completion; force the mid-Season state back.
        Match.objects.filter(pk=unfinished.pk).update(winner=None, is_completed=False)

        self.stdout.write(f"Accounts {EMAIL_A} (data) and {EMAIL_B} (empty)")
        self.stdout.write(f"Password: {PASSWORD}")
        self.stdout.write(
            f"Match {match.pk}, Single Round {game_round.pk}, "
            f"unfinished Match {unfinished.pk}"
        )
        self.stdout.write(
            f"Map {arena_map.pk} ({MAP_NAME}), mapped Single Round {mapped_round.pk}"
        )
        if league:
            seeded, seasons = seed_browser_check_league(a, [red, blue, third, fourth])
            self.stdout.write(
                f"League {seeded.pk} ({LEAGUE_NAME}), Seasons "
                + ", ".join(str(s.pk) for s in seasons)
                + " (completed, completed, active)"
            )

    @transaction.atomic
    def _clean(self) -> None:
        users = get_user_model().objects.filter(email__in=[EMAIL_A, EMAIL_B])
        # League.manager and Match.season are SET_NULL, so deleting the Account
        # would orphan the League and its Matches: delete them explicitly (the
        # League cascades its Seasons). An Unmanaged League of the seed's name
        # (its Account deleted by hand) goes too. Not teardown_league: its
        # orphan-Team sweep would delete A's Teams and skew the counts below.
        leagues = League.objects.filter(
            Q(manager__in=users) | Q(name=LEAGUE_NAME, manager__isnull=True)
        )
        league_count = leagues.count()
        Match.objects.filter(season__league__in=leagues).delete()
        leagues.delete()
        # Reported only when there was one, so a League-less clean prints the
        # same line as before --league existed.
        counts = {"Leagues": league_count} if league_count else {}
        counts |= {
            "Single Rounds": _deleted(GameRound.objects.filter(manager__in=users)),
            "Matches": _deleted(Match.objects.filter(manager__in=users)),
            "Players": _deleted(Player.objects.filter(team__manager__in=users)),
            "Teams": _deleted(Team.objects.filter(manager__in=users)),
            "Accounts": _deleted(users),
        }
        # delete_map removes the image and processed cache once this commits.
        maps = list(ArenaMap.objects.filter(name=MAP_NAME))
        for arena_map in maps:
            delete_map(arena_map)
        counts["Maps"] = len(maps)
        self.stdout.write(
            "removed " + ", ".join(f"{name}: {n}" for name, n in counts.items())
        )
