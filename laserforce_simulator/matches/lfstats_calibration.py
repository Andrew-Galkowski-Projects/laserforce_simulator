"""LFS-01 Calibration harness, Test side (spec #514, Implementation Decision 4)."""

from __future__ import annotations

import json
import math
import os
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any, cast

from core.models import (
    ArenaMap,
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapCellRankingConfig,
    MapZoneConfig,
    SightLineConfig,
)
from matches.sim_helpers.parallel_worker import (
    calibration_round_worker,
    worker_django_init,
)
from matches.sim_helpers.score_calculator import calculate_mvp
from matches.sim_helpers.time_constants import SURVIVED_SENTINEL
from matches.simulation.entrypoints import _SIMULATION_STATS, _PlayerData
from teams.lfstats_formulas import DEFAULT_CONSTANTS, ImportConstants, Row, stats_for

POSITIONS: tuple[str, ...] = ("commander", "heavy", "scout", "ammo", "medic")

# (metric_id, kind, positions LFstats records it for), in report order. The
# ids are the LFstats per-position row keys the sim values are compared with.
METRICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("avg_accuracy", "fraction", POSITIONS),
    ("avg_hit_diff", "count", POSITIONS),
    ("avg_times_hit", "count", POSITIONS),
    ("avg_mvp", "count", POSITIONS),
    ("avg_uptime_pct", "fraction", POSITIONS),
    ("elimination_rate", "fraction", POSITIONS),
    ("avg_resupplies_given", "count", ("ammo", "medic")),
    ("avg_nukes_detonated", "count", ("commander",)),
    ("avg_missiles_hit", "count", ("commander", "heavy")),
    ("avg_rapid_fire", "count", ("scout",)),
)

# Decision 16: a metric passes within 15% of the real value or an absolute
# floor, whichever is wider. The floor keeps small counts (0.3 vs 0.4 nukes a
# game) from failing on a percentage.
TOLERANCE_SHARE = 0.15
TOLERANCE_FLOOR: dict[str, float] = {"count": 0.5, "fraction": 0.05}
# Absorbs float noise at the exact edge (abs(1.15 - 1.0) is 0.1499..., abs(0.85
# - 1.0) is 0.1500...02), so both sides of a boundary behave alike.
_EDGE_EPSILON = 1e-9


def within_tolerance(kind: str, sim: float, real: float) -> bool:
    """True when ``sim`` is within 15% of ``real`` or the ``kind``'s floor.

    ``kind`` is ``"count"`` (floor 0.5) or ``"fraction"`` (floor 0.05).
    """
    if kind not in TOLERANCE_FLOOR:
        raise ValueError(f"Unknown metric kind {kind!r}")
    allowed = max(TOLERANCE_SHARE * abs(real), TOLERANCE_FLOOR[kind])
    return abs(sim - real) <= allowed + _EDGE_EPSILON


_FIXTURE_REQUIRED = ("name", "zone_size", "zone_data", "bases", "sight_data")


