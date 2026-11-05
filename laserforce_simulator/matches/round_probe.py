"""Measure how players move and choose goals over seeded rounds (MECH-07 retro).

The numbers behind a simulation behaviour decision, ``round_probe``'s engine: per
Role the time in each Mode, where goals come from, the time not Active, the distance
to the Role's target and mean points, plus goal sharing per seed. Serial and read-only:
it observes ``choose_goal_cell`` without drawing from the RNG, so a seed plays the same
round as it would unobserved.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from matches.simulation import BatchSimulator, entrypoints
from matches.simulation.entrypoints import _SIMULATION_STATS, _PlayerData
from teams.models import Player

_TARGETS: dict[str, tuple[str, tuple[str, ...], bool]] = {
    # role: (label, the roles measured to, whether they are on the other team)
    "commander": ("enemy Medic/Ammo", ("medic", "ammo"), True),
    "heavy": ("enemy Medic/Ammo", ("medic", "ammo"), True),
    "scout": ("enemy Medic/Ammo", ("medic", "ammo"), True),
    "medic": ("own Heavy", ("heavy",), False),
    "ammo": ("own Heavy", ("heavy",), False),
}
_ROSTER_SLOTS = ("commander", "heavy", "scout", "scout", "medic", "ammo")
_GOAL_SOURCES = ("action", "positioning", "override")


@dataclass(frozen=True)
class RoleMeasures:
    """One Role's numbers; every share is 0.0 to 1.0."""

    mode_share: dict[str, float]
    goal_share: dict[str, float]
    not_active_share: float
    target: str
    squares_to_target: float
    mean_points: float


@dataclass(frozen=True)
class ProbeResult:
    roles: dict[str, RoleMeasures]
    sharing_by_seed: dict[int, float]


def goal_sharing_share(goals_by_team_tick: Mapping) -> float:
    """The share of team-ticks where 2+ teammates hold the same goal.

    ``goals_by_team_tick`` maps each team-tick to ``{tag_id: goal}``.
    """
    if not goals_by_team_tick:
        return 0.0
    shared = sum(
        1
        for goals in goals_by_team_tick.values()
        if any(count > 1 for count in Counter(goals.values()).values())
    )
    return shared / len(goals_by_team_tick)


def _default_roster(team: str) -> list[tuple[str, _PlayerData]]:
    """Six Players on the model's default stats, so a Role's numbers come from the
    Role and not from a generated roster."""
    stats = {name: Player._meta.get_field(name).default for name in _SIMULATION_STATS}
    return [
        (role, _PlayerData(f"{team}-{slot}", f"{team} {role} {slot}", stats))
        for slot, role in enumerate(_ROSTER_SLOTS)
    ]


def _goal_source(player, goal) -> str:
    committed = player._committed_goal
    if committed is None or goal != committed[0]:
        return "override"
    return "action" if committed[1] else "positioning"


def _squares_to_target(player, all_alive: list) -> int | None:
    """Manhattan squares to the nearest placed target, or None with none."""
    _, roles, enemy = _TARGETS[player.role]
    distances = [
        abs(other.cell_row - player.cell_row) + abs(other.cell_col - player.cell_col)
        for other in all_alive
        if other.role in roles
        and (other.team_color != player.team_color) == enemy
        and other.cell_row is not None
    ]
    return min(distances, default=None)


def _share(counter: Counter, keys: Iterable[str] | None = None) -> dict[str, float]:
    total = sum(counter.values())
    names = sorted(counter) if keys is None else keys
    return {name: counter[name] / total if total else 0.0 for name in names}


def run_probe(movement_ctx, seeds: Iterable[int]) -> ProbeResult:
    """Play one round per seed on ``movement_ctx`` and measure it."""
    modes: dict[str, Counter] = defaultdict(Counter)
    sources: dict[str, Counter] = defaultdict(Counter)
    squares: dict[str, list[int]] = defaultdict(list)
    points: dict[str, list[int]] = defaultdict(list)
    active_ticks: Counter = Counter()
    player_rounds: Counter = Counter()
    sharing_by_seed: dict[int, float] = {}
    goals_by_team_tick: dict = {}
    real_choose = entrypoints.choose_goal_cell

    def observed_choose(
        player,
        all_alive,
        spawn_cells,
        movement_ctx=None,
        intended_action="",
        second=0.0,
        time_domain="seconds",
    ):
        goal = real_choose(
            player,
            all_alive,
            spawn_cells,
            movement_ctx,
            intended_action,
            second,
            time_domain,
        )
        if goal is not None:
            key = (second, player.team_color)
            goals_by_team_tick.setdefault(key, {})[player.tag_id] = goal
        sources[player.role][_goal_source(player, goal)] += 1
        if player.cell_row is not None:
            distance = _squares_to_target(player, all_alive)
            if distance is not None:
                squares[player.role].append(distance)
        return goal

    entrypoints.choose_goal_cell = observed_choose
    try:
        for seed in seeds:
            goals_by_team_tick.clear()
            random.seed(seed)
            _, reds, blues = BatchSimulator()._simulate_round(
                _default_roster("red"),
                _default_roster("blue"),
                movement_ctx=movement_ctx,
            )
            sharing_by_seed[seed] = goal_sharing_share(goals_by_team_tick)
            for player in reds + blues:
                role = player.role
                modes[role].update(
                    {str(mode): n for mode, n in player.mode_ticks.items()}
                )
                points[role].append(player.counters.points_scored)
                active_ticks[role] += player.ticks_active
                player_rounds[role] += 1
    finally:
        entrypoints.choose_goal_cell = real_choose

    ticks = BatchSimulator.ROUND_TICKS
    return ProbeResult(
        roles={
            role: RoleMeasures(
                mode_share=_share(modes[role]),
                goal_share=_share(sources[role], _GOAL_SOURCES),
                not_active_share=1.0
                - active_ticks[role] / (ticks * player_rounds[role]),
                target=_TARGETS[role][0],
                squares_to_target=(
                    sum(squares[role]) / len(squares[role]) if squares[role] else 0.0
                ),
                mean_points=sum(points[role]) / len(points[role]),
            )
            for role in sorted(player_rounds)
        },
        sharing_by_seed=sharing_by_seed,
    )
