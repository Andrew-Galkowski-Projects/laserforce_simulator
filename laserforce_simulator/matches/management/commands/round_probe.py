"""Measure how players move, choose goals and use Actions over seeded rounds.

A thin wrapper over ``matches.round_probe``. The map fixture loads inside a rolled-back
transaction, so nothing is written. Run it on two checkouts to compare them: see
``management/commands/CLAUDE.md``.
"""

from __future__ import annotations

from math import sqrt
from statistics import stdev
from typing import Any, Callable, Iterable, Mapping

from django.core.management.base import BaseCommand, CommandError, CommandParser

from matches import lfstats_calibration as cal
from matches.management.commands.lfstats_calibrate import (
    DEFAULT_MAP_FIXTURE,
    DEFAULT_TEST,
    DEFAULT_TRAIN,
    REPO_ROOT,
    _load_json,
    _map_context,
)
from matches.round_probe import (
    LOCKER_ROLES,
    TARGET_STATES,
    Lineup,
    LockMeasures,
    Measure,
    ProbeResult,
    RoleMeasures,
    probe_seeds,
    run_probe,
)
from matches.sim_helpers import support_survival
from matches.sim_helpers.time_constants import TICK_SECONDS

# The real per-role targets ``--targets`` prints beside the sim's (CAL-02c, #566).
DEFAULT_TARGETS = REPO_ROOT / "data" / "tdf_role_targets_2026.json"
_ROLES = ("commander", "heavy", "scout", "ammo", "medic")
# CAL-04a: the Roles that make Resupply visits.
_VISITORS = ("commander", "heavy", "scout")
# The real figure beside a role line: measure -> (targets metric, scale to print).
_REAL_METRIC: dict[str, tuple[str, float]] = {
    "tags_made_per_round": ("tags", 1.0),
    "eliminated_share": ("eliminated", 100.0),
    "times_tagged": ("times_tagged", 1.0),
    # Like for like: the sim's shots fired are Tags plus misses, so the real figure
    # is the zaps on players plus misses, without the Base zaps ``shots_fired`` holds.
    "shots_fired": ("shots_at_players", 1.0),
    "downs_given": ("downs_given", 1.0),
    "downs_taken": ("downs_taken", 1.0),
    "active_share": ("active_share", 100.0),
    "tags_per_active_minute": ("tags_per_active_min", 1.0),
    "enemies_on_floor_per_active_roll": ("enemies_on_floor", 1.0),
    "elimination_tick": ("elim_minute", 120.0),
}
# CAL-03a (#589): the real figures the targets file does not hold, from the CAL-03
# section of PLAN.md (164 TDF games); printed only beside a targets run.
_CAL03_REAL: dict[str, dict[str, float]] = {
    "survival_5": {"ammo": 97, "medic": 95},
    "survival_7": {"ammo": 92, "medic": 78},
    "survival_9": {"commander": 85, "ammo": 80, "medic": 62},
    "tag_downs_per_minute_in_play": {
        "commander": 2.32,
        "heavy": 1.88,
        "scout": 2.92,
        "ammo": 2.11,
        "medic": 1.19,
    },
    "lives_nuke": {"medic": 6.5},
    "life_resupplies": {"ammo": 10.2, "medic": 0.0},
    "doubles_share": {"commander": 44, "heavy": 39, "scout": 46},
    "hit_ratio": {
        "commander": 0.95,
        "heavy": 0.93,
        "scout": 1.12,
        "ammo": 0.99,
        "medic": 0.87,
    },
    "shot_first_share": {
        "commander": 24,
        "heavy": 8,
        "scout": 18,
        "ammo": 19,
        "medic": 20,
    },
    "visits_per_round": {"commander": 6.2, "heavy": 8.3, "scout": 4.6},
    "resupplies_per_visit": {"commander": 2.82, "heavy": 2.30, "scout": 2.77},
    "doubles_per_visit": {"commander": 0.86, "heavy": 0.64, "scout": 0.87},
    "median_gap_seconds": {"commander": 84, "heavy": 52, "scout": 111},
}
# Team-wide real figures (PLAN.md CAL-03): Tags per round, doubles % of Resupplies.
_CAL03_REAL_ROUND_TAGS = 403.0
_CAL03_REAL_DOUBLES = 32.1
# MECH-24c (#614): the real Lock figures (spec #611, the 165 Internationals TDF
# games), printed beside the ``locks`` block in a targets run.
_LOCKS_REAL_PER_ROUND = 15.9
_LOCKS_REAL_MISSILES_PER_PLAYER = {"commander": 2.47, "heavy": 1.76}
_LOCKS_REAL_CONVERSION = {"all": 53, "active": 23, "reset": 65}
_LOCKS_REAL_STATE_SHARE = {"reset": 69}
_STATE_LABEL = {"active": "an active target", "reset": "a reset target"}


