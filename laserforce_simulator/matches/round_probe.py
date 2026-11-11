"""Measure how players move, choose goals and use Actions over seeded rounds.

The numbers behind a simulation behaviour decision, ``round_probe``'s engine: per
Role the time in each Mode, where goals come from, the time not Active, the distance
to the Role's target, mean points, what the Role does with its Actions, Tags made and
Eliminations, plus goal sharing per seed. CAL-02 (#567) added the tag measures: times
tagged, shots fired, Downs given and taken, Active share, Tags per Active minute,
Reaction and Follow-up shots, enemies on the floor per Active roll, shot cadence, and the
who-tags-whom and who-Downs-whom matrices. CAL-03a (#589) added the support measures:
survival by minute, minutes in play, Tag Downs per minute in play, lives lost by
cause, life Resupplies received, doubles, hit ratio, shot-first, Resupply visits and
Tags per round. Each measure is pooled over the rounds and kept per round. Read-only:
it observes ``plan_action``, ``choose_goal_cell``, the capture, ``resolve_shot``, the
missile launch and the two Resupply events without drawing from the RNG, so a seed plays the same
round as it would unobserved. One round is one task, so ``workers`` processes give
the serial numbers.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from math import sqrt
from statistics import median, stdev
from typing import Iterable, Mapping, Sequence

from matches.sim_helpers import shot as shot_module
from matches.sim_helpers.combat import _CHOICES
from matches.sim_helpers.parallel_worker import worker_django_init
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.time_constants import SURVIVED_SENTINEL, TICK_SECONDS
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

# A lineup: the red and the blue roster, each ``(role, _PlayerData)`` pairs.
Lineup = tuple[Sequence[tuple[str, _PlayerData]], Sequence[tuple[str, _PlayerData]]]


_ROLES = ("commander", "heavy", "scout", "ammo", "medic")
# Shot cadence bins (ticks between a player's consecutive shots): upper bound, label.
_CADENCE_BINS: tuple[tuple[int, str], ...] = (
    (1, "<=1t"),
    (2, "2t"),
    (4, "3-4t"),
    (10, "5-10t"),
    (20, "11-20t"),
    (60, "21-60t"),
    (120, "61-120t"),
    (10**9, "120t+"),
)
TICKS_PER_MINUTE = 120
# CAL-03a (#589) measure windows, in ticks (a tick is 0.5 s).
SURVIVAL_MINUTES = (5, 7, 9, 11)
DOUBLE_PAIR_TICKS = 2  # a life + shot pair to one receiver within 1 s is a double
VISIT_GAP_TICKS = 30  # Resupplies with no gap above 15 s are one visit
SHOT_FIRST_TICKS = 4  # the defender Tagged its attacker in the 2 s before the Down


@dataclass(frozen=True)
class Measure:
    """A per-player-round number: its mean and its standard error over the
    player-rounds (0.0 with fewer than two)."""

    mean: float
    se: float


@dataclass(frozen=True)
class Cadence:
    """Ticks between a player's consecutive shots: the median (None with no gap)
    and the share of the gaps in each bin of ``_CADENCE_BINS``."""

    median: float | None
    bin_share: dict[str, float]
    gaps: int


@dataclass(frozen=True)
class VisitMeasures:
    """CAL-03a: a receiver's Resupply visits (Resupplies with no gap above
    ``VISIT_GAP_TICKS``). ``per_round`` is over player-rounds, the per-visit
    numbers over visits and the gap (seconds, to the next visit) over gaps;
    ``median_gap_seconds`` is None with no gap."""

    per_round: Measure
    resupplies_per_visit: Measure
    doubles_per_visit: Measure
    gap_seconds: Measure
    median_gap_seconds: float | None


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
    # CAL-02: the tag measures, per player-round.
    times_tagged: Measure
    shots_fired: Measure
    hiding_misses: Measure
    downs_given: Measure
    downs_taken: Measure
    active_share: Measure
    tags_per_active_minute: Measure
    reaction_shots: Measure
    follow_up_shots: Measure
    enemies_on_floor_per_active_roll: Measure
    # Mode ticks over the round's ticks (``mode_share`` is over the Mode ticks).
    mode_round_share: dict[str, float]
    cadence: Cadence
    # CAL-03a (#589), per player-round: % still in play at each minute of
    # ``SURVIVAL_MINUTES``; minutes in play (to the Elimination or the team wipe);
    # Tag Downs given per minute in play; lives lost per cause (``tag``,
    # ``missile``, ``nuke``); life Resupplies received.
    survival: dict[int, Measure]
    minutes_in_play: Measure
    tag_downs_per_minute_in_play: Measure
    lives_lost: dict[str, Measure]
    life_resupplies: Measure
    # Ratios over player-rounds with a cluster standard error: doubles as a % of
    # the Resupply episodes received (a double is one), Tags made on enemies over
    # times Tagged by enemies, and the % of enemy Tag Downs taken shot first.
    doubles_share: Measure
    hit_ratio: Measure
    shot_first_share: Measure
    visits: VisitMeasures


@dataclass(frozen=True)
class ProbeResult:
    """``roles`` pools every seed; ``by_seed`` holds the same measures per seed.

    ``who_tags_whom`` and ``who_downs_whom`` are per round, both teams: attacker
    role to defender role to the Tags (Downs) landed.
    """

    roles: dict[str, RoleMeasures]
    sharing_by_seed: dict[int, float]
    by_seed: dict[int, dict[str, RoleMeasures]]
    team_captures_by_seed: dict[int, dict[str, int]]
    who_tags_whom: dict[str, dict[str, float]] = field(default_factory=dict)
    who_downs_whom: dict[str, dict[str, float]] = field(default_factory=dict)
    # CAL-03a: Tags made per round, both teams (se over the rounds), and doubles
    # as a % of every Resupply episode received, all roles pooled.
    round_tags: Measure = Measure(0.0, 0.0)
    doubles_share: Measure = Measure(0.0, 0.0)


@dataclass
class _PlayerRound:
    """One player's numbers from one round, read off its counters and the observers."""

    times_tagged: int = 0
    shots_fired: int = 0
    hiding_misses: int = 0
    downs_given: int = 0
    downs_taken: int = 0
    ticks_active: int = 0
    tags_made: int = 0
    reaction_shots: int = 0
    follow_up_shots: int = 0
    active_rolls: int = 0
    enemies_on_floor: int = 0
    # CAL-03a: the Elimination tick (None survived), the ticks in play, lives lost
    # to missiles and nukes, enemy Tags made and taken, enemy Tag Downs taken and
    # how many of them this player shot first, and each Resupply received as
    # (tick, "lives" | "shots") in arrival order.
    eliminated_at: int | None = None
    in_play_ticks: int = 0
    lives_missile: int = 0
    lives_nuke: int = 0
    enemy_tags_made: int = 0
    enemy_times_tagged: int = 0
    enemy_downs_taken: int = 0
    shot_first: int = 0
    resupplies: list[tuple[int, str]] = field(default_factory=list)

    def survived(self, minute: int) -> float:
        """100.0 unless Eliminated before ``minute``'s tick, else 0.0."""
        out_at = self.eliminated_at
        if out_at is not None and out_at < minute * TICKS_PER_MINUTE:
            return 0.0
        return 100.0

    @property
    def minutes_in_play(self) -> float:
        return self.in_play_ticks / TICKS_PER_MINUTE

    @property
    def life_resupplies(self) -> int:
        return sum(1 for _, kind in self.resupplies if kind == "lives")

    def visits(self) -> list[list[tuple[int, str]]]:
        """The Resupplies grouped into visits: no gap above ``VISIT_GAP_TICKS``."""
        groups: list[list[tuple[int, str]]] = []
        for resupply in self.resupplies:
            if groups and resupply[0] - groups[-1][-1][0] <= VISIT_GAP_TICKS:
                groups[-1].append(resupply)
            else:
                groups.append([resupply])
        return groups

    @property
    def active_share(self) -> float:
        return self.ticks_active / BatchSimulator.ROUND_TICKS

    @property
    def tags_per_active_minute(self) -> float:
        return _ratio(self.tags_made, self.ticks_active / TICKS_PER_MINUTE)

    @property
    def enemies_on_floor_per_active_roll(self) -> float:
        return _ratio(self.enemies_on_floor, self.active_rolls)


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
    rows: list[_PlayerRound] = field(default_factory=list)
    gaps: list[int] = field(default_factory=list)

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
        self.rows += other.rows
        self.gaps += other.gaps


