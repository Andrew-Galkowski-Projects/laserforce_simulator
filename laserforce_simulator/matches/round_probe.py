"""Measure how players move, choose goals and use Actions over seeded rounds.

The numbers behind a simulation behaviour decision, ``round_probe``'s engine: per
Role the time in each Mode, where goals come from, the time not Active, the distance
to the Role's target, mean points, what the Role does with its Actions, Tags made and
Eliminations, plus goal sharing per seed. Each measure is pooled over the seeds and kept per seed. Serial and
read-only: it observes ``plan_action`` and ``choose_goal_cell`` without drawing from
the RNG, so a seed plays the same round as it would unobserved.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from matches.sim_helpers.combat import _CHOICES
from matches.sim_helpers.time_constants import SURVIVED_SENTINEL
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
# A hide roll stops the player on the tick it lands, before is_hiding reads True.
_STILL_ROLLS = ("capture_base", "hide")
_RESUPPLY_PLANS = ("resupply_ammo", "resupply_lives")


@dataclass(frozen=True)
class RoleMeasures:
    """One Role's numbers; every share is 0.0 to 1.0."""

    mode_share: dict[str, float]
    goal_share: dict[str, float]
    not_active_share: float
    target: str
    squares_to_target: float
    mean_points: float
    action_share: dict[str, float]
    empty_roll_share: dict[str, float]
    still_share: float
    hold_share: float
    hold_run: float
    tags_per_round: float
    tags_made_per_round: float
    resupplies_per_round: float
    captures_per_round: float
    eliminated_share: float
    # The mean tick of the player-rounds that ended Eliminated; None with none.
    elimination_tick: float | None


@dataclass(frozen=True)
class ProbeResult:
    """``roles`` pools every seed; ``by_seed`` holds the same measures per seed."""

    roles: dict[str, RoleMeasures]
    sharing_by_seed: dict[int, float]
    by_seed: dict[int, dict[str, RoleMeasures]]
    team_captures_by_seed: dict[int, dict[str, int]]


@dataclass
class _RoleTally:
    """One Role's raw counts, over one round or pooled over several."""

    modes: Counter = field(default_factory=Counter)
    sources: Counter = field(default_factory=Counter)
    squares: list[int] = field(default_factory=list)
    points: list[int] = field(default_factory=list)
    ticks_active: int = 0
    player_rounds: int = 0
    active_rolls: Counter = field(default_factory=Counter)
    empty_rolls: Counter = field(default_factory=Counter)
    still_ticks: int = 0
    hold_ticks: int = 0
    hold_runs: list[int] = field(default_factory=list)
    tag_plans: int = 0
    tags_made: int = 0
    resupply_plans: int = 0
    captures: int = 0
    elimination_ticks: list[int] = field(default_factory=list)

    def add(self, other: _RoleTally) -> None:
        self.modes.update(other.modes)
        self.sources.update(other.sources)
        self.squares += other.squares
        self.points += other.points
        self.ticks_active += other.ticks_active
        self.player_rounds += other.player_rounds
        self.active_rolls.update(other.active_rolls)
        self.empty_rolls.update(other.empty_rolls)
        self.still_ticks += other.still_ticks
        self.hold_ticks += other.hold_ticks
        self.hold_runs += other.hold_runs
        self.tag_plans += other.tag_plans
        self.tags_made += other.tags_made
        self.resupply_plans += other.resupply_plans
        self.captures += other.captures
        self.elimination_ticks += other.elimination_ticks


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


def _ratio(part: float, whole: float) -> float:
    return part / whole if whole else 0.0


def _share(counter: Counter, keys: Iterable[str] | None = None) -> dict[str, float]:
    total = sum(counter.values())
    names = sorted(counter) if keys is None else keys
    return {name: _ratio(counter[name], total) for name in names}


def _measures(role: str, tally: _RoleTally) -> RoleMeasures:
    round_ticks = BatchSimulator.ROUND_TICKS
    active_rolls = sum(tally.active_rolls.values())
    return RoleMeasures(
        mode_share=_share(tally.modes),
        goal_share=_share(tally.sources, _GOAL_SOURCES),
        not_active_share=1.0 - tally.ticks_active / (round_ticks * tally.player_rounds),
        target=_TARGETS[role][0],
        squares_to_target=_ratio(sum(tally.squares), len(tally.squares)),
        mean_points=sum(tally.points) / len(tally.points),
        action_share=_share(tally.active_rolls, _CHOICES),
        empty_roll_share={
            action: _ratio(tally.empty_rolls[action], tally.active_rolls[action])
            for action in _CHOICES
        },
        still_share=_ratio(tally.still_ticks, active_rolls),
        hold_share=_ratio(tally.hold_ticks, active_rolls),
        hold_run=_ratio(sum(tally.hold_runs), len(tally.hold_runs)),
        tags_per_round=tally.tag_plans / tally.player_rounds,
        tags_made_per_round=tally.tags_made / tally.player_rounds,
        resupplies_per_round=tally.resupply_plans / tally.player_rounds,
        captures_per_round=tally.captures / tally.player_rounds,
        eliminated_share=len(tally.elimination_ticks) / tally.player_rounds,
        elimination_tick=(
            sum(tally.elimination_ticks) / len(tally.elimination_ticks)
            if tally.elimination_ticks
            else None
        ),
    )