def calibration_lineups(limit: int | None = None) -> list[Lineup]:
    """The used Test games' (red, blue) rosters from the default Training and Test
    files, the Calibration harness's way (``lfstats_calibration.used_lineups``:
    its used-games filter, rosters in position order, the default import
    constants). ``limit`` keeps the first games only."""
    train = _load_json(str(DEFAULT_TRAIN), "Training file")
    test = _load_json(str(DEFAULT_TEST), "Test file")
    return cal.used_lineups(train, test, limit)


def _real(targets: Mapping | None, role: str, measure: str) -> str:
    """`` | real 54.5`` for a role line with a figure in ``targets``, else nothing."""
    if targets is None or measure not in _REAL_METRIC:
        return ""
    metric, scale = _REAL_METRIC[measure]
    real = targets.get("per_role", {}).get(role, {}).get(metric)
    if real is None:
        return ""
    return f" | real {real['mean'] * scale:.1f}"


def _percent(share: float) -> str:
    return f"{share * 100:.1f}"


def _spread(mean: float, per_seed: Iterable[float], form: str = ".1f") -> str:
    """``mean (min-max)``: the pooled mean and the range across the seeds."""
    values = list(per_seed)
    return f"{mean:{form}} ({min(values):{form}}-{max(values):{form}})"


def _percent_spread(share: float, per_seed: Iterable[float]) -> str:
    return _spread(share * 100, [value * 100 for value in per_seed])


def _standard_error(per_seed: list[float]) -> float:
    """How far the mean of these seeds could move by chance; 0.0 with one seed."""
    return stdev(per_seed) / sqrt(len(per_seed)) if len(per_seed) > 1 else 0.0


def _margin_spread(mean: float, per_seed: list[float], form: str = ".1f") -> str:
    """``mean +/- standard error (min-max)``, for a measure a target can read."""
    return (
        f"{mean:{form}} +/- {_standard_error(per_seed):{form}} "
        f"({min(per_seed):{form}}-{max(per_seed):{form}})"
    )


def _measure_spread(
    pooled: Measure, per_seed: Iterable[Measure], scale: float = 1.0
) -> str:
    """``mean +/- standard error (min-max)``: the error is over the player-rounds,
    the range across the seeds' means."""
    means = [seed.mean * scale for seed in per_seed]
    return (
        f"{pooled.mean * scale:.1f} +/- {pooled.se * scale:.1f} "
        f"({min(means):.1f}-{max(means):.1f})"
    )


def _cal03_real(targets: Mapping | None, role: str, key: str, form: str) -> str:
    """`` | real 2.32`` from ``_CAL03_REAL`` beside a targets run, else nothing."""
    real = _CAL03_REAL.get(key, {}).get(role)
    if targets is None or real is None:
        return ""
    return f" | real {real:{form}}"


def _measure_line(
    label: str, pooled: Measure, per_seed: list[Measure], form: str = ".1f"
) -> str:
    """``  label: mean +/- se (min-max)``: the error is the measure's own, the
    range across the seeds' means."""
    means = [seed.mean for seed in per_seed]
    return (
        f"  {label}: {pooled.mean:{form}} +/- {pooled.se:{form}} "
        f"({min(means):{form}}-{max(means):{form}})"
    )