def load_map_fixture(path: str | os.PathLike[str]) -> ArenaMap:
    """Create an ``ArenaMap`` and every config ``load_map_context`` reads, from a JSON file.

    The caller wraps this in ``transaction.atomic()`` and rolls back, so
    nothing persists. ``ArenaMap`` is shared, unowned data; no image file is
    written (``image`` is only a stored path).

    **Fixture format** (``data/maps/*.json``), one object whose keys mirror
    the model fields, all at the one ``zone_size``::

        {
          "name": "Syracuse Laser Tag",       # ArenaMap.name
          "image": "maps/syracuse.png",       # optional ArenaMap.image path
          "img_width": 1200,                  # optional ArenaMap.img_width
          "img_height": 800,                  # optional ArenaMap.img_height
          "zone_size": 20,                    # MapZoneConfig.zone_size (px)
          "zone_data": {...},                 # MapZoneConfig.zone_data verbatim:
                                              #   {"zones": [[int]], "wall_meta",
                                              #    "elevation", "red_spawn",
                                              #    "blue_spawn", ...} or a bare grid
          "bases": [                          # MapBaseConfig rows
            {"base_type": "red", "x_px": 70, "y_px": 210}, ...
          ],
          "sight_data": {"r,c": ["r,c", ...]},  # SightLineConfig.sight_data
          "base_sight_lines": [               # BaseSightLineConfig rows
            {"base_type": "red", "visible_cells": [[r, c], ...]}, ...
          ],
          "cell_ranking": [[r, c], ...],      # optional MapCellRankingConfig.ranked_cells
          "strong_spots": [[r, c], ...]       # optional HeavyStrongSpotsConfig.cells
        }

    The zone config is saved ``confirmed``. ``base_sight_lines`` is required
    in practice: ``load_map_context`` refuses a map without base sight lines.

    Raises:
        ValueError: when a required key (name, zone_size, zone_data, bases,
            sight_data) is missing. Nothing is created in that case.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in _FIXTURE_REQUIRED if key not in data]
    if missing:
        raise ValueError(f"Map fixture {path} is missing {', '.join(missing)}")
    zone_size = data["zone_size"]
    arena_map = ArenaMap.objects.create(
        name=data["name"],
        image=data.get("image", ""),
        img_width=data.get("img_width"),
        img_height=data.get("img_height"),
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=data["zone_data"],
        confirmed=True,
    )
    for base in data["bases"]:
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type=base["base_type"],
            x_px=base["x_px"],
            y_px=base["y_px"],
        )
    SightLineConfig.objects.create(
        arena_map=arena_map, zone_size=zone_size, sight_data=data["sight_data"]
    )
    for base_sight in data.get("base_sight_lines", []):
        BaseSightLineConfig.objects.create(
            arena_map=arena_map,
            base_type=base_sight["base_type"],
            zone_size=zone_size,
            visible_cells=base_sight["visible_cells"],
        )
    if "cell_ranking" in data:
        MapCellRankingConfig.objects.create(
            arena_map=arena_map, zone_size=zone_size, ranked_cells=data["cell_ranking"]
        )
    if "strong_spots" in data:
        HeavyStrongSpotsConfig.objects.create(
            arena_map=arena_map, zone_size=zone_size, cells=data["strong_spots"]
        )
    return arena_map


def player_data(
    ipl_id: str,
    name: str,
    row: Row,
    baseline: Row,
    loss_row: Row | None,
    loss_baseline: Row,
    role: str,
    constants: ImportConstants = DEFAULT_CONSTANTS,
) -> _PlayerData:
    """The engine's picklable player stand-in, built from one position's LFstats row.

    ``row`` / ``baseline`` are the player's row and the field baseline at
    ``row["position"]``; ``loss_row`` (``None`` when the player has none) and
    ``loss_baseline`` feed ``adaptability``. The sim stats are
    ``stats_for([row], ...)`` with ``constants``; when ``role`` is the row's
    own position each is multiplied by ``constants.preferred_role_boost`` and
    capped at 100, as ``Player.stat_for_simulation`` does for a preferred Role.
    No ORM is touched.
    """
    position = row["position"]
    stats = stats_for(
        [row],
        [loss_row] if loss_row is not None else [],
        {position: baseline},
        {position: loss_baseline},
        constants,
    )
    sim_stats = {s: stats[s] for s in _SIMULATION_STATS}
    if role == position:
        boost = constants.preferred_role_boost
        sim_stats = {s: min(int(v * boost), 100) for s, v in sim_stats.items()}
    return _PlayerData(ipl_id, name, sim_stats)


class _RoundView:
    """The three ``GameRound`` fields ``calculate_mvp`` reads, from a round result."""

    def __init__(self, result: dict) -> None:
        self.red_team_eliminated = result["red_eliminated"]
        self.blue_team_eliminated = result["blue_eliminated"]
        self.eliminated_at = result["eliminated_at"]


class _MvpView:
    """A ``PlayerState`` seen through the ``PlayerRoundState`` names ``calculate_mvp`` reads.

    ``specific_tags`` is empty: the batch engine does not track per-target
    tags, so a saved batch round has an empty one too (the scout's +0.2 per
    Commander / Heavy tag never scores in the sim).
    """

    def __init__(self, player_state: Any, result: dict) -> None:
        counters = player_state.counters
        self.role = player_state.role
        self.team_color = player_state.team_color
        self.final_lives = player_state.final_lives
        self.final_medic_hits = player_state.medic_hits
        self.tags_made = counters.tags_made
        self.shots_missed = counters.shots_missed
        self.enemy_nuke_cancels = counters.enemy_nuke_cancels
        self.ally_nuke_cancels = counters.ally_nuke_cancels
        self.times_missiled = counters.times_missiled
        self.missiles_landed = counters.missiles_landed
        self.specials_used = counters.specials_used
        self.own_specials_cancelled = counters.own_specials_cancelled
        self.points_scored = counters.points_scored
        self.specific_tags: dict = {}
        self.game_round = _RoundView(result)

    @property
    def get_accuracy(self) -> int:
        # PlayerRoundState.get_accuracy: an integer percentage.
        total = self.tags_made + self.shots_missed
        return round(self.tags_made / total * 100) if total else 0


def sim_metrics(player_state: Any, result: dict) -> dict[str, float]:
    """One simulated player-round's value for each metric that applies to its Role.

    ``player_state`` is an end-of-round ``PlayerState``; ``result`` is the
    round's result dict from ``BatchSimulator._simulate_round`` (only
    ``red_eliminated`` / ``blue_eliminated`` / ``eliminated_at`` are read, for MVP).
    """
    c = player_state.counters
    role = player_state.role
    shots = c.tags_made + c.shots_missed
    live_ticks = (
        player_state.ticks_active
        + player_state.ticks_reset_window
        + player_state.ticks_not_targetable
    )
    values: dict[str, float] = {
        "avg_accuracy": c.tags_made / shots if shots else 0.0,
        "avg_hit_diff": c.tags_made / max(c.times_tagged, 1),
        "avg_times_hit": float(c.times_tagged),
        # calculate_mvp is duck-typed; _MvpView supplies the fields it reads.
        "avg_mvp": float(calculate_mvp(cast(Any, _MvpView(player_state, result)))),
        # LFstats uptime: dead time (after elimination) is not in the denominator.
        "avg_uptime_pct": player_state.ticks_active / live_ticks if live_ticks else 0.0,
        "elimination_rate": (
            1.0 if player_state.was_eliminated_at < SURVIVED_SENTINEL else 0.0
        ),
        "avg_resupplies_given": float(c.resupplies_given),
        "avg_nukes_detonated": float(c.specials_used - c.own_specials_cancelled),
        "avg_missiles_hit": float(c.missiles_landed),
        "avg_rapid_fire": float(c.specials_used),
    }
    return {
        metric: values[metric]
        for metric, _kind, positions in METRICS
        if role in positions
    }


# --- run_test ---------------------------------------------------------------

# Grill decision 29 (spec #514 comment): a Test game is skipped when any
# player has fewer than this many Training games at the position they played.
TEST_MIN_POSITION_GAMES = 5
# The sim's fixed roster. A side with any other mix is skipped.
LINEUP: tuple[str, ...] = ("commander", "heavy", "scout", "scout", "ammo", "medic")
# Spearman is over players with at least this many used scorecards at the
# position, and is None with fewer than SPEARMAN_MIN_PLAYERS of them.
SPEARMAN_MIN_SCORECARDS = 2
SPEARMAN_MIN_PLAYERS = 3
# Fire plays red, Ice plays blue.
COLOUR_SIDE: dict[str, str] = {"fire": "red", "ice": "blue"}


@dataclass(frozen=True)
class CellResult:
    """One (Role, metric): simulated vs real mean and the tolerance verdict."""

    position: str
    metric: str
    sim_mean: float
    real_mean: float
    n_scorecards: int
    passed: bool
    spearman: float | None


@dataclass(frozen=True)
class MatchResult:
    """Match level over the used games (reported, never gating)."""

    games: int
    # Share of used games whose mean simulated margin picks the real winner.
    winner_agreement: float
    # Mean of |mean simulated (red - blue) margin - real (fire - ice) margin|.
    mean_margin_error: float


@dataclass(frozen=True)
class Coverage:
    total: int
    used: int
    skipped_lineup: int
    skipped_thin: int


@dataclass(frozen=True)
class TestReport:
    __test__ = False  # not a pytest test class

    cells: list[CellResult]
    match: MatchResult
    coverage: Coverage


def _key(ipl_id: str, position: str) -> str:
    return f"{ipl_id}|{position}"


def _full_lineup(roster: list[dict]) -> bool:
    return sorted(e["position"] for e in roster) == sorted(LINEUP) and all(
        e["ipl_id"] is not None for e in roster
    )


def _is_thin(train: dict, entry: dict) -> bool:
    row = train["rows"]["all"].get(_key(entry["ipl_id"], entry["position"]))
    return row is None or row["games"] < TEST_MIN_POSITION_GAMES


def _ordered(roster: list[dict]) -> list[dict]:
    """The roster in ``POSITIONS`` order (stable, so scouts keep their order)."""
    return sorted(roster, key=lambda e: POSITIONS.index(e["position"]))


def _train_player(
    train: dict, ipl_id: str, name: str, position: str, constants: ImportConstants
) -> tuple[str, _PlayerData]:
    """``(role, _PlayerData)`` from the player's Training row at ``position``."""
    key = _key(ipl_id, position)
    return (
        position,
        player_data(
            ipl_id,
            name,
            train["rows"]["all"][key],
            train["baselines"]["all"][position],
            train["rows"]["loss"].get(key),
            train["baselines"]["loss"][position],
            position,
            constants,
        ),
    )