def _role_measures(tallies: Mapping[str, _RoleTally]) -> dict[str, RoleMeasures]:
    """Measures for each Role with a finished player; a goal spy can see others."""
    return {
        role: _measures(role, tallies[role])
        for role in sorted(tallies)
        if tallies[role].player_rounds
    }


def run_probe(movement_ctx, seeds: Iterable[int]) -> ProbeResult:
    """Play one round per seed on ``movement_ctx`` and measure it."""
    tallies: dict[str, _RoleTally] = defaultdict(_RoleTally)
    open_runs: Counter = Counter()
    goals_by_team_tick: dict = {}
    tallies_by_seed: dict[int, dict[str, _RoleTally]] = {}
    sharing_by_seed: dict[int, float] = {}
    real_choose = entrypoints.choose_goal_cell
    real_plan = entrypoints.plan_action
    real_capture = entrypoints._capture_base_shared
    team_captures: dict[str, int] = {}
    team_captures_by_seed: dict[int, dict[str, int]] = {}

    def observed_capture(player, *args, **kwargs) -> bool:
        # Only a live capture comes through here; the round-end award does not.
        captured = real_capture(player, *args, **kwargs)
        if captured:
            tallies[player.role].captures += 1
            team_captures[player.team_color] += 1
        return captured

    def close_run(player) -> None:
        length = open_runs.pop(id(player), 0)
        if length:
            tallies[player.role].hold_runs.append(length)

    def observed_plan(
        player,
        all_alive,
        second,
        movement_ctx=None,
        *,
        save_player=None,
        time_domain="seconds",
    ):
        plans = real_plan(
            player,
            all_alive,
            second,
            movement_ctx,
            save_player=save_player,
            time_domain=time_domain,
        )
        tally = tallies[player.role]
        for plan in plans:
            if plan["type"] == "tag":
                tally.tag_plans += 1
            elif plan["type"] in _RESUPPLY_PLANS:
                tally.resupply_plans += 1
        active = player.is_active_at(second)
        if active:
            tally.active_rolls[player.last_chosen_action] += 1
            if not plans:
                tally.empty_rolls[player.last_chosen_action] += 1
            if (
                player.is_hiding
                or player.is_holding
                or player.last_chosen_action in _STILL_ROLLS
            ):
                tally.still_ticks += 1
        if not player.is_holding:
            close_run(player)
        elif active:
            tally.hold_ticks += 1
            open_runs[id(player)] += 1
        return plans

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
        tallies[player.role].sources[_goal_source(player, goal)] += 1
        if player.cell_row is not None:
            distance = _squares_to_target(player, all_alive)
            if distance is not None:
                tallies[player.role].squares.append(distance)
        return goal

    entrypoints.choose_goal_cell = observed_choose
    entrypoints.plan_action = observed_plan
    entrypoints._capture_base_shared = observed_capture
    try:
        for seed in seeds:
            tallies = defaultdict(_RoleTally)
            team_captures = {"red": 0, "blue": 0}
            team_captures_by_seed[seed] = team_captures
            goals_by_team_tick.clear()
            random.seed(seed)
            _, reds, blues = BatchSimulator()._simulate_round(
                _default_roster("red"),
                _default_roster("blue"),
                movement_ctx=movement_ctx,
            )
            sharing_by_seed[seed] = goal_sharing_share(goals_by_team_tick)
            for player in reds + blues:
                close_run(player)
                tally = tallies[player.role]
                tally.modes.update(
                    {str(mode): n for mode, n in player.mode_ticks.items()}
                )
                tally.points.append(player.counters.points_scored)
                tally.tags_made += player.counters.tags_made
                tally.ticks_active += player.ticks_active
                tally.player_rounds += 1
                if player.was_eliminated_at < SURVIVED_SENTINEL:
                    tally.elimination_ticks.append(player.was_eliminated_at)
            tallies_by_seed[seed] = tallies
    finally:
        entrypoints.choose_goal_cell = real_choose
        entrypoints.plan_action = real_plan
        entrypoints._capture_base_shared = real_capture

    pooled: dict[str, _RoleTally] = defaultdict(_RoleTally)
    for seed_tallies in tallies_by_seed.values():
        for role, tally in seed_tallies.items():
            pooled[role].add(tally)
    return ProbeResult(
        roles=_role_measures(pooled),
        sharing_by_seed=sharing_by_seed,
        by_seed={
            seed: _role_measures(seed_tallies)
            for seed, seed_tallies in tallies_by_seed.items()
        },
        team_captures_by_seed=team_captures_by_seed,
    )