def _cal03_lines(
    role: str,
    pooled: RoleMeasures,
    seeds: list[RoleMeasures],
    targets: Mapping | None = None,
) -> list[str]:
    """CAL-03a (#589): survival, minutes in play, lives by cause, Resupplies,
    doubles, hit ratio, shot-first and Resupply visits, each with its standard
    error and the real figure beside it when there is one."""

    def line(
        label: str, key: str, read: Callable[[RoleMeasures], Measure], form=".1f"
    ) -> str:
        return _measure_line(
            label, read(pooled), [read(seed) for seed in seeds], form
        ) + _cal03_real(targets, role, key, form)

    def survival_at(minute: int) -> str:
        return _measure_line(
            f"Survival % at minute {minute}",
            pooled.survival[minute],
            [seed.survival[minute] for seed in seeds],
        ) + _cal03_real(targets, role, f"survival_{minute}", ".1f")

    lines = [survival_at(minute) for minute in pooled.survival]
    lines += [
        line("Minutes in play", "minutes_in_play", lambda m: m.minutes_in_play),
        line(
            "Tag Downs per minute in play",
            "tag_downs_per_minute_in_play",
            lambda m: m.tag_downs_per_minute_in_play,
            ".2f",
        ),
        line("Lives lost to Tags", "lives_tag", lambda m: m.lives_lost["tag"]),
        line(
            "Lives lost to missiles", "lives_missile", lambda m: m.lives_lost["missile"]
        ),
        line("Lives lost to nukes", "lives_nuke", lambda m: m.lives_lost["nuke"]),
        line(
            "Life Resupplies received", "life_resupplies", lambda m: m.life_resupplies
        ),
        line(
            "Doubles % of Resupplies received",
            "doubles_share",
            lambda m: m.doubles_share,
        ),
        line(
            "Hit ratio (Tags made / times tagged)",
            "hit_ratio",
            lambda m: m.hit_ratio,
            ".2f",
        ),
        line(
            "Shot-first % of Tag Downs",
            "shot_first_share",
            lambda m: m.shot_first_share,
        ),
        line(
            "Resupply visits per round",
            "visits_per_round",
            lambda m: m.visits.per_round,
        ),
        line(
            "Resupplies per visit",
            "resupplies_per_visit",
            lambda m: m.visits.resupplies_per_visit,
            ".2f",
        ),
        line(
            "Doubles per visit",
            "doubles_per_visit",
            lambda m: m.visits.doubles_per_visit,
            ".2f",
        ),
    ]
    visits = pooled.visits
    if visits.median_gap_seconds is None:
        gap = "none"
    else:
        gap = (
            f"median {visits.median_gap_seconds:.1f} (mean "
            f"{visits.gap_seconds.mean:.1f} +/- {visits.gap_seconds.se:.1f})"
        )
    real_gap = _cal03_real(targets, role, "median_gap_seconds", ".0f")
    return lines + [f"  Gap to the next visit, s: {gap}" + real_gap]


def _pooled(measures: Mapping[str, Measure]) -> str:
    """``name mean +/- se, ...``: pooled shares with their own standard error."""
    return ", ".join(
        f"{name} {m.mean:.1f} +/- {m.se:.1f}" for name, m in measures.items()
    )


def _cal04_lines(
    role: str, pooled: RoleMeasures, seeds: list[RoleMeasures]
) -> list[str]:
    """CAL-04a (#599): a Commander, Heavy or Scout's engine visits; a Medic or
    Ammo's lives lost by cause and minute band; the Ammo's distance to its Medic."""

    def line(label: str, read: Callable[[RoleMeasures], Measure]) -> str:
        return _measure_line(label, read(pooled), [read(seed) for seed in seeds])

    if role in _VISITORS:
        visits = pooled.engine_visits
        ticks = visits.median_ticks_to_arrive
        reach = support_survival.LEASH_SQUARES
        return [
            line(
                "Engine visit starts per round, normal",
                lambda m: m.engine_visits.normal_starts,
            ),
            line(
                "Engine visit starts per round, critical",
                lambda m: m.engine_visits.critical_starts,
            ),
            f"  Engine visit end %: {_pooled(visits.end_share)}",
            "  Engine visit ticks to arrive: "
            + ("none" if ticks is None else f"median {ticks:.1f}"),
            line(
                "Engine visits never arriving %",
                lambda m: m.engine_visits.never_arrived,
            ),
            line(
                "Arrivals with both supports ready %",
                lambda m: m.engine_visits.ready_at_arrival,
            ),
            f"  Not-ready arrival causes %: {_pooled(visits.not_ready_causes)}",
            line(
                f"Arrivals with the supports more than {reach} squares apart %",
                lambda m: m.engine_visits.supports_apart,
            ),
            line(
                "Arrivals with both supports ready later in the visit %",
                lambda m: m.engine_visits.ready_later,
            ),
        ]
    lines = [
        f"  Lives lost to {name} by minute band: "
        + _pooled(pooled.lives_by_band[cause])
        for cause, name in (("tag", "Tags"), ("missile", "missiles"), ("nuke", "nukes"))
    ]
    if role == "ammo":
        distance = pooled.medic_distance_median
        far = pooled.medic_distance_far
        lines.append(
            "  Medic-Ammo distance, squares: "
            + (
                "none"
                if distance is None
                else f"median {distance:.1f} over {pooled.medic_distance_plans} "
                f"Active plans; more than {support_survival.LEASH_SQUARES}: "
                f"{far.mean:.1f} +/- {far.se:.1f} %"
            )
        )
    return lines