@dataclass
class _RoundTally:
    """Everything one round's observers saw: plain counts a worker can return.

    The matrices count ``(attacker role, defender role)`` over both teams.
    """

    seed: int
    roles: dict[str, _RoleTally]
    sharing: float
    team_captures: dict[str, int]
    tags_by_pair: Counter = field(default_factory=Counter)
    downs_by_pair: Counter = field(default_factory=Counter)
    tags: int = 0  # Tags made in the round, both teams


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


def _measure(values: Sequence[float]) -> Measure:
    """The mean of ``values`` and its standard error (0.0 below two values)."""
    mean = _ratio(sum(values), len(values))
    se = stdev(values) / sqrt(len(values)) if len(values) > 1 else 0.0
    return Measure(mean=mean, se=se)


def _ratio_measure(pairs: Sequence[tuple[float, float]], scale: float = 1.0) -> Measure:
    """``sum(y) / sum(x)`` over ``(y, x)`` pairs with its cluster-robust standard
    error (each pair one cluster), times ``scale``; 0.0 with no ``x``."""
    whole = sum(x for _, x in pairs)
    if not whole:
        return Measure(mean=0.0, se=0.0)
    ratio = sum(y for y, _ in pairs) / whole
    n = len(pairs)
    if n < 2:
        return Measure(mean=ratio * scale, se=0.0)
    spread = sum((y - ratio * x) ** 2 for y, x in pairs) * n / (n - 1)
    return Measure(mean=ratio * scale, se=sqrt(spread) / whole * scale)


