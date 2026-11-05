"""Measure how players move and choose goals over seeded rounds (MECH-07 retro).

A thin wrapper over ``matches.round_probe``. The map fixture loads inside a rolled-back
transaction, so nothing is written. Run it on two checkouts to compare them: see
``management/commands/CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from matches.management.commands.lfstats_calibrate import (
    DEFAULT_MAP_FIXTURE,
    _map_context,
)
from matches.round_probe import ProbeResult, run_probe


def _percent(share: float) -> str:
    return f"{share * 100:.1f}"


def format_result(result: ProbeResult) -> list[str]:
    """The probe's numbers as ASCII lines, one block per Role, then goal sharing."""
    lines = []
    for role, measures in result.roles.items():
        modes = ", ".join(
            f"{mode} {_percent(share)}" for mode, share in measures.mode_share.items()
        )
        goals = ", ".join(
            f"{source} {_percent(share)}"
            for source, share in measures.goal_share.items()
        )
        lines += [
            role,
            f"  Mode %: {modes or 'none'}",
            f"  Goals %: {goals}",
            f"  Not Active %: {_percent(measures.not_active_share)}",
            f"  Squares to {measures.target}: {measures.squares_to_target:.1f}",
            f"  Mean points: {measures.mean_points:.0f}",
        ]
    shares = list(result.sharing_by_seed.values())
    lines.append(
        f"Goal sharing %: mean {_percent(sum(shares) / len(shares))}, "
        f"min {_percent(min(shares))}, max {_percent(max(shares))}, "
        f"{sum(1 for share in shares if share >= 0.15)} of {len(shares)} rounds "
        "at 15 or more"
    )
    lines.append(
        "Per seed: "
        + ", ".join(
            f"{seed}={_percent(share)}"
            for seed, share in result.sharing_by_seed.items()
        )
    )
    return lines


class Command(BaseCommand):
    help = "Measure Modes, goal sources, distances and goal sharing over seeded rounds."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--seeds",
            type=int,
            default=12,
            help="play seeds 1 to N, one round each (default: 12)",
        )
        parser.add_argument(
            "--map-fixture",
            dest="map_fixture",
            default=str(DEFAULT_MAP_FIXTURE),
            help="map fixture JSON (default: the Syracuse fixture)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["seeds"] < 1:
            raise CommandError("--seeds must be at least 1.")
        movement_ctx = _map_context(options["map_fixture"])
        result = run_probe(movement_ctx, range(1, options["seeds"] + 1))
        for line in format_result(result):
            self.stdout.write(line)