def _real_cadence(targets: Mapping | None, role: str) -> str:
    """`` | real median 7.2 ticks``: the targets' cadence median (seconds) in
    ticks, or nothing without one."""
    if targets is None:
        return ""
    median = targets.get("cadence", {}).get(role, {}).get("median")
    if median is None:
        return ""
    return f" | real median {median / TICK_SECONDS:.1f} ticks"


def _cadence_line(
    pooled: RoleMeasures, role: str, targets: Mapping | None = None
) -> str:
    cadence = pooled.cadence
    if cadence.median is None:
        return "  Shot cadence, ticks: none" + _real_cadence(targets, role)
    bins = ", ".join(
        f"{label} {share * 100:.0f}%" for label, share in cadence.bin_share.items()
    )
    return (
        f"  Shot cadence, ticks: median {cadence.median:.1f} over {cadence.gaps} "
        f"gaps; {bins}" + _real_cadence(targets, role)
    )


def _role_lines(
    role: str,
    pooled: RoleMeasures,
    seeds: list[RoleMeasures],
    targets: Mapping | None = None,
) -> list[str]:
    """One Role's block. ``seeds`` holds its measures in each seed it played."""

    def number(read: Callable[[RoleMeasures], float], form: str = ".1f") -> str:
        return _margin_spread(read(pooled), [read(seed) for seed in seeds], form)

    def percent(read: Callable[[RoleMeasures], float]) -> str:
        return _margin_spread(read(pooled) * 100, [read(seed) * 100 for seed in seeds])

    def measure(name: str, scale: float = 1.0) -> str:
        return _measure_spread(
            getattr(pooled, name), [getattr(seed, name) for seed in seeds], scale
        ) + _real(targets, role, name)

    # A seed nobody was Eliminated in has no tick to range over.
    elimination_ticks = [
        seed.elimination_tick for seed in seeds if seed.elimination_tick is not None
    ]
    elimination_tick = (
        "none"
        if pooled.elimination_tick is None
        else _margin_spread(pooled.elimination_tick, elimination_ticks, ".0f")
    )

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
    round_modes = ", ".join(
        f"{mode} "
        + _percent_spread(
            share, [seed.mode_round_share.get(mode, 0.0) for seed in seeds]
        )
        for mode, share in pooled.mode_round_share.items()
    )
    return [
        role,
        f"  Mode %: {modes or 'none'}",
        f"  Mode % of round: {round_modes or 'none'}",
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
        f"  Tags made per round: {number(lambda m: m.tags_made_per_round)}"
        + _real(targets, role, "tags_made_per_round"),
        f"  Resupplies per round (planned): "
        f"{number(lambda m: m.resupplies_per_round)}",
        f"  Live captures per round: {number(lambda m: m.captures_per_round)}",
        f"  Eliminated %: {percent(lambda m: m.eliminated_share)}"
        + _real(targets, role, "eliminated_share"),
        f"  Elimination tick: {elimination_tick}"
        + _real(targets, role, "elimination_tick"),
        f"  Times tagged per round: {measure('times_tagged')}",
        f"  Shots fired per round: {measure('shots_fired')}",
        f"  Downs given per round: {measure('downs_given')}",
        f"  Downs taken per round: {measure('downs_taken')}",
        f"  Active %: {measure('active_share', 100.0)}",
        f"  Tags per Active minute: {measure('tags_per_active_minute')}",
        f"  Reaction shots per round: {measure('reaction_shots')}",
        f"  Follow-up shots per round: {measure('follow_up_shots')}",
        "  Enemies on the floor per Active roll: "
        f"{measure('enemies_on_floor_per_active_roll')}",
        _cadence_line(pooled, role, targets),
        *_cal03_lines(role, pooled, seeds, targets),
        *_cal04_lines(role, pooled, seeds),
    ]