def _sim_roster(train: dict, roster: list[dict], constants: ImportConstants) -> list:
    """``(role, _PlayerData)`` pairs from each player's Training row at the position played."""
    return [
        _train_player(
            train, entry["ipl_id"], entry["callsign"], entry["position"], constants
        )
        for entry in roster
    ]


def _ranks(values: list[float]) -> list[float]:
    """1-based ranks, ties sharing their average rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's rho (Pearson over average ranks); None below 3 or with no spread."""
    if len(xs) < SPEARMAN_MIN_PLAYERS:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _run_rounds(tasks: list[tuple], workers: int) -> list[dict]:
    """``calibration_round_worker`` over ``tasks``, in order, serial or pooled."""
    if workers and workers > 1:
        from concurrent.futures import ProcessPoolExecutor  # noqa: PLC0415

        with ProcessPoolExecutor(
            max_workers=workers, initializer=worker_django_init
        ) as executor:
            return list(executor.map(calibration_round_worker, tasks))
    return [calibration_round_worker(task) for task in tasks]


def _check_engaged(position: str, rounds: list[dict[str, float]]) -> None:
    """Raise unless some round at ``position`` made a tag or took one."""
    if not any(r["avg_hit_diff"] or r["avg_times_hit"] for r in rounds):
        raise ValueError(
            f"No {position} player tagged or was tagged in any simulated round; "
            "check the map's spawn cells reach the floor"
        )


