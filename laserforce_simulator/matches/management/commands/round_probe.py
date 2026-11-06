"""Measure how players move, choose goals and use Actions over seeded rounds.

A thin wrapper over ``matches.round_probe``. The map fixture loads inside a rolled-back
transaction, so nothing is written. Run it on two checkouts to compare them: see
``management/commands/CLAUDE.md``.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from django.core.management.base import BaseCommand, CommandError, CommandParser

from matches.management.commands.lfstats_calibrate import (
    DEFAULT_MAP_FIXTURE,
    _map_context,
)
from matches.round_probe import ProbeResult, RoleMeasures, run_probe


def _percent(share: float) -> str:
    return f"{share * 100:.1f}"


def _spread(mean: float, per_seed: Iterable[float], form: str = ".1f") -> str:
    """``mean (min-max)``: the pooled mean and the range across the seeds."""
    values = list(per_seed)
    return f"{mean:{form}} ({min(values):{form}}-{max(values):{form}})"


def _percent_spread(share: float, per_seed: Iterable[float]) -> str:
    return _spread(share * 100, [value * 100 for value in per_seed])


def _role_lines(
    role: str, pooled: RoleMeasures, seeds: list[RoleMeasures]
) -> list[str]:
    """One Role's block. ``seeds`` holds its measures in each seed it played."""

    def number(read: Callable[[RoleMeasures], float], form: str = ".1f") -> str:
        return _spread(read(pooled), [read(seed) for seed in seeds], form)

    def percent(read: Callable[[RoleMeasures], float]) -> str:
        return _percent_spread(read(pooled), [read(seed) for seed in seeds])

    modes = ", ".join(
        f"{mode} "
        + _percent_spread(share, [seed.mode_share.get(mode, 0.0) for seed in seeds])
        for mode, share in pooled.mode_share.items()
    )
    goals = ", ".join(
        f"{source} "
        + _percent_spread(share, [seed.goal_share[source] for seed in seeds])
        for source, share in pooled.goal_share.items()
    )
    rolled = [action for action, share in pooled.action_share.items() if share > 0.0]
    actions = ", ".join(
        f"{action} "
        + _percent_spread(
            pooled.action_share[action], [seed.action_share[action] for seed in seeds]
        )
        for action in rolled
    )
    empties = ", ".join(
        f"{action} "
        + _percent_spread(
            pooled.empty_roll_share[action],
            [
                seed.empty_roll_share[action]
                for seed in seeds
                if seed.action_share[action] > 0.0
            ],
        )
        for action in rolled
    )
    return [
        role,
        f"  Mode %: {modes or 'none'}",
        f"  Goals %: {goals}",
        f"  Not Active %: {percent(lambda m: m.not_active_share)}",
        f"  Squares to {pooled.target}: {number(lambda m: m.squares_to_target)}",
        f"  Mean points: {number(lambda m: m.mean_points, '.0f')}",
        f"  Action %: {actions or 'none'}",
        f"  Empty rolls %: {empties or 'none'}",
        f"  Still %: {percent(lambda m: m.still_share)}",
        f"  Hold %: {percent(lambda m: m.hold_share)}",
        f"  Hold run, ticks: {number(lambda m: m.hold_run)}",
        f"  Tags per round (planned Shots): {number(lambda m: m.tags_per_round)}",
        f"  Resupplies per round (planned): "
        f"{number(lambda m: m.resupplies_per_round)}",
        f"  Live captures per round: {number(lambda m: m.captures_per_round)}",
    ]


def _team_captures(result: ProbeResult, team: str) -> str:
    counts = [seed[team] for seed in result.team_captures_by_seed.values()]
    return f"{team} {sum(counts) / len(counts):.1f} ({min(counts)}-{max(counts)})"


def format_result(result: ProbeResult) -> list[str]:
    """The probe's numbers as ASCII lines, one block per Role, then the team-wide ones.

    Each measure reads ``mean (min-max)``: the mean pools every seed, the range is
    across the seeds.
    """
    lines = []
    for role, pooled in result.roles.items():
        seeds = [roles[role] for roles in result.by_seed.values() if role in roles]
        lines += _role_lines(role, pooled, seeds)
    lines.append(
        "Live captures per round: "
        f"{_team_captures(result, 'red')}, {_team_captures(result, 'blue')}"
    )
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
    help = (
        "Measure Modes, goal sources, distances, Actions, Holds, captures and goal "
        "sharing over seeded rounds."
    )

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
