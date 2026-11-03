"""LFS-01 Calibration harness, Test side (spec #514, Implementation Decision 4)."""

from __future__ import annotations

import json
import math
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        "avg_mvp": float(calculate_mvp(_MvpView(player_state, result))),
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


def _sim_roster(train: dict, roster: list[dict], constants: ImportConstants) -> list:
    """``(role, _PlayerData)`` pairs from each player's Training row at the position played."""
    out = []
    for entry in roster:
        position = entry["position"]
        key = _key(entry["ipl_id"], position)
        out.append(
            (
                position,
                player_data(
                    entry["ipl_id"],
                    entry["callsign"],
                    train["rows"]["all"][key],
                    train["baselines"]["all"][position],
                    train["rows"]["loss"].get(key),
                    train["baselines"]["loss"][position],
                    position,
                    constants,
                ),
            )
        )
    return out


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
                pk = (entry["ipl_id"], entry["position"])
                scorecards[pk] = scorecards.get(pk, 0) + 1
                sim_rounds.setdefault(pk, []).extend(o[side][i] for o in game_outputs)
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

    cells: list[CellResult] = []
    for position in POSITIONS:
        players = [pk for pk in scorecards if pk[1] == position]
        if not players:
            continue
        n = sum(scorecards[pk] for pk in players)
        for metric, kind, positions in METRICS:
            if position not in positions:
                continue
            real = {pk: _real_average(test, pk, metric) for pk in players}
            sim = {pk: [r[metric] for r in sim_rounds[pk]] for pk in players}
            all_sim = [v for pk in players for v in sim[pk]]
            sim_mean = sum(all_sim) / len(all_sim)
            real_mean = sum(real[pk] * scorecards[pk] for pk in players) / n
            ranked = [pk for pk in players if scorecards[pk] >= SPEARMAN_MIN_SCORECARDS]
            spearman = _spearman(
                [sum(sim[pk]) / len(sim[pk]) for pk in ranked],
                [real[pk] for pk in ranked],
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


def _real_average(test: dict, pk: tuple[str, str], metric: str) -> float:
    key = _key(*pk)
    row = test["averages"].get(key)
    if row is None or row.get(metric) is None:
        raise ValueError(f"Test averages have no {metric} for {key}")
    return float(row[metric])