def _build_cells(
    sim_rounds: dict[tuple[str, str], list[dict[str, float]]],
    weights: dict[tuple[str, str], int],
    real_value: Callable[[tuple[str, str], str], float],
    min_ranked: int,
) -> list[CellResult]:
    """One ``CellResult`` per applicable (Role, metric), in ``POSITIONS`` / ``METRICS`` order.

    ``sim_rounds`` maps each ``(ipl, position)`` to its simulated per-round
    metric dicts; ``weights`` gives each its weight in ``real_mean`` (the sum
    is ``n_scorecards``); ``real_value(player_pos, metric)`` is the real average it is
    compared with. ``spearman`` ranks the players whose weight is at least
    ``min_ranked``. A position with no player has no cells.

    Raises:
        ValueError: when no player at a position tagged or was tagged in any
            round: the sim never engaged them (a spawn cut off from the floor,
            say), so its numbers are not a calibration result.
    """
    cells: list[CellResult] = []
    for position in POSITIONS:
        players = [player_pos for player_pos in weights if player_pos[1] == position]
        if not players:
            continue
        _check_engaged(position, [r for p in players for r in sim_rounds[p]])
        n = sum(weights[player_pos] for player_pos in players)
        for metric, kind, positions in METRICS:
            if position not in positions:
                continue
            real = {
                player_pos: real_value(player_pos, metric) for player_pos in players
            }
            sim = {
                player_pos: [r[metric] for r in sim_rounds[player_pos]]
                for player_pos in players
            }
            all_sim = [v for player_pos in players for v in sim[player_pos]]
            sim_mean = sum(all_sim) / len(all_sim)
            real_mean = (
                sum(real[player_pos] * weights[player_pos] for player_pos in players)
                / n
            )
            ranked = [
                player_pos
                for player_pos in players
                if weights[player_pos] >= min_ranked
            ]
            spearman = _spearman(
                [sum(sim[player_pos]) / len(sim[player_pos]) for player_pos in ranked],
                [real[player_pos] for player_pos in ranked],
            )
            cells.append(
                CellResult(
                    position=position,
                    metric=metric,
                    sim_mean=sim_mean,
                    real_mean=real_mean,
                    n_scorecards=n,
                    passed=within_tolerance(kind, sim_mean, real_mean),
                    spearman=spearman,
                )
            )
    return cells