def _double_count(resupplies: Sequence[tuple[int, str]]) -> int:
    """Doubles among a receiver's Resupplies in arrival order: each one pairs with
    the first unpaired one of the other kind at most ``DOUBLE_PAIR_TICKS`` later."""
    used = [False] * len(resupplies)
    pairs = 0
    for i, (tick, kind) in enumerate(resupplies):
        if used[i]:
            continue
        for j in range(i + 1, len(resupplies)):
            if resupplies[j][0] - tick > DOUBLE_PAIR_TICKS:
                break
            if not used[j] and resupplies[j][1] != kind:
                used[i] = used[j] = True
                pairs += 1
                break
    return pairs


def _doubles_share(rows: Sequence[_PlayerRound]) -> Measure:
    """Doubles as a % of the Resupply episodes received (a double is one)."""
    pairs = []
    for row in rows:
        doubles = _double_count(row.resupplies)
        pairs.append((float(doubles), float(len(row.resupplies) - doubles)))
    return _ratio_measure(pairs, 100.0)


def _visit_measures(rows: Sequence[_PlayerRound]) -> VisitMeasures:
    per_round, sizes, doubles, gaps = [], [], [], []
    for row in rows:
        visits = row.visits()
        per_round.append(float(len(visits)))
        for visit in visits:
            sizes.append(float(len(visit)))
            doubles.append(float(_double_count(visit)))
        gaps += [
            (after[0][0] - before[-1][0]) * TICK_SECONDS
            for before, after in zip(visits, visits[1:])
        ]
    return VisitMeasures(
        per_round=_measure(per_round),
        resupplies_per_visit=_measure(sizes),
        doubles_per_visit=_measure(doubles),
        gap_seconds=_measure(gaps),
        median_gap_seconds=float(median(gaps)) if gaps else None,
    )


def _cadence(gaps: Sequence[int]) -> Cadence:
    """The median gap and the share of the gaps in each cadence bin."""
    bins: Counter = Counter()
    for gap in gaps:
        bins[next(label for bound, label in _CADENCE_BINS if gap <= bound)] += 1
    return Cadence(
        median=float(median(gaps)) if gaps else None,
        bin_share={label: _ratio(bins[label], len(gaps)) for _, label in _CADENCE_BINS},
        gaps=len(gaps),
    )