def _team_captures(result: ProbeResult, team: str) -> str:
    counts = [seed[team] for seed in result.team_captures_by_seed.values()]
    return f"{team} {sum(counts) / len(counts):.1f} ({min(counts)}-{max(counts)})"


def _matrix_lines(
    title: str,
    matrix: Mapping[str, Mapping[str, float]],
    real: Mapping[str, Mapping[str, float]] | None,
) -> list[str]:
    """The attacker-by-defender table per round with a total column; under each
    attacker row the real row when ``real`` has one."""

    def row(label: str, cells: Mapping[str, float]) -> str:
        values = [cells.get(defender, 0.0) for defender in _ROLES]
        return f"{label:12}" + "".join(f"{v:10.1f}" for v in values + [sum(values)])

    lines = [
        f"{title} per round, both teams (attacker row, defender column):",
        f"{'':12}" + "".join(f"{name:>10}" for name in (*_ROLES, "total")),
    ]
    for attacker in _ROLES:
        lines.append(row(attacker, matrix.get(attacker, {})))
        if real is not None and attacker in real:
            lines.append(row("  real", real[attacker]))
    return lines


def _lock_lines(locks: LockMeasures, targets: Mapping | None) -> list[str]:
    """MECH-24c (#614): the ``locks`` block. The per-round counts read
    ``mean +/- se (min-max)`` over the rounds; the shares ``mean +/- se`` with a
    cluster-robust error over the rounds. A targets run adds the real figures."""

    def real(value: float | None, form: str = ".0f") -> str:
        if targets is None or value is None:
            return ""
        return f" | real {value:{form}}"

    def per_round(label: str, values: tuple[float, ...], form: str = ".1f") -> str:
        return f"  {label}: {_margin_spread(_mean(values), list(values), form)}"

    def share(label: str, measure: Measure, real_value: float | None = None) -> str:
        return f"  {label}: {measure.mean:.1f} +/- {measure.se:.1f}" + real(real_value)

    lines = [
        "Missile Locks per round, both teams:",
        per_round("Locks, every locker", locks.all_locks)
        + real(_LOCKS_REAL_PER_ROUND, ".1f"),
    ]
    for role in LOCKER_ROLES:
        lines += [
            per_round(f"{role} Locks", locks.locks[role]),
            per_round(f"{role} missiles", locks.missiles[role]),
            per_round(
                f"{role} missiles per player-round",
                locks.missiles_per_player[role],
                ".2f",
            )
            + real(_LOCKS_REAL_MISSILES_PER_PLAYER.get(role), ".2f"),
        ]
    lines.append(
        share(
            "Conversion % (missiles / Locks)",
            locks.conversion["all"],
            _LOCKS_REAL_CONVERSION["all"],
        )
    )
    lines += [
        share(
            f"Conversion % on {_STATE_LABEL[state]}",
            locks.conversion[state],
            _LOCKS_REAL_CONVERSION[state],
        )
        for state in TARGET_STATES
    ]
    lines += [
        share(
            f"Locks on {_STATE_LABEL[state]} %",
            locks.state_share[state],
            _LOCKS_REAL_STATE_SHARE.get(state),
        )
        for state in TARGET_STATES
    ]
    lines += [
        f"  Lock endings %, {scope}: {_pooled(locks.endings[scope])}"
        for scope in ("all", *TARGET_STATES)
    ]
    lines += [
        share(
            f"Dodge pick % (dodge plans / locked plans), {state}",
            locks.dodge_pick[state],
        )
        + f" over {locks.locked_plans[state]} plans"
        for state in TARGET_STATES
    ]
    lines += [
        share(
            f"Dodge success % (successful rolls / rolls), {state}",
            locks.dodge_success[state],
        )
        + f" over {locks.dodge_rolls[state]} rolls"
        for state in TARGET_STATES
    ]
    return lines


def _mean(values: tuple[float, ...]) -> float:
    """The mean of ``values`` (0.0 with none)."""
    return sum(values) / len(values) if values else 0.0