def run_test(
    train: dict,
    test: dict,
    movement_ctx: Any,
    *,
    constants: ImportConstants = DEFAULT_CONSTANTS,
    rounds: int,
    seed: int,
    workers: int = 1,
) -> TestReport:
    """Replay each usable Test game ``rounds`` times and compare with the real numbers.

    ``train`` is the Training file's dict (``rows.all`` / ``rows.loss`` keyed
    ``"<ipl>|<position>"``, ``baselines.all`` / ``.loss`` per position);
    ``test`` the Test file's (``games``, ``averages`` keyed the same way).

    A game is skipped as ``skipped_lineup`` when either side is not exactly
    ``LINEUP`` or has a null ``ipl_id``, and otherwise as ``skipped_thin`` when
    any player has no Training row, or fewer than ``TEST_MIN_POSITION_GAMES``
    Training games, at the position played. Each player is built by
    ``player_data`` from that row, with the preferred-role boost.

    Fire plays red and Ice blue. Per-round seeds are
    ``random.Random(seed).getrandbits(63)``, drawn in game order then round
    order, so ``workers=1`` and ``workers=N`` return equal reports.

    ``real_mean`` is the mean of the used scorecards' Internationals averages
    at that position (each player's ``averages`` value weighted by their used
    scorecards there); ``sim_mean`` the mean over those scorecards' simulated
    rounds. ``spearman`` ranks players with at least
    ``SPEARMAN_MIN_SCORECARDS`` used scorecards at the position by their
    simulated mean against their Internationals average.

    Raises:
        ValueError: when ``rounds`` is below 1, or a used scorecard has no
            Internationals average for a metric it is compared on.
    """
    if rounds < 1:
        raise ValueError(f"rounds must be at least 1, got {rounds}")
    used: list[dict] = []
    skipped_lineup = skipped_thin = 0
    for game in test["games"]:
        sides = {team["colour"]: team for team in game["teams"]}
        if sorted(sides) != sorted(COLOUR_SIDE) or len(game["teams"]) != 2:
            skipped_lineup += 1
            continue
        if not all(_full_lineup(team["roster"]) for team in sides.values()):
            skipped_lineup += 1
            continue
        if any(
            _is_thin(train, entry)
            for team in sides.values()
            for entry in team["roster"]
        ):
            skipped_thin += 1
            continue
        used.append(sides)

    gen = random.Random(seed)
    tasks: list[tuple] = []
    for sides in used:
        red = _sim_roster(train, _ordered(sides["fire"]["roster"]), constants)
        blue = _sim_roster(train, _ordered(sides["ice"]["roster"]), constants)
        for _ in range(rounds):
            tasks.append((red, blue, movement_ctx, gen.getrandbits(63)))
    outputs = _run_rounds(tasks, workers)

    # (ipl, position) -> per-round metric dicts, and -> used scorecard count.
    sim_rounds: dict[tuple[str, str], list[dict[str, float]]] = {}
    scorecards: dict[tuple[str, str], int] = {}
    agree = 0
    margin_error = 0.0
    for g, sides in enumerate(used):
        game_outputs = outputs[g * rounds : (g + 1) * rounds]
        for colour, side in COLOUR_SIDE.items():
            for i, entry in enumerate(_ordered(sides[colour]["roster"])):
                player_pos = (entry["ipl_id"], entry["position"])
                scorecards[player_pos] = scorecards.get(player_pos, 0) + 1
                sim_rounds.setdefault(player_pos, []).extend(
                    o[side][i] for o in game_outputs
                )
        sim_margin = (
            sum(o["red_score"] - o["blue_score"] for o in game_outputs) / rounds
        )
        real_margin = sides["fire"]["score"] - sides["ice"]["score"]
        real_winner = (
            1
            if sides["fire"]["result"] == "win"
            else -1 if sides["ice"]["result"] == "win" else 0
        )
        agree += _sign(sim_margin) == real_winner
        margin_error += abs(sim_margin - real_margin)

    cells = _build_cells(
        sim_rounds,
        scorecards,
        lambda player_pos, metric: _real_average(test, player_pos, metric),
        SPEARMAN_MIN_SCORECARDS,
    )

    games = len(used)
    return TestReport(
        cells=cells,
        match=MatchResult(
            games=games,
            winner_agreement=agree / games if games else 0.0,
            mean_margin_error=margin_error / games if games else 0.0,
        ),
        coverage=Coverage(
            total=len(test["games"]),
            used=games,
            skipped_lineup=skipped_lineup,
            skipped_thin=skipped_thin,
        ),
    )