def _per_round_matrix(pairs: Counter, rounds: int) -> dict[str, dict[str, float]]:
    """``pairs`` counts over ``rounds`` as attacker role to defender role, per round."""
    return {
        attacker: {
            defender: _ratio(pairs[(attacker, defender)], rounds) for defender in _ROLES
        }
        for attacker in _ROLES
    }


def _measures(role: str, tally: _RoleTally) -> RoleMeasures:
    round_ticks = BatchSimulator.ROUND_TICKS
    active_rolls = sum(tally.active_rolls.values())
    rows = tally.rows

    def measure(name: str) -> Measure:
        return _measure([getattr(row, name) for row in rows])

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
        times_tagged=measure("times_tagged"),
        shots_fired=measure("shots_fired"),
        hiding_misses=measure("hiding_misses"),
        downs_given=measure("downs_given"),
        downs_taken=measure("downs_taken"),
        active_share=measure("active_share"),
        tags_per_active_minute=measure("tags_per_active_minute"),
        reaction_shots=measure("reaction_shots"),
        follow_up_shots=measure("follow_up_shots"),
        enemies_on_floor_per_active_roll=measure("enemies_on_floor_per_active_roll"),
        mode_round_share={
            mode: ticks / (round_ticks * tally.player_rounds)
            for mode, ticks in sorted(tally.modes.items())
        },
        cadence=_cadence(tally.gaps),
        survival={
            minute: _measure([row.survived(minute) for row in rows])
            for minute in SURVIVAL_MINUTES
        },
        minutes_in_play=measure("minutes_in_play"),
        tag_downs_per_minute_in_play=_measure(
            [row.downs_given / row.minutes_in_play for row in rows if row.in_play_ticks]
        ),
        lives_lost={
            "tag": measure("downs_taken"),
            "missile": measure("lives_missile"),
            "nuke": measure("lives_nuke"),
        },
        life_resupplies=measure("life_resupplies"),
        doubles_share=_doubles_share(rows),
        hit_ratio=_ratio_measure(
            [(row.enemy_tags_made, row.enemy_times_tagged) for row in rows]
        ),
        shot_first_share=_ratio_measure(
            [(row.shot_first, row.enemy_downs_taken) for row in rows], 100.0
        ),
        visits=_visit_measures(rows),
    )


def _role_measures(tallies: Mapping[str, _RoleTally]) -> dict[str, RoleMeasures]:
    """Measures for each Role with a finished player; a goal spy can see others."""
    return {
        role: _measures(role, tallies[role])
        for role in sorted(tallies)
        if tallies[role].player_rounds
    }