def format_result(result: ProbeResult, targets: Mapping | None = None) -> list[str]:
    """The probe's numbers as ASCII lines, one block per Role, then the team-wide ones.

    Each measure reads ``mean (min-max)``, or ``mean +/- standard error (min-max)`` for
    one a target can read: the mean pools every seed, the range and the standard error
    are across the seeds. The CAL-02 tag measures take their standard error over the
    player-rounds instead. With ``targets`` (the committed TDF per-role targets) a line
    with a real counterpart ends `` | real <mean>``, and each matrix row gets the real
    row under it.
    """
    lines = []
    for role, pooled in result.roles.items():
        seeds = [roles[role] for roles in result.by_seed.values() if role in roles]
        lines += _role_lines(role, pooled, seeds, targets)
    if result.who_tags_whom:
        lines += _matrix_lines(
            "Who tags whom",
            result.who_tags_whom,
            targets.get("who_tags_whom") if targets else None,
        )
        lines += _matrix_lines(
            "Who Downs whom",
            result.who_downs_whom,
            targets.get("who_downs_whom") if targets else None,
        )
    real = targets is not None
    lines.append(
        f"Tags per round, both teams: {result.round_tags.mean:.1f} +/- "
        f"{result.round_tags.se:.1f}"
        + (f" | real {_CAL03_REAL_ROUND_TAGS:.1f}" if real else "")
    )
    lines.append(
        f"Doubles % of all Resupplies received: {result.doubles_share.mean:.1f} +/- "
        f"{result.doubles_share.se:.1f}"
        + (f" | real {_CAL03_REAL_DOUBLES:.1f}" if real else "")
    )
    lines.append(
        "Live captures per round: "
        f"{_team_captures(result, 'red')}, {_team_captures(result, 'blue')}"
    )
    shares = list(result.sharing_by_seed.values())
    lines.append(
        f"Goal sharing %: mean {_percent(sum(shares) / len(shares))} "
        f"+/- {_percent(_standard_error(shares))}, "
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
    if result.locks.all_locks:  # a result built with rounds played
        lines += _lock_lines(result.locks, targets)
    return lines


class Command(BaseCommand):
    help = (
        "Measure Modes, goal sources, distances, Actions, Holds, captures, goal "
        "sharing and the tag measures over seeded rounds, beside the real TDF "
        "targets."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--seeds",
            type=int,
            default=12,
            help="play seeds 1 to N, one round each (default: 12); "
            "ignored with --lineups",
        )
        parser.add_argument(
            "--map-fixture",
            dest="map_fixture",
            default=str(DEFAULT_MAP_FIXTURE),
            help="map fixture JSON (default: the Syracuse fixture)",
        )
        parser.add_argument(
            "--lineups",
            choices=("test",),
            help="test: play the used Test games' real lineups, one round each, "
            "instead of the default-stat rosters",
        )
        parser.add_argument(
            "--games",
            type=int,
            help="with --lineups, play the first N games only (default: all 128)",
        )
        parser.add_argument(
            "--seed",
            type=int,
            default=42,
            help="with --lineups, the master seed the per-game seeds are drawn "
            "from (default: 42)",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=1,
            help="processes to play the rounds across (default: 1)",
        )
        parser.add_argument(
            "--targets",
            default=str(DEFAULT_TARGETS),
            help="real per-role targets JSON to print beside the sim's "
            f"(default: {DEFAULT_TARGETS.relative_to(REPO_ROOT)})",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if options["seeds"] < 1:
            raise CommandError("--seeds must be at least 1.")
        if options["workers"] < 1:
            raise CommandError("--workers must be at least 1.")
        if options["games"] is not None and options["games"] < 1:
            raise CommandError("--games must be at least 1.")
        targets = _load_json(options["targets"], "Targets file")
        movement_ctx = _map_context(options["map_fixture"])
        if options["lineups"] == "test":
            lineups = calibration_lineups(options["games"])
            seeds = probe_seeds(options["seed"], len(lineups))
            self.stdout.write(
                f"{len(lineups)} Test games, one round each, master seed "
                f"{options['seed']}, {options['workers']} workers"
            )
        else:
            lineups = None
            seeds = list(range(1, options["seeds"] + 1))
        result = run_probe(
            movement_ctx, seeds, lineups=lineups, workers=options["workers"]
        )
        for line in format_result(result, targets):
            self.stdout.write(line)