_METRIC_KIND: dict[str, str] = {metric: kind for metric, kind, _ps in METRICS}


def objective(cells: list[CellResult]) -> float:
    """Decision 25: the mean over cells of ``(err / max(|real|, floor)) ** 2``.

    ``err`` is ``sim_mean - real_mean``; ``floor`` is the cell kind's
    tolerance floor (0.5 for a count, 0.05 for a fraction), so a near-zero
    real value does not blow the error up.

    Raises:
        ValueError: when ``cells`` is empty.
    """
    if not cells:
        raise ValueError("objective needs at least one cell")
    total = 0.0
    for cell in cells:
        floor = TOLERANCE_FLOOR[_METRIC_KIND[cell.metric]]
        err = cell.sim_mean - cell.real_mean
        total += (err / max(abs(cell.real_mean), floor)) ** 2
    return total / len(cells)


# --- run_train --------------------------------------------------------------

# A (player, position) plays in run_train only with at least this many
# Training games at that position. It is the import's MIN_GAMES number, but the
# import applies it to a player's total games across positions.
MIN_TRAIN_GAMES = 26


@dataclass(frozen=True)
class TrainReport:
    """``run_train``'s cells (same shape as ``TestReport.cells``) and their objective."""

    cells: list[CellResult]
    objective: float


def _field_player(train: dict, position: str, constants: ImportConstants) -> tuple:
    """A field-average ``(role, _PlayerData)`` built from the position baseline."""
    baseline = train["baselines"]["all"][position]
    return (
        position,
        player_data(
            f"field-{position}",
            f"Field {position}",
            dict(baseline, position=position),
            baseline,
            None,
            train["baselines"]["loss"][position],
            position,
            constants,
        ),
    )


def _field_side(
    field: dict[str, tuple], player: tuple | None = None
) -> tuple[list, int]:
    """A ``LINEUP``-ordered side of field players, ``player`` in its Role's first slot.

    Returns the side and ``player``'s index in it (-1 without one).
    """
    side: list = []
    index = -1
    for position in LINEUP:
        if player is not None and index < 0 and player[0] == position:
            index = len(side)
            side.append(player)
        else:
            side.append(field[position])
    return side, index


def run_train(
    train: dict,
    movement_ctx: Any,
    *,
    constants: ImportConstants = DEFAULT_CONSTANTS,
    rounds: int,
    seed: int,
    workers: int = 1,
) -> TrainReport:
    """Simulate each qualifying Training player at their Role in a field-average team.

    Every ``"<ipl>|<position>"`` row of ``train["rows"]["all"]`` with at least
    ``MIN_TRAIN_GAMES`` games plays ``rounds`` rounds as red at that position;
    the other five red players and all six blue are field-average players built
    from ``train["baselines"]["all"]`` through ``player_data`` (each at its own
    Role, so boosted like the player). Players run in sorted key order; per-round
    seeds are ``random.Random(seed).getrandbits(63)`` in player then round
    order, so ``workers=1`` and ``workers=N`` return equal reports.

    Each cell's ``real_mean`` is the unweighted mean of the players' Training
    averages (every player plays the same number of rounds); ``n_scorecards``
    is the number of players; ``spearman`` ranks all of them.

    Raises:
        ValueError: when ``rounds`` is below 1, no row qualifies, or a
            qualifying row has no value for a metric it is compared on.
    """
    if rounds < 1:
        raise ValueError(f"rounds must be at least 1, got {rounds}")
    rows = train["rows"]["all"]
    keys = sorted(k for k, row in rows.items() if row["games"] >= MIN_TRAIN_GAMES)
    if not keys:
        raise ValueError(
            f"No Training row has at least {MIN_TRAIN_GAMES} games at its position"
        )
    field = {p: _field_player(train, p, constants) for p in POSITIONS}
    blue, _ = _field_side(field)

    gen = random.Random(seed)
    tasks: list[tuple] = []
    slots: list[tuple[tuple[str, str], int]] = []
    for key in keys:
        row = rows[key]
        position = row["position"]
        ipl_id = key.split("|", 1)[0]
        player = _train_player(
            train, ipl_id, row.get("callsign", ipl_id), position, constants
        )
        red, index = _field_side(field, player)
        slots.append(((ipl_id, position), index))
        for _ in range(rounds):
            tasks.append((red, blue, movement_ctx, gen.getrandbits(63)))
    outputs = _run_rounds(tasks, workers)

    sim_rounds: dict[tuple[str, str], list[dict[str, float]]] = {}
    for p, (player_pos, index) in enumerate(slots):
        sim_rounds[player_pos] = [
            o["red"][index] for o in outputs[p * rounds : (p + 1) * rounds]
        ]

    def real_value(player_pos: tuple[str, str], metric: str) -> float:
        value = rows[_key(*player_pos)].get(metric)
        if value is None:
            raise ValueError(f"Training row {_key(*player_pos)} has no {metric}")
        return float(value)

    cells = _build_cells(
        sim_rounds, {player_pos: 1 for player_pos, _ in slots}, real_value, 1
    )
    return TrainReport(cells=cells, objective=objective(cells))