def _probe_round(task: tuple) -> _RoundTally:
    """Play one observed round: ``task`` is ``(red, blue, movement_ctx, seed)``.

    The observers are installed in this process for the round and put back after
    it, so a worker process observes its own rounds. They read and call through,
    never roll, so the round is the one the seed plays unobserved.
    """
    red, blue, movement_ctx, seed = task
    tallies: dict[str, _RoleTally] = defaultdict(_RoleTally)
    rows: dict[int, _PlayerRound] = defaultdict(_PlayerRound)
    last_shot_tick: dict[int, int] = {}
    tags_by_pair: Counter = Counter()
    downs_by_pair: Counter = Counter()
    open_runs: Counter = Counter()
    goals_by_team_tick: dict = {}
    team_captures = {"red": 0, "blue": 0}
    real_choose = entrypoints.choose_goal_cell
    real_plan = entrypoints.plan_action
    real_capture = entrypoints._capture_base_shared
    # The tick loop fires Shots through the name ``entrypoints`` imported; a Reaction
    # or Follow-up with no cooldown fires through the resolver's own module.
    real_resolve = entrypoints.resolve_shot
    real_shot_resolve = shot_module.resolve_shot
    # CAL-03a: the missile's lives and every Resupply received, read off the
    # class attributes the engine calls through.
    real_missile = BatchSimulator._complete_missile
    real_resupply_lives = EventLog.resupply_lives
    real_resupply_ammo = EventLog.resupply_ammo
    # The tick of the latest enemy Tag per (attacker, defender), for shot-first.
    last_enemy_tag: dict[tuple[int, int], int] = {}

    def observe_enemy_tag(attacker, defender, tick: int, downed: bool) -> None:
        rows[id(attacker)].enemy_tags_made += 1
        rows[id(defender)].enemy_times_tagged += 1
        if downed:
            rows[id(defender)].enemy_downs_taken += 1
            back = last_enemy_tag.get((id(defender), id(attacker)))
            if back is not None and tick - back <= SHOT_FIRST_TICKS:
                rows[id(defender)].shot_first += 1
        last_enemy_tag[(id(attacker), id(defender))] = tick

    def observed_missile(sim, attacker, defender, second, ctx=None):
        lives = defender.final_lives
        real_missile(sim, attacker, defender, second, ctx)
        rows[id(defender)].lives_missile += lives - defender.final_lives

    def observed_resupply(real, kind: str):
        def observe(log, supporter, requestor, tick, *, amount=None):
            rows[id(requestor)].resupplies.append((int(tick), kind))
            return real(log, supporter, requestor, tick, amount=amount)

        return observe

    def observed_resolve(real):
        def observe(attacker, defender, tick, *, kind, ctx, chain_depth=0):
            tags_before = attacker.counters.tags_made
            misses_before = attacker.counters.shots_missed
            defender_hiding = defender.is_hiding
            outcome = real(
                attacker, defender, tick, kind=kind, ctx=ctx, chain_depth=chain_depth
            )
            hit = attacker.counters.tags_made > tags_before
            missed = attacker.counters.shots_missed > misses_before
            if hit or missed:
                last = last_shot_tick.get(id(attacker))
                if last is not None:
                    tallies[attacker.role].gaps.append(tick - last)
                last_shot_tick[id(attacker)] = tick
            if missed and defender_hiding:
                rows[id(attacker)].hiding_misses += 1
            if hit:
                tags_by_pair[(attacker.role, defender.role)] += 1
                if outcome.downed:
                    downs_by_pair[(attacker.role, defender.role)] += 1
                    rows[id(attacker)].downs_given += 1
                    rows[id(defender)].downs_taken += 1
                if attacker.team_color != defender.team_color:
                    observe_enemy_tag(attacker, defender, tick, outcome.downed)
            return outcome

        return observe

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
            row = rows[id(player)]
            row.active_rolls += 1
            row.enemies_on_floor += sum(
                1
                for other in all_alive
                if other.team_color != player.team_color and other.final_lives > 0
            )
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
    entrypoints.resolve_shot = observed_resolve(real_resolve)
    shot_module.resolve_shot = observed_resolve(real_shot_resolve)
    BatchSimulator._complete_missile = observed_missile  # type: ignore[method-assign]
    EventLog.resupply_lives = observed_resupply(  # type: ignore[method-assign]
        real_resupply_lives, "lives"
    )
    EventLog.resupply_ammo = observed_resupply(  # type: ignore[method-assign]
        real_resupply_ammo, "shots"
    )
    try:
        random.seed(seed)
        result, reds, blues = BatchSimulator()._simulate_round(
            list(red), list(blue), movement_ctx=movement_ctx
        )
        # Minutes in play stop at a team wipe (decision 3): the engine credits a
        # survivor the rest of the round as Active, the probe does not.
        round_end = min(
            result.get("eliminated_at", SURVIVED_SENTINEL), BatchSimulator.ROUND_TICKS
        )
        round_tags = 0
        for player in reds + blues:
            close_run(player)
            tally = tallies[player.role]
            counters = player.counters
            tally.modes.update({str(mode): n for mode, n in player.mode_ticks.items()})
            tally.points.append(counters.points_scored)
            tally.tags_made += counters.tags_made
            tally.ticks_active += player.ticks_active
            tally.player_rounds += 1
            if player.was_eliminated_at < SURVIVED_SENTINEL:
                tally.elimination_ticks.append(player.was_eliminated_at)
            row = rows[id(player)]
            row.times_tagged = counters.times_tagged
            row.tags_made = counters.tags_made
            row.shots_fired = counters.tags_made + counters.shots_missed
            row.reaction_shots = counters.reaction_shots
            row.follow_up_shots = counters.follow_up_shots
            row.ticks_active = player.ticks_active
            eliminated = player.was_eliminated_at < SURVIVED_SENTINEL
            row.eliminated_at = player.was_eliminated_at if eliminated else None
            row.in_play_ticks = min(player.was_eliminated_at, round_end)
            row.lives_nuke = counters.lives_lost_to_nukes
            round_tags += counters.tags_made
            tally.rows.append(row)
    finally:
        entrypoints.choose_goal_cell = real_choose
        entrypoints.plan_action = real_plan
        entrypoints._capture_base_shared = real_capture
        entrypoints.resolve_shot = real_resolve
        shot_module.resolve_shot = real_shot_resolve
        BatchSimulator._complete_missile = real_missile  # type: ignore[method-assign]
        EventLog.resupply_lives = real_resupply_lives  # type: ignore[method-assign]
        EventLog.resupply_ammo = real_resupply_ammo  # type: ignore[method-assign]
    return _RoundTally(
        seed=seed,
        roles=dict(tallies),
        sharing=goal_sharing_share(goals_by_team_tick),
        team_captures=team_captures,
        tags_by_pair=tags_by_pair,
        downs_by_pair=downs_by_pair,
        tags=round_tags,
    )