# --- search -----------------------------------------------------------------

# The coordinate search stops after this many passes even if the last changed something.
MAX_SEARCH_PASSES = 3


@dataclass(frozen=True)
class SearchStep:
    """One evaluated candidate. Pass 0 is the start (``field`` / ``value`` None)."""

    pass_index: int
    field: str | None
    value: float | None
    objective: float
    # True when this candidate became the new best.
    kept: bool


@dataclass(frozen=True)
class SearchResult:
    constants: ImportConstants
    objective: float
    history: list[SearchStep]


def search(
    train: dict,
    movement_ctx: Any,
    *,
    start: ImportConstants,
    grid: Mapping[str, Sequence[float]],
    rounds: int,
    seed: int,
    workers: int = 1,
    on_step: Callable[[SearchStep], None] | None = None,
) -> SearchResult:
    """Coordinate search over ``ImportConstants`` fields, scored by ``run_train``'s objective.

    Fields are tried in the dataclass's declaration order (not ``grid``'s),
    each over its ``grid`` values other than the current one, keeping a value
    only when it strictly lowers the objective. The search repeats until a full
    pass keeps nothing, or ``MAX_SEARCH_PASSES`` passes have run. Every
    evaluation uses the same ``seed``, so a candidate's score is reproducible and
    each distinct constants is simulated once (repeats come from a cache, and
    still appear in ``history``). Only ``train`` is read: there is no Test input.
    ``on_step``, when given, receives each ``SearchStep`` as soon as it is
    scored, so a run of hours shows its progress.

    Raises:
        ValueError: when ``grid`` names a field ``ImportConstants`` lacks.
    """
    order = [f.name for f in fields(ImportConstants)]
    unknown = sorted(set(grid) - set(order))
    if unknown:
        raise ValueError(f"Unknown ImportConstants field(s): {', '.join(unknown)}")

    cache: dict[ImportConstants, float] = {}

    def evaluate(constants: ImportConstants) -> float:
        if constants not in cache:
            cache[constants] = run_train(
                train,
                movement_ctx,
                constants=constants,
                rounds=rounds,
                seed=seed,
                workers=workers,
            ).objective
        return cache[constants]

    history: list[SearchStep] = []

    def record(step: SearchStep) -> None:
        history.append(step)
        if on_step is not None:
            on_step(step)

    best, best_score = start, evaluate(start)
    record(SearchStep(0, None, None, best_score, True))
    for pass_index in range(1, MAX_SEARCH_PASSES + 1):
        changed = False
        for name in (n for n in order if n in grid):
            for value in grid[name]:
                if value == getattr(best, name):
                    continue
                candidate = replace(best, **{name: value})
                score = evaluate(candidate)
                kept = score < best_score
                record(SearchStep(pass_index, name, value, score, kept))
                if kept:
                    best, best_score, changed = candidate, score, True
        if not changed:
            break
    return SearchResult(constants=best, objective=best_score, history=history)


def _real_average(test: dict, player_pos: tuple[str, str], metric: str) -> float:
    key = _key(*player_pos)
    row = test["averages"].get(key)
    if row is None or row.get(metric) is None:
        raise ValueError(f"Test averages have no {metric} for {key}")
    return float(row[metric])