def probe_seeds(seed: int, count: int) -> list[int]:
    """``count`` per-round seeds drawn from ``random.Random(seed)``, in round order."""
    generator = random.Random(seed)
    return [generator.getrandbits(63) for _ in range(count)]


def _run_rounds(tasks: list[tuple], workers: int) -> list[_RoundTally]:
    """``_probe_round`` over ``tasks`` in order, in this process or ``workers``."""
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

        with ProcessPoolExecutor(
            max_workers=workers, initializer=worker_django_init
        ) as executor:
            return list(executor.map(_probe_round, tasks))
    return [_probe_round(task) for task in tasks]


def run_probe(
    movement_ctx,
    seeds: Iterable[int],
    lineups: Sequence[Lineup] | None = None,
    workers: int = 1,
) -> ProbeResult:
    """Play one round per seed on ``movement_ctx`` and measure it.

    With no ``lineups`` every seed plays the default-stat rosters. With them, seed
    ``i`` plays ``lineups[i]`` (red roster, blue roster), one round per lineup.
    ``workers`` above 1 plays the rounds across that many processes; the numbers
    are the serial ones.
    """
    seed_list = list(seeds)
    if lineups is None:
        default: Lineup = (_default_roster("red"), _default_roster("blue"))
        lineups = [default] * len(seed_list)
    tasks: list[tuple] = [
        (red, blue, movement_ctx, seed)
        for seed, (red, blue) in zip(seed_list, lineups, strict=True)
    ]
    rounds = _run_rounds(tasks, workers)

    pooled: dict[str, _RoleTally] = defaultdict(_RoleTally)
    tags_by_pair: Counter = Counter()
    downs_by_pair: Counter = Counter()
    for played in rounds:
        for role, tally in played.roles.items():
            pooled[role].add(tally)
        tags_by_pair.update(played.tags_by_pair)
        downs_by_pair.update(played.downs_by_pair)
    return ProbeResult(
        roles=_role_measures(pooled),
        sharing_by_seed={played.seed: played.sharing for played in rounds},
        by_seed={played.seed: _role_measures(played.roles) for played in rounds},
        team_captures_by_seed={played.seed: played.team_captures for played in rounds},
        who_tags_whom=_per_round_matrix(tags_by_pair, len(rounds)),
        who_downs_whom=_per_round_matrix(downs_by_pair, len(rounds)),
        round_tags=_measure([float(played.tags) for played in rounds]),
        doubles_share=_doubles_share(
            [row for tally in pooled.values() for row in tally.rows]
        ),
    )
