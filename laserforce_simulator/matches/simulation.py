import math as _math
import random
import logging
from typing import Iterator, Optional
from django.db import transaction
from .models import GameEvent, Match, GameRound, PlayerRoundState
from .sim_helpers.mechanics import shot_cooldown
from .sim_helpers.pathfinding import (
    astar_advance,
    astar_advance_cached,
    cells_to_move,
    choose_goal_cell,
)
from .sim_helpers.combat import (
    _NEUTRAL_BASE_TYPES,
    _can_tag_through_windowed_wall,
    _get_los_targets,
    _get_base_interaction,
    elevation_hit_modifier,
    _elevation_hit_modifier,
    plan_action,
    attempt_resupply as _attempt_resupply_shared,
    capture_base as _capture_base_shared,
    award_bases as _award_bases_shared,
    start_missile_lock as _start_missile_lock_shared,
    tick_missile_lock as _tick_missile_lock_shared,
)
from .sim_helpers.pending_events import (
    PendingMissileLock,
    PendingNuke,
    PendingFollowup,
    PendingReaction,
)
from .sim_helpers.down import record_down
from .sim_helpers.event_log import EventLog
from .sim_helpers.round_context import RoundContext
from .sim_helpers.shot import (
    SHOT_KIND_FOLLOW_UP,
    SHOT_KIND_INITIAL,
    SHOT_KIND_OVERWATCH,
    SHOT_KIND_REACTION,
    resolve_shot,
)
from .sim_helpers.spawn_assigner import assign_spawn_cells
from .sim_helpers.map_loader import (
    load_map_context,
    zone_from_cell,
)
from .sim_helpers.resupply_queue import resolve_resupply_requests
from .sim_helpers.time_constants import (
    MEDIC_UNDER_FIRE_WINDOW_TICKS,
    SCORE_BROADCAST_PERIOD_TICKS,
    SURVIVED_SENTINEL,
    TEAM_ELIM_BONUS_CUTOFF_TICKS,
    TICKS_PER_ROUND,
)


def _str_tag_id(player) -> str:
    """Return a consistent string tag ID for memory system usage.

    PlayerState objects have a string ``tag_id`` field directly.
    PlayerRoundState objects use ``string_tag_id`` property (added in MECH-06).
    """
    string_tag = getattr(player, "string_tag_id", None)
    if string_tag is not None:
        return string_tag
    return str(getattr(player, "tag_id", f"{player.team_color}_{player.role}"))


def _observe_lives(observer, seen) -> int | None:
    """Roll to observe seen player's current lives based on observer's resource_awareness.

    Chance = min(75, resource_awareness/4 + (resource_awareness/100) * (1-lives_ratio) * 50).
    At ra=100, lives_ratio=0.05 the chance is ~72.5%; at lives_ratio=0 it caps at 75%.
    Returns current lives on success, None on failure.
    """
    ra = getattr(observer, "resource_awareness", 50)
    max_lives = getattr(seen, "max_lives", getattr(seen, "starting_lives", 1))
    lives_ratio = seen.final_lives / max(1, max_lives)
    base_pct = ra / 4.0
    enemy_low_factor = max(0.0, 1.0 - lives_ratio) * (ra / 100.0)
    chance = min(75.0, base_pct + enemy_low_factor * 50.0)
    if random.random() * 100 < chance:
        return seen.final_lives
    return None


def _update_player_memory(observer, seen_players: list, second: float) -> bool:
    """MECH-06: update observer's memory with directly observed players.

    Cell, role, and status are always recorded. Current lives are added when the
    observer wins a resource_awareness roll (see _observe_lives).

    Returns True if any entry was new or had a changed cell or status — i.e. the
    memory actually gained information worth broadcasting to teammates.
    """
    observer_memory = getattr(observer, "player_memory", None)
    if observer_memory is None:
        observer.player_memory = {}
        observer_memory = observer.player_memory
    changed = False
    for seen in seen_players:
        if seen.cell_row is not None:
            tag_id = _str_tag_id(seen)
            new_cell = (seen.cell_row, seen.cell_col)
            if not seen.is_taggable_at(second):
                status = "downed"
            elif not seen.is_active_at(second):
                status = "reset_window"
            else:
                status = "active"
            existing = observer_memory.get(tag_id)
            if (
                existing is None
                or existing.get("cell") != new_cell
                or existing.get("status") != status
            ):
                changed = True
            entry: dict = {
                "cell": new_cell,
                "timestamp": second,
                "role": seen.role,
                "status": status,
            }
            observed_lives = _observe_lives(observer, seen)
            if observed_lives is not None:
                entry["lives"] = observed_lives
            observer_memory[tag_id] = entry
    return changed


def _broadcast_communication(
    actor,
    all_alive: list,
    movement_ctx,
    second: float,
) -> None:
    """MECH-06: per-tick communication broadcast.

    Rolls actor.communication / 100 probability. On success, shares actor's memory
    entries for enemy players with all living allies within the communication range
    (Euclidean half-diagonal of the map).
    """
    communication = getattr(actor, "communication", 0)
    if communication <= 0:
        return
    if random.random() * 100 >= communication:
        return

    # Compute communication range from map dimensions
    if movement_ctx is not None:
        zone_data = (
            movement_ctx.get_zone_data()
            if hasattr(movement_ctx, "get_zone_data")
            else movement_ctx.get("zone_data")
        )
        if zone_data:
            rows = len(zone_data)
            cols = len(zone_data[0]) if rows else 0
            comm_range = _math.sqrt(rows**2 + cols**2) / 2.0
        else:
            comm_range = float("inf")
    else:
        comm_range = float("inf")

    actor_r = actor.cell_row
    actor_c = actor.cell_col
    actor_team = actor.team_color
    actor_memory = getattr(actor, "player_memory", None)
    if not actor_memory:
        return

    # Filter to enemy-only memory entries, then pick the single highest-priority one.
    # Priority order (most tactically important first): heavy → commander → medic → ammo → scout
    _COMM_ROLE_PRIORITY = {
        "heavy": 0,
        "commander": 1,
        "medic": 2,
        "ammo": 3,
        "scout": 4,
    }
    enemy_color = "blue" if actor_team == "red" else "red"
    best_tag_id = None
    best_entry = None
    best_priority = len(_COMM_ROLE_PRIORITY)
    for tag_id, entry in actor_memory.items():
        if not (isinstance(tag_id, str) and tag_id.startswith(enemy_color)):
            continue
        priority = _COMM_ROLE_PRIORITY.get(
            entry.get("role", ""), len(_COMM_ROLE_PRIORITY)
        )
        if priority < best_priority:
            best_priority = priority
            best_tag_id = tag_id
            best_entry = entry
    if best_entry is None:
        return

    for ally in all_alive:
        if ally.team_color != actor_team or ally is actor:
            continue
        if ally.cell_row is None or ally.cell_col is None:
            continue
        # Check distance
        if actor_r is not None and actor_c is not None:
            dist = _math.sqrt(
                (ally.cell_row - actor_r) ** 2 + (ally.cell_col - actor_c) ** 2
            )
            if dist > comm_range:
                continue
        ally_memory = getattr(ally, "player_memory", None)
        if ally_memory is None:
            ally.player_memory = {}
            ally_memory = ally.player_memory
        existing = ally_memory.get(best_tag_id)
        if existing is None or best_entry["timestamp"] > existing["timestamp"]:
            ally_memory[best_tag_id] = dict(best_entry)


def _apply_score_broadcast(
    all_alive: list,
    second: float,
    period: int = SCORE_BROADCAST_PERIOD_TICKS,
) -> None:
    """MECH-06: every ``period`` time-units, compute which team is winning and
    update score_broadcast_state.

    Stores {"winning_team": "red"|"blue"|"tied", "timestamp": second} on each
    player. Players whose score_broadcast_next <= second get the update.

    TIME-01: ``period`` is the broadcast cadence in the caller's time domain.
    BatchSimulator (tick-native) uses the default SCORE_BROADCAST_PERIOD_TICKS;
    ResourceBasedSimulator passes its seconds-domain cadence (180) explicitly so
    its internal behaviour stays byte-identical.
    """
    red_pts = sum(p.points_scored for p in all_alive if p.team_color == "red")
    blue_pts = sum(p.points_scored for p in all_alive if p.team_color == "blue")
    if red_pts > blue_pts:
        winning_team = "red"
    elif blue_pts > red_pts:
        winning_team = "blue"
    else:
        winning_team = "tied"

    for player in all_alive:
        next_broadcast = getattr(player, "score_broadcast_next", period)
        if second >= next_broadcast:
            player.score_broadcast_state = {
                "winning_team": winning_team,
                "timestamp": second,
            }
            player.score_broadcast_next = next_broadcast + period


def _apply_nuke_activation_broadcast(
    commander,
    target_team_players: list,
    second: float,
) -> None:
    """MECH-06: when a nuke is activated, all alive enemy-team players learn the
    Commander's current cell via memory update.
    """
    if commander.cell_row is None:
        return
    cmd_tag = _str_tag_id(commander)
    for p in target_team_players:
        if p.final_lives <= 0:
            continue
        p_memory = getattr(p, "player_memory", None)
        if p_memory is None:
            p.player_memory = {}
            p_memory = p.player_memory
        p_memory[cmd_tag] = {
            "cell": (commander.cell_row, commander.cell_col),
            "timestamp": second,
            "role": "commander",
        }


def _check_medic_under_fire(
    medic,
    all_alive: list,
    second: float,
    window: int = MEDIC_UNDER_FIRE_WINDOW_TICKS,
) -> None:
    """MECH-06: when a Medic is hit 2× within ``window``, alert all living teammates.

    Appends current second to medic.medic_hit_times, trims entries older than
    ``window``, and if ≥ 2 hits remain, updates all alive teammates' memory
    with the medic's cell.

    TIME-01: ``window`` is in the caller's time domain. BatchSimulator uses the
    default MEDIC_UNDER_FIRE_WINDOW_TICKS; ResourceBasedSimulator passes its
    seconds-domain window (12) explicitly so its behaviour stays byte-identical.
    """
    hit_times = getattr(medic, "medic_hit_times", None)
    if hit_times is None:
        medic.medic_hit_times = []
        hit_times = medic.medic_hit_times
    hit_times.append(second)
    # Trim entries older than the window
    medic.medic_hit_times = [t for t in hit_times if second - t <= window]
    if len(medic.medic_hit_times) >= 2 and medic.cell_row is not None:
        medic_tag = _str_tag_id(medic)
        for p in all_alive:
            if p.team_color != medic.team_color or p is medic:
                continue
            if p.final_lives <= 0:
                continue
            p_memory = getattr(p, "player_memory", None)
            if p_memory is None:
                p.player_memory = {}
                p_memory = p.player_memory
            p_memory[medic_tag] = {
                "cell": (medic.cell_row, medic.cell_col),
                "timestamp": second,
                "role": "medic",
            }


def _apply_nuke_reaction_flags(all_alive: list, pending_nukes: list) -> None:
    """MECH-04: reset then set reacting_to_nuke for all alive players each tick.

    Caches game_awareness and player_awareness once per player so repeated
    @property calls (which hit stat_for_simulation) don't multiply with the
    number of pending nukes.
    """
    for p in all_alive:
        setattr(p, "reacting_to_nuke", False)
    if not pending_nukes:
        return
    awareness = {id(p): (p.game_awareness, p.player_awareness) for p in all_alive}
    for pending_nuke in pending_nukes:
        target_color = "blue" if pending_nuke.player.team_color == "red" else "red"
        for p in all_alive:
            if p.team_color != target_color:
                continue
            ga, pa = awareness[id(p)]
            if random.random() < (ga + pa) / 200.0:
                setattr(p, "reacting_to_nuke", True)


# NOTE: ``_actor_meta`` / ``_target_meta`` / ``_build_meta`` are gone —
# the EventLog candidate moved the RES-02b universal metadata-snapshot
# helpers into ``sim_helpers.event_log`` as private to the EventLog
# class (single source of truth for the GameEvent-dict shape).
#
# NOTE: ``_resupply_event_dict`` and the resupply-side ``_batch_emit`` lambda
# inside ``_simulate_round`` are gone — the EventLog candidate collapsed the
# resupply_queue ↔ simulation callable seam. The resupply verbs
# (``ctx.events.resupply_lives`` / ``.resupply_ammo`` / ``.combo_resupply``)
# now own the wire-shape; helpers take ``ctx`` directly.


from matches.sim_helpers.role_constants import ROLE_STATS

# Module logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


# _elevation_hit_modifier, elevation_hit_modifier, _can_tag_through_windowed_wall,
# _get_los_targets, _get_base_interaction, and _NEUTRAL_BASE_TYPES are imported
# from sim_helpers/combat.py above and re-exported here for backward compatibility.


class _PlayerData:
    """Lightweight picklable stand-in for a Player ORM object.

    Holds pre-computed stat_for_simulation() values so worker processes
    can call _make_players() without touching the Django ORM.
    """

    def __init__(self, player_id: int, name: str, stats: dict) -> None:
        self.id = player_id
        self.name = name
        self._stats = stats

    def stat_for_simulation(self, stat_name: str, role: str) -> int:
        return self._stats[stat_name]


_SIMULATION_STATS = (
    "accuracy",
    "survival",
    "player_awareness",
    "game_awareness",
    "decision_making",
    "stamina",
    "special_usage",
    "resupply_efficiency",
    "resupply_synergy",
    "teamwork",
    "communication",
    "resource_awareness",
    "speed",
)


def _precompute_roster(roster) -> list:
    """Convert a roster of (role, Player) tuples into picklable (role, _PlayerData) pairs.

    Pre-computes all stat_for_simulation() values so worker processes never
    touch the Django ORM.
    """
    result = []
    for role, player_model in roster:
        stats = {
            s: player_model.stat_for_simulation(s, role) for s in _SIMULATION_STATS
        }
        result.append((role, _PlayerData(player_model.id, player_model.name, stats)))
    return result


def _chunk_size_for(n: int) -> int:
    """SIM-10: adaptive chunk size — ~50 snapshots per run regardless of n.

    Returns an int in ``[1, 25]`` chosen so a full run of ``n`` games yields
    roughly 50 progress snapshots: ``n // 50`` clamped into ``[1, 25]``. Used
    by :meth:`BatchSimulator.run_incremental` to decide how often to yield a
    partial-aggregate snapshot.
    """
    return max(1, min(25, n // 50))


class BatchSimulator:
    """Pure in-memory simulator for running N rounds without DB writes.

    Every computation uses PlayerState dataclass objects so there are no
    ORM saves, refreshes, or GameEvent inserts.  One round typically runs
    in ~25 ms instead of ~9 s, making 100-round batches feasible in <3 s.
    """

    ROLE_STARTING_RESOURCES = {
        "commander": {"lives": 15, "shots": 30, "special": 0, "missiles": 5},
        "heavy": {"lives": 10, "shots": 20, "special": 0, "missiles": 5},
        "scout": {"lives": 15, "shots": 30, "special": 0, "missiles": 0},
        "medic": {"lives": 20, "shots": 15, "special": 0, "missiles": 0},
        "ammo": {"lives": 10, "shots": 15, "special": 0, "missiles": 0},
    }

    # 0.5-second tick: models real shot speeds (regular=2/s, heavy=1/s).
    TICK = 0.5

    # SIM-09: round length, patchable in tests (e.g. ``patch.object(
    # BatchSimulator, "ROUND_TICKS", 40)``) so fast-path tests can simulate
    # a short round without monkeypatching the module-level constant.
    ROUND_TICKS = TICKS_PER_ROUND

    # SIM-09: per-side bonus points for eliminating the opposing team
    # within ``TEAM_ELIM_BONUS_CUTOFF_TICKS``. Applied per-Match in
    # ``simulate_match`` (mirrors the former ``ResourceBasedSimulator``
    # elimination bonus).
    elimination_bonus = 10000

    @staticmethod
    def _is_flipped(game_index: int) -> bool:
        """SIM-08: per-game orientation parity — flipped iff the index is odd.

        Single source of truth for the side-alternation rule, shared by
        ``_side_order`` (serial) and ``_run_parallel``. An even game count
        yields an exact 50/50 split; an odd count differs by exactly one
        game. Pure function of the loop index — NEVER consumes the RNG.
        """
        return bool(game_index & 1)

    @staticmethod
    def _side_order(
        game_index: int,
        red_roster: list,
        blue_roster: list,
    ) -> tuple[list, list, bool]:
        """SIM-08: deterministic per-game side assignment.

        Returns ``(side_red_roster, side_blue_roster, flipped)`` for game
        ``game_index``, with ``flipped`` from :meth:`_is_flipped`. When
        ``flipped`` is ``True`` the canonical blue roster is passed as the
        physical "red" side and vice versa, so ``_simulate_round`` sees the
        swapped rosters; callers de-flip results back to team position using
        the returned ``flipped`` flag.
        """
        flipped = BatchSimulator._is_flipped(game_index)
        if flipped:
            return blue_roster, red_roster, True
        return red_roster, blue_roster, False

    @staticmethod
    def _aggregate_batch(games: list[tuple[dict, int, bool]], n: int) -> dict:
        """SIM-08: build the run()/_run_parallel result dict from per-game
        ``(result, seed, flipped)`` triples.

        This is the SINGLE aggregation path shared by the serial and
        parallel runs, so the contractually-required serial==parallel
        guarantee (identical team-position aggregates AND ``side_advantage``
        for a given master_seed) is structurally impossible to break by
        drift between two copies of the logic.

        ``red_*`` / ``blue_*`` are TEAM-POSITION keyed (the team passed as
        the team_red / team_blue argument, de-flipped from the physical side
        it actually played); ``side_advantage`` carries the raw
        physical-side signal (whichever team really played red / blue).
        """
        red_wins = blue_wins = ties = 0
        red_scores: list = []
        blue_scores: list = []
        red_survivors_list: list = []
        blue_survivors_list: list = []
        # Each entry is (team_position_score_diff, seed, flipped).
        round_seeds: list = []
        # Raw PHYSICAL-side accumulators for the side_advantage dict.
        red_side_wins = blue_side_wins = side_ties = 0
        red_side_scores: list = []
        blue_side_scores: list = []

        for result, s, flipped in games:
            phys_rp, phys_bp = result["red_points"], result["blue_points"]
            # De-flip the physical-side result back to team position.
            if flipped:
                rp, bp = phys_bp, phys_rp
                red_surv = result["blue_survivors"]
                blue_surv = result["red_survivors"]
            else:
                rp, bp = phys_rp, phys_bp
                red_surv = result["red_survivors"]
                blue_surv = result["blue_survivors"]

            round_seeds.append((rp - bp, s, flipped))
            if rp > bp:
                red_wins += 1
            elif bp > rp:
                blue_wins += 1
            else:
                ties += 1
            red_scores.append(rp)
            blue_scores.append(bp)
            red_survivors_list.append(red_surv)
            blue_survivors_list.append(blue_surv)

            # Physical-side aggregation (un-de-flipped).
            if phys_rp > phys_bp:
                red_side_wins += 1
            elif phys_bp > phys_rp:
                blue_side_wins += 1
            else:
                side_ties += 1
            red_side_scores.append(phys_rp)
            blue_side_scores.append(phys_bp)

        # Pick the 10 most average and 10 most outlier rounds by score diff.
        if round_seeds:
            mean_diff = sum(d for d, _, _ in round_seeds) / n
            ranked = sorted(round_seeds, key=lambda x: abs(x[0] - mean_diff))
            # JSON-safe [seed, flipped] pairs (lists, not tuples) so they
            # survive a Django session round-trip unchanged.
            avg_seeds = [[s, f] for _, s, f in ranked[:10]]
            outlier_seeds = [[s, f] for _, s, f in ranked[-10:]]
        else:
            avg_seeds = outlier_seeds = []

        avg = lambda lst: sum(lst) / len(lst) if lst else 0
        pct = lambda w: w / n * 100 if n else 0.0
        return {
            "n": n,
            "red_wins": red_wins,
            "blue_wins": blue_wins,
            "ties": ties,
            "red_win_pct": pct(red_wins),
            "blue_win_pct": pct(blue_wins),
            "avg_red_score": avg(red_scores),
            "avg_blue_score": avg(blue_scores),
            "avg_red_survivors": avg(red_survivors_list),
            "avg_blue_survivors": avg(blue_survivors_list),
            "red_scores": red_scores,
            "blue_scores": blue_scores,
            "avg_seeds": avg_seeds,
            "outlier_seeds": outlier_seeds,
            # Raw map-side advantage signal across the batch.
            "side_advantage": {
                "n": n,
                "red_side_wins": red_side_wins,
                "blue_side_wins": blue_side_wins,
                "side_ties": side_ties,
                "red_side_win_pct": pct(red_side_wins),
                "blue_side_win_pct": pct(blue_side_wins),
                "avg_red_side_score": avg(red_side_scores),
                "avg_blue_side_score": avg(blue_side_scores),
            },
        }

    def run(
        self,
        team_red,
        team_blue,
        n: int = 100,
        *,
        arena_map=None,
        workers: Optional[int] = None,
        master_seed: Optional[int] = None,
    ) -> dict:
        """Simulate n rounds and return aggregate statistics.

        SIM-10: re-implemented as a consumer of :meth:`run_incremental` — the
        generator is the sole game-loop and the sole caller of
        :meth:`_aggregate_batch`. Behaviour and return value are unchanged
        from the caller's perspective.

        Loads team rosters from the DB once upfront, then runs n purely
        in-memory rounds and aggregates the outcomes. Pass arena_map to enable
        cell-aware pathfinding movement; omit for the 3-zone fallback.

        workers: number of parallel worker processes.  None / 1 = serial.
        Values > 1 dispatch rounds to a ProcessPoolExecutor; set to
        os.cpu_count() for full parallelism.

        master_seed: 63-bit integer seeding the per-round seed generator.
        When None, a fresh OS-entropy generator picks one (independent of the
        global RNG). Serial and parallel paths derive identical per-round
        seeds from the same master_seed, so a given master_seed always
        reproduces the same batch of games.
        """
        last = None
        for snap in self.run_incremental(
            team_red,
            team_blue,
            n,
            arena_map=arena_map,
            workers=workers,
            master_seed=master_seed,
        ):
            last = snap
        if last is None:
            # Defensive: run_incremental always yields at least once (even for
            # n == 0 it emits the terminal empty-aggregate snapshot). This
            # branch is unreachable under the current contract.
            return self._aggregate_batch([], 0)
        return last["aggregate"]

    def run_incremental(
        self,
        team_red,
        team_blue,
        n: int = 100,
        *,
        arena_map=None,
        workers: Optional[int] = None,
        master_seed: Optional[int] = None,
    ) -> Iterator[dict]:
        """SIM-10: Generator twin of :meth:`run`. Yields partial-aggregate
        snapshots at chunk boundaries (submission-indexed); the final yielded
        snapshot's ``aggregate`` dict equals what :meth:`run` would return
        for the same args.

        Each snapshot is a dict with exactly three keys:

        - ``completed`` (``int``): number of games included in ``aggregate``;
          monotonic non-decreasing across yields; final yield has
          ``completed == n``.
        - ``total`` (``int``): equal to ``n`` for every yield.
        - ``aggregate`` (``dict``): the existing :meth:`_aggregate_batch`
          output dict over games ``[0..completed)`` (submission-indexed).

        Serial (``workers in (None, 1)``) and parallel (``workers > 1``)
        paths produce **identical snapshots at every chunk boundary** for
        the same ``master_seed``.

        Fail-fast: the first failing per-game ``.result()`` cancels every
        pending future (best-effort) and re-raises the original exception
        out of the generator. In serial mode the exception simply propagates.

        ``n == 0`` yields one terminal snapshot with an empty aggregate and
        returns.
        """
        # Read rosters once — list of (role, Player) tuples
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)

        movement_ctx, _ = load_map_context(arena_map)

        if master_seed is None:
            master_seed = random.Random().getrandbits(63)
        gen = random.Random(master_seed)

        # n == 0: one terminal snapshot, empty aggregate.
        if n <= 0:
            yield {
                "completed": 0,
                "total": 0,
                "aggregate": self._aggregate_batch([], 0),
            }
            return

        chunk = _chunk_size_for(n)

        if workers and workers > 1:
            yield from self._run_incremental_parallel(
                red_roster, blue_roster, n, movement_ctx, workers, gen, chunk
            )
            return

        # Serial path — submission-indexed snapshots at every chunk boundary.
        games: list[tuple[dict, int, bool]] = []
        for i in range(n):
            s = gen.getrandbits(63)
            side_red, side_blue, flipped = self._side_order(i, red_roster, blue_roster)
            random.seed(s)
            result, _, _ = self._simulate_round(
                side_red, side_blue, movement_ctx=movement_ctx
            )
            games.append((result, s, flipped))

            completed = i + 1
            # Emit on every multiple of chunk; the final boundary (completed
            # == n) is always emitted because it is either a multiple of
            # chunk or hits the explicit final-emission branch below.
            if completed % chunk == 0 or completed == n:
                yield {
                    "completed": completed,
                    "total": n,
                    "aggregate": self._aggregate_batch(games[:completed], completed),
                }

    def _run_incremental_parallel(
        self,
        red_roster,
        blue_roster,
        n: int,
        movement_ctx,
        workers: int,
        gen: random.Random,
        chunk: int,
    ) -> Iterator[dict]:
        """SIM-10: parallel branch of :meth:`run_incremental`.

        Submits all ``n`` games up front to a ``ProcessPoolExecutor``,
        drains via ``as_completed`` for liveness, but **gates** snapshot
        emission on a ``pending_boundary`` watermark so snapshots are
        emitted in strict submission-index order — identical to the
        serial path at every chunk boundary for a given ``master_seed``.

        Fail-fast: the first ``.result()`` exception cancels every still
        pending future (best-effort) and re-raises out of the generator;
        the ``ProcessPoolExecutor`` context manager handles pool shutdown.
        """
        from concurrent.futures import ProcessPoolExecutor, as_completed
        from matches.sim_helpers.parallel_worker import (
            batch_round_worker,
            worker_django_init,
        )

        red_data = _precompute_roster(red_roster)
        blue_data = _precompute_roster(blue_roster)

        # Generate n distinct integer seeds and orientations in the parent
        # — identical to the serial path so a given master_seed produces
        # the same per-game (seed, flipped) pairs in both modes.
        seeds = [gen.getrandbits(63) for _ in range(n)]
        flips = [self._is_flipped(i) for i in range(n)]

        # Pre-allocated per-game results in submission order.
        games_results: list[Optional[tuple[dict, int, bool]]] = [None] * n

        with ProcessPoolExecutor(
            max_workers=workers, initializer=worker_django_init
        ) as executor:
            future_to_index: dict = {}
            for i in range(n):
                future = executor.submit(
                    batch_round_worker,
                    (red_data, blue_data, movement_ctx, seeds[i], flips[i]),
                )
                future_to_index[future] = i

            pending_boundary = chunk  # Next boundary awaiting emission.

            try:
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    try:
                        result = future.result()
                    except BaseException:
                        # Fail-fast: cancel every future (a no-op on the
                        # one that just raised and on any already-completed
                        # ones; effective on still-pending submissions).
                        for f in future_to_index:
                            f.cancel()
                        raise
                    games_results[idx] = (result, seeds[idx], flips[idx])

                    # Emit every now-ready boundary in order. A single
                    # as_completed step can bridge multiple boundaries
                    # (a long gap of contiguous slots fills at once), so
                    # loop until the next boundary is not yet ready.
                    while pending_boundary <= n and all(
                        games_results[j] is not None for j in range(pending_boundary)
                    ):
                        completed = pending_boundary
                        # All slots [0..completed) are populated by the
                        # all(...) gate above; the cast strips the Optional.
                        completed_games = [
                            g for g in games_results[:completed] if g is not None
                        ]
                        yield {
                            "completed": completed,
                            "total": n,
                            "aggregate": self._aggregate_batch(
                                completed_games, completed
                            ),
                        }
                        # Step the watermark; the final boundary is n even
                        # when n is not a multiple of chunk.
                        if pending_boundary == n:
                            pending_boundary = n + 1
                            break
                        next_boundary = pending_boundary + chunk
                        pending_boundary = min(next_boundary, n)
            finally:
                # Context-manager __exit__ shuts down the pool; nothing
                # else to do here. (Kept as an explicit anchor for the
                # fail-fast comment above.)
                pass

    # ------------------------------------------------------------------ #
    # SIM-09: view-path persistence (replaces ResourceBasedSimulator)
    # ------------------------------------------------------------------ #

    def _simulate_and_flush_round(
        self,
        team_red,
        team_blue,
        *,
        match,
        round_number: int,
        movement_ctx,
        arena_map,
        zone_size: int | None,
    ) -> "GameRound":
        """Draw a fresh 63-bit seed, ``random.seed()`` it, simulate one
        round in-memory via ``_simulate_round``, and persist it through
        ``_flush_to_db``.

        SIM-09 helper shared by :meth:`simulate_match` (called twice —
        round 1 with canonical args, round 2 with reversed args for the
        per-Match colour swap) and :meth:`simulate_single_round_detailed`.
        Each call draws its own independent seed, mirroring the per-round
        seed-draw in :meth:`run`. The colour swap is a property of how
        ``simulate_match`` *calls* this helper (which team it passes as
        ``team_red``), not of the helper itself — the helper always sees
        canonical "team_red plays red" inputs.
        """
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)

        seed = random.Random().getrandbits(63)
        random.seed(seed)

        events: list = []
        result, red_players, blue_players = self._simulate_round(
            red_roster,
            blue_roster,
            event_log=events,
            movement_ctx=movement_ctx,
        )
        return self._flush_to_db(
            team_red,
            team_blue,
            result,
            red_players,
            blue_players,
            events,
            rng_seed=seed,
            movement_ctx=movement_ctx,
            match=match,
            round_number=round_number,
            arena_map=arena_map,
            zone_size=zone_size,
        )

    @transaction.atomic
    def simulate_match(
        self,
        team_red,
        team_blue,
        match_type: str = "friendly",
        *,
        arena_map=None,
    ) -> Match:
        """Create a ``Match``, simulate its two rounds in-memory, and
        persist everything to the DB.

        SIM-09: replaces ``ResourceBasedSimulator.simulate_match``. The
        per-Match colour swap (round 2 with the team that was blue in
        round 1 playing red) is implemented here by *passing the rosters
        reversed* into the second :meth:`_simulate_and_flush_round`
        call — the stored ``GameRound.team_red`` in round 2 is literally
        the team that physically played red, and the swap is reflected
        when copying per-round point/elimination columns onto the Match.
        This per-Match swap is **distinct** from the SIM-08 batch
        Orientation alternation: there is no ``flipped`` flag, no
        odd/even index parity — every Match's round 2 reverses the
        rosters relative to round 1.

        Atomicity: one outer ``@transaction.atomic`` wraps the Match row
        creation, both round persistences (nested ``_flush_to_db``
        atomics become savepoints under Django's default behaviour), and
        the bonus / completion update. A mid-simulation failure rolls
        back the whole match so no half-built / 0-0 Match is left in the
        DB.
        """
        movement_ctx, zone_size = load_map_context(arena_map)

        match = Match.objects.create(
            team_red=team_red, team_blue=team_blue, match_type=match_type
        )

        # Round 1: canonical sides (team_red plays red, team_blue plays blue).
        round1 = self._simulate_and_flush_round(
            team_red,
            team_blue,
            match=match,
            round_number=1,
            movement_ctx=movement_ctx,
            arena_map=arena_map,
            zone_size=zone_size,
        )
        match.red_round1_points = round1.red_points
        match.blue_round1_points = round1.blue_points
        match.red_round1_eliminated = round1.red_team_eliminated
        match.blue_round1_eliminated = round1.blue_team_eliminated
        match.round1_eliminated_at = round1.eliminated_at

        # Round 2: per-Match colour swap — the team that was team_blue in
        # round 1 is passed as team_red here, so the stored sides reflect
        # what physically played which colour this round.
        round2 = self._simulate_and_flush_round(
            team_blue,
            team_red,
            match=match,
            round_number=2,
            movement_ctx=movement_ctx,
            arena_map=arena_map,
            zone_size=zone_size,
        )
        # round2.red_points is the score of the team that played red this
        # round (= the canonical team_blue argument), so swap when copying
        # onto the team-position-keyed Match columns.
        match.red_round2_points = round2.blue_points
        match.blue_round2_points = round2.red_points
        match.red_round2_eliminated = round2.blue_team_eliminated
        match.blue_round2_eliminated = round2.red_team_eliminated
        match.round2_eliminated_at = round2.eliminated_at

        # Per-side elimination bonus: a side whose opponent was eliminated
        # in a round earns ``elimination_bonus`` for that round.
        if match.red_round1_eliminated:
            match.blue_bonus_points += self.elimination_bonus
        if match.blue_round1_eliminated:
            match.red_bonus_points += self.elimination_bonus
        if match.red_round2_eliminated:
            match.blue_bonus_points += self.elimination_bonus
        if match.blue_round2_eliminated:
            match.red_bonus_points += self.elimination_bonus

        match.is_completed = True
        match.save()

        return match

    @transaction.atomic
    def simulate_single_round_detailed(
        self, team_red, team_blue, *, arena_map=None
    ) -> "GameRound":
        """Simulate a single standalone round (no parent Match) and
        persist it to the DB.

        SIM-09: replaces ``ResourceBasedSimulator.simulate_single_round_detailed``.
        Draws a fresh 63-bit seed, runs one ``_simulate_round``, and
        flushes through ``_flush_to_db`` with ``match=None``.
        """
        movement_ctx, zone_size = load_map_context(arena_map)
        return self._simulate_and_flush_round(
            team_red,
            team_blue,
            match=None,
            round_number=1,
            movement_ctx=movement_ctx,
            arena_map=arena_map,
            zone_size=zone_size,
        )

    # ------------------------------------------------------------------ #
    # LG-01 — Season-scheduled round simulation
    # ------------------------------------------------------------------ #

    @transaction.atomic
    def simulate_scheduled_round(
        self,
        season,
        team_a,
        team_b,
        round_number: int,
        *,
        arena_map=None,
    ) -> "GameRound":
        """Simulate one Round of a Season Match.

        LG-01 pure orchestration over the existing per-Round
        simulator. The two-Round Match progresses one Round at a
        time:

        * Round 1: find-or-create the Match (Side-agnostic lookup)
          and persist ``GameRound(round_number=1)``. ``Match`` stays
          ``is_completed=False`` so ``calculate_winner`` does not
          fire yet.
        * Round 2: find the existing Match Side-agnostically;
          simulate with **args reversed** (``team_red=team_b,
          team_blue=team_a`` — mirrors ``simulate_match``'s per-Match
          colour swap byte-for-byte); persist
          ``GameRound(round_number=2)``; set ``match.is_completed=True``
          and save so the ``save`` override triggers
          ``calculate_winner``.

        After persistence (either round), call
        ``season.complete_if_finished()`` so the Season auto-
        transitions to ``completed`` when the final fixture lands.

        Raises:
            ValueError: if ``season.state != "active"``.
            ValueError: if ``round_number not in (1, 2)``.
            ValueError: if ``round_number == 2`` and no existing
                Match found for these teams in this Season.
        """
        if season.state != "active":
            raise ValueError(
                f"Season must be active to simulate; got state={season.state!r}"
            )
        if round_number not in (1, 2):
            raise ValueError(f"round_number must be 1 or 2; got {round_number!r}")

        movement_ctx, zone_size = load_map_context(arena_map)

        # Side-agnostic Match lookup (the team that played red in one
        # round may play blue in the other — the per-Match colour
        # swap is applied at round 2). Two ORM queries, first match
        # wins.
        match = (
            Match.objects.filter(season=season)
            .filter(team_red=team_a, team_blue=team_b)
            .first()
        ) or (
            Match.objects.filter(season=season)
            .filter(team_red=team_b, team_blue=team_a)
            .first()
        )

        # =============== Round 1 ===============
        if round_number == 1:
            if match is None:
                match = Match.objects.create(
                    season=season,
                    team_red=team_a,
                    team_blue=team_b,
                    is_completed=False,
                )

            game_round = self._simulate_and_flush_round(
                team_a,
                team_b,
                match=match,
                round_number=1,
                movement_ctx=movement_ctx,
                arena_map=arena_map,
                zone_size=zone_size,
            )

            self._persist_round_results(
                match, game_round, round_number=1, swapped=False
            )
            match.save()  # is_completed stays False; no calculate_winner yet
            season.complete_if_finished()
            return game_round

        # =============== Round 2 ===============
        if match is None:
            raise ValueError(
                "No round 1 Match found for season="
                f"{getattr(season, 'id', None)!r} team_a={team_a!r} "
                f"team_b={team_b!r}; play round 1 first"
            )

        # Per-Match colour swap: pass the rosters reversed so the
        # stored GameRound reflects which team physically played red
        # this round.
        game_round = self._simulate_and_flush_round(
            team_b,
            team_a,
            match=match,
            round_number=2,
            movement_ctx=movement_ctx,
            arena_map=arena_map,
            zone_size=zone_size,
        )

        self._persist_round_results(match, game_round, round_number=2, swapped=True)
        match.is_completed = True
        match.save()  # triggers calculate_winner via the save override
        season.complete_if_finished()
        return game_round

    def _persist_round_results(
        self,
        match: "Match",
        game_round: "GameRound",
        *,
        round_number: int,
        swapped: bool,
    ) -> None:
        """Map one Round's outcome onto the Match's team-position-keyed columns.

        ``round_number`` selects which `*_roundN_*` columns to write.
        ``swapped`` is ``True`` iff the simulator was invoked with the
        team args reversed (the per-Match colour swap of round 2): when
        swapped, the GameRound's physical red side is team_b (the
        Match's `team_blue`), so red↔blue columns and elimination flags
        are crossed when written back to the Match. This is the single
        source of truth for the round-1/round-2 mapping.
        """
        if swapped:
            red_points = game_round.blue_points
            blue_points = game_round.red_points
            red_eliminated = game_round.blue_team_eliminated
            blue_eliminated = game_round.red_team_eliminated
        else:
            red_points = game_round.red_points
            blue_points = game_round.blue_points
            red_eliminated = game_round.red_team_eliminated
            blue_eliminated = game_round.blue_team_eliminated

        if round_number == 1:
            match.red_round1_points = red_points
            match.blue_round1_points = blue_points
            match.red_round1_eliminated = red_eliminated
            match.blue_round1_eliminated = blue_eliminated
            match.round1_eliminated_at = game_round.eliminated_at
        else:
            match.red_round2_points = red_points
            match.blue_round2_points = blue_points
            match.red_round2_eliminated = red_eliminated
            match.blue_round2_eliminated = blue_eliminated
            match.round2_eliminated_at = game_round.eliminated_at

        # Elimination bonus is symmetric: a team being eliminated this
        # round awards the bonus to its opponent's side of the Match.
        if red_eliminated:
            match.blue_bonus_points += self.elimination_bonus
        if blue_eliminated:
            match.red_bonus_points += self.elimination_bonus

    # ------------------------------------------------------------------ #
    # Internal round simulation
    # ------------------------------------------------------------------ #

    def _make_players(
        self,
        roster,
        team_color: str,
        spawn_cells: dict[str, tuple[int, int]] | None = None,
        zone_data: list[list[int]] | None = None,
        team_spawn_pools: dict | None = None,
    ):
        from .sim_helpers.player_state import PlayerState

        default_zone = 0 if team_color == "red" else 2
        base_spawn = spawn_cells.get(team_color) if spawn_cells else None

        # Pre-compute role-priority spawn assignments for the whole team (MAP-08).
        roster_list = list(roster)
        if team_spawn_pools and spawn_cells and zone_data is not None:
            roster_roles = [role for role, _ in roster_list]
            cell_assignments = assign_spawn_cells(
                roster_roles, team_color, spawn_cells, team_spawn_pools
            )
        else:
            cell_assignments = {}

        scout_index = 0
        players = []
        for idx, (role, player_model) in enumerate(roster_list):
            resources = self.ROLE_STARTING_RESOURCES[role]
            if role == "scout":
                scout_index += 1
                tag_id = f"{team_color}_scout_{scout_index}"
            else:
                tag_id = f"{team_color}_{role}"

            # Determine starting cell using pre-computed assignments (MAP-08).
            if cell_assignments:
                chosen = cell_assignments.get(idx)
                if chosen is not None:
                    cell_row: int | None = chosen[0]
                    cell_col: int | None = chosen[1]
                else:
                    cell_row = None
                    cell_col = None
            elif base_spawn is not None and zone_data is not None:
                cell_row = base_spawn[0]
                cell_col = base_spawn[1]
            else:
                cell_row = None
                cell_col = None

            if cell_row is not None and spawn_cells:
                starting_zone = zone_from_cell(cell_row, cell_col, spawn_cells)
            else:
                starting_zone = default_zone

            state = PlayerState(
                tag_id=tag_id,
                player_id=player_model.id,
                name=player_model.name,
                team_color=team_color,
                role=role,
                accuracy=player_model.stat_for_simulation("accuracy", role),
                survival=player_model.stat_for_simulation("survival", role),
                player_awareness=player_model.stat_for_simulation(
                    "player_awareness", role
                ),
                game_awareness=player_model.stat_for_simulation("game_awareness", role),
                decision_making=player_model.stat_for_simulation(
                    "decision_making", role
                ),
                stamina=player_model.stat_for_simulation("stamina", role),
                special_usage=player_model.stat_for_simulation("special_usage", role),
                resupply_efficiency=player_model.stat_for_simulation(
                    "resupply_efficiency", role
                ),
                resupply_synergy=player_model.stat_for_simulation(
                    "resupply_synergy", role
                ),
                teamwork=player_model.stat_for_simulation("teamwork", role),
                communication=player_model.stat_for_simulation("communication", role),
                resource_awareness=player_model.stat_for_simulation(
                    "resource_awareness", role
                ),
                speed=player_model.stat_for_simulation("speed", role),
                starting_lives=resources["lives"],
                starting_shots=resources["shots"],
                final_lives=resources["lives"],
                final_shots=resources["shots"],
                final_special=resources["special"],
                final_missiles=resources["missiles"],
                shields=ROLE_STATS[role]["shield"],
                current_zone=starting_zone,
                cell_row=cell_row,
                cell_col=cell_col,
            )
            players.append(state)
        return players

    def _simulate_round(
        self, red_roster, blue_roster, event_log=None, movement_ctx=None
    ):
        """Returns (result_dict, red_players, blue_players).

        When event_log is a list it is populated with event dicts suitable for
        _flush_to_db, enabling exact replay and DB persistence.
        """
        spawn_cells = movement_ctx["spawn_cells"] if movement_ctx else None
        zone_data = movement_ctx["zone_data"] if movement_ctx else None
        team_spawn_pools = (
            movement_ctx.get("team_spawn_pools") if movement_ctx else None
        )
        red_players = self._make_players(
            red_roster,
            "red",
            spawn_cells=spawn_cells,
            zone_data=zone_data,
            team_spawn_pools=team_spawn_pools,
        )
        blue_players = self._make_players(
            blue_roster,
            "blue",
            spawn_cells=spawn_cells,
            zone_data=zone_data,
            team_spawn_pools=team_spawn_pools,
        )

        pending_missile_locks: list[PendingMissileLock] = []
        pending_nukes: list[PendingNuke] = []
        pending_followups: list[PendingFollowup] = []
        pending_reactions: list[PendingReaction] = []
        eliminated_at = SURVIVED_SENTINEL

        # RoundContext: the per-round mutable-state bundle threaded through
        # record_down and resolve_shot. Built once here; the all_alive list
        # is rebound at each tick once it is recomputed. Replaces the RV-02
        # static→instance self-stash (``self._event_log`` /
        # ``self._pending_nukes``) that record_down used to reach via
        # ``getattr(self, ...)``; that hack is structurally unnecessary now
        # that ctx carries the references.
        # EventLog wraps the caller-provided ``event_log`` list so the
        # 18 inline ``event_log.append({...})`` sites in this module
        # keep working until step 8 retires them; until then,
        # ``event_log`` (local list) and ``ctx.events.entries`` are
        # the same list. On the batch path (``event_log is None``)
        # the EventLog is the null-object variant (persist=False).
        if event_log is None:
            events_log = EventLog(persist=False)
        else:
            events_log = EventLog(persist=True, buffer=event_log)
        ctx = RoundContext(
            events=events_log,
            pending_nukes=pending_nukes,
            pending_followups=pending_followups,
            pending_reactions=pending_reactions,
            all_alive=[],
            movement_ctx=movement_ctx,
        )

        # TIME-01: fully tick-native loop. `tick` is the integer tick index in
        # [0, self.ROUND_TICKS). All internal comparisons, scheduling, uptime
        # accumulation, and timestamps are in ticks. shot_cooldown() still
        # returns seconds, so it is converted to ticks at each scheduling site.
        # SIM-09: round length is read from ``self.ROUND_TICKS`` (defaults to
        # ``TICKS_PER_ROUND``) so tests can patch it for fast-path runs.
        for tick in range(self.ROUND_TICKS):
            # TIME-01: `second` is bound to the integer tick so every internal
            # comparison, scheduled time, uptime accumulation, and emitted
            # timestamp below is tick-valued (the canonical unit). The name is
            # retained only to avoid a 50-site mechanical rename in this
            # SIM-09-doomed engine; no value here is in real seconds.
            second = tick
            # --- process missile locks (LOS-check per tick for 3 ticks) ---
            still_locking_b: list[PendingMissileLock] = []
            for lock in pending_missile_locks:
                result = _tick_missile_lock_shared(lock, second, movement_ctx)
                if result == "pending":
                    still_locking_b.append(lock)
                elif result == "hit":
                    survival = getattr(lock.defender, "survival", 50)
                    dodge_pct = min(20.0, survival / 5.0)
                    if random.random() * 100 < dodge_pct:
                        ctx.events.missile_dodge(lock.defender, lock.attacker, second)
                        # RES-03: also emit a 'missiled' resolution row so
                        # the missile log records this fired missile as a
                        # miss (dodged).
                        ctx.events.missiled(
                            lock.attacker,
                            lock.defender,
                            second,
                            result="miss",
                            friendly_fire=(
                                lock.attacker.team_color == lock.defender.team_color
                            ),
                        )
                    else:
                        self._complete_missile(
                            lock.attacker, lock.defender, second, ctx
                        )
                # "miss": missile already consumed, no further action
            pending_missile_locks = still_locking_b

            # --- process pending reactions (deferred by shot cooldown) ---
            # Shot-resolver consolidation: each queued reaction dispatches to
            # ``resolve_shot`` with ``kind=SHOT_KIND_REACTION``. The resolver
            # owns the validity gates, hit roll, mutations, Down cascade,
            # events, and reaction-never-re-reacts policy.
            due_rx = [rx for rx in pending_reactions if rx.fire_at <= second]
            pending_reactions = [rx for rx in pending_reactions if rx.fire_at > second]
            for rx in due_rx:
                resolve_shot(
                    rx.attacker,
                    rx.defender,
                    second,
                    kind=SHOT_KIND_REACTION,
                    ctx=ctx,
                )

            # --- process pending follow-ups (deferred by shot cooldown) ---
            # Each queued follow-up dispatches to ``resolve_shot`` with
            # ``kind=SHOT_KIND_FOLLOW_UP`` and the carried ``chain_depth``.
            # The resolver applies the chain cap (``_MAX_CHAIN_DEPTH``) and
            # the no-victim-reaction policy.
            due_fu = [fu for fu in pending_followups if fu.fire_at <= second]
            pending_followups = [fu for fu in pending_followups if fu.fire_at > second]
            for fu in due_fu:
                resolve_shot(
                    fu.attacker,
                    fu.defender,
                    second,
                    kind=SHOT_KIND_FOLLOW_UP,
                    ctx=ctx,
                    chain_depth=fu.chain_depth,
                )

            # --- process pending nukes (MECH-05: after reactions/followups so tag-cancels land first) ---
            fired_n = [n for n in pending_nukes if n.complete_time <= second]
            pending_nukes = [n for n in pending_nukes if n.complete_time > second]
            for n in fired_n:
                # MECH-05: check both liveness AND that the fuse was not disarmed via tag-cancel
                nuke_armed = n.player.special_active_until >= n.complete_time
                if n.player.final_lives > 0 and nuke_armed:
                    opposing = (
                        blue_players if n.player.team_color == "red" else red_players
                    )
                    self._complete_nuke(n.player, n.complete_time, opposing, ctx)
                elif not n.cancel_logged:
                    # RV-02: defensive fallback — a nuke that fizzles without
                    # ever passing through record_down (no down/disarm site
                    # logged it) still gets exactly one nuke_cancelled record.
                    # Normally the down-tick emit already set cancel_logged.
                    n.cancel_logged = True
                    ctx.events.nuke_cancelled(n.player, n.complete_time)

            # --- alive players this tick ---
            red_alive = [
                p
                for p in red_players
                if p.final_lives > 0 and p.was_eliminated_at > second
            ]
            blue_alive = [
                p
                for p in blue_players
                if p.final_lives > 0 and p.was_eliminated_at > second
            ]
            all_alive = red_alive + blue_alive
            ctx.all_alive = all_alive

            # MECH-04: mark players reacting to incoming nukes.
            # Runs after drain_nukes so flags apply to still-pending (future) nukes only;
            # a nuke that detonated this tick has already done its damage.
            _apply_nuke_reaction_flags(all_alive, pending_nukes)

            # MECH-06: per-tick LOS-based memory update + communication broadcast
            if movement_ctx is not None:
                sight_data = movement_ctx.sight_data or {}
                for actor in all_alive:
                    if actor.cell_row is None:
                        continue
                    actor_key = f"{actor.cell_row},{actor.cell_col}"
                    visible_cells = sight_data.get(actor_key, frozenset())
                    # Build list of players visible to this actor
                    seen = [
                        p
                        for p in all_alive
                        if p is not actor
                        and p.cell_row is not None
                        and f"{p.cell_row},{p.cell_col}" in visible_cells
                    ]
                    if seen and _update_player_memory(actor, seen, second):
                        _broadcast_communication(actor, all_alive, movement_ctx, second)

            # MECH-06: score broadcast every 180 s
            _apply_score_broadcast(all_alive, second)

            random.shuffle(all_alive)

            # MOVE-01: snapshot each player's previous-tick action BEFORE
            # planning overwrites last_chosen_action, so the always-on Advance
            # can feed choose_goal_cell the same MAP-05 ``intended_action``
            # (prev action) the old in-plan change_zone branch used.
            prev_actions = {
                id(player): getattr(player, "last_chosen_action", "")
                for player in all_alive
            }

            plans = []
            for player in all_alive:
                plans.extend(
                    self._plan_action(
                        player, all_alive, second, movement_ctx=movement_ctx
                    )
                )

            tag_attempts = []
            batch_resupply_requestors = []
            for plan in plans:
                ptype = plan["type"]
                actor = plan["actor"]
                if ptype in ("resupply_ammo", "resupply_lives"):
                    self._attempt_resupply(actor, plan["target"], second, ctx)
                elif ptype == "request_resupply":
                    batch_resupply_requestors.append(actor)
                elif ptype == "only_move":
                    # MOVE-01: map-path movement is the always-on per-tick
                    # Advance step below (decoupled from the weighted action
                    # roll); the only_move roll only flags this tick's Advance
                    # as a single 2× step. Nothing to do here.
                    pass
                elif ptype == "hold":
                    # MOVE-03: entering/maintaining Overwatch is a Stationary
                    # no-op in the dispatch — is_holding (set in plan_action)
                    # anchors the player via the _advance_player Stationary
                    # predicate, and the Overwatch shots are collected after
                    # the Advance loop below (BatchSim-only, ADR-0009).
                    pass
                elif ptype == "change_zone":
                    # MOVE-01 decision 7: 3-zone fallback only (movement_ctx
                    # is None) — keep the weighted _change_zone behaviour.
                    self._change_zone(actor, plan.get("zone"))
                elif ptype == "hide":
                    actor.is_hiding = True
                elif ptype == "capture_base":
                    self._capture_base(
                        actor,
                        plan["base_id"],
                        ctx,
                        second,
                        movement_ctx=plan.get("movement_ctx"),
                    )
                elif ptype == "missile":
                    # RES-03: emit a 'locking' event on lock start via the
                    # ctx.events.locking verb (collapsed from the legacy
                    # emit_event callable seam by the EventLog candidate).
                    scheduled = self._start_missile_lock(
                        actor,
                        plan["target"],
                        second,
                        movement_ctx,
                        ctx=ctx,
                    )
                    if scheduled:
                        pending_missile_locks.append(scheduled)
                elif ptype == "use_special":
                    scheduled = self._use_special(actor, second, all_alive, ctx)
                    if scheduled and scheduled[0] == "nuke":
                        pending_nukes.append(
                            PendingNuke(complete_time=scheduled[1], player=scheduled[2])
                        )
                        # MECH-06: nuke activation broadcast — enemy team learns commander's cell
                        enemy_color_nuke = (
                            "blue" if actor.team_color == "red" else "red"
                        )
                        nuke_targets = [
                            p for p in all_alive if p.team_color == enemy_color_nuke
                        ]
                        _apply_nuke_activation_broadcast(actor, nuke_targets, second)
                elif ptype == "tag":
                    tag_attempts.append(
                        {
                            "attacker": actor,
                            "defender": plan["target"],
                            "overwatch": False,
                        }
                    )

            # MOVE-01: always-on Advance — every non-stationary player advances
            # toward their goal cell every tick, decoupled from the weighted
            # action roll (map path only; movement_ctx is None falls back to
            # the weighted change_zone dispatch above). Runs after the action
            # dispatch so is_hiding (set by the hide branch) and
            # last_chosen_action are final for the stationary check.
            # Order-stable: iterates the already-shuffled all_alive; consumes
            # no RNG (SIM-07/SIM-08 determinism preserved).
            if movement_ctx is not None:
                for actor in all_alive:
                    self._advance_player(
                        actor,
                        all_alive,
                        second,
                        movement_ctx,
                        prev_actions.get(id(actor), ""),
                    )

            # MOVE-03: Overwatch resolution (BatchSimulator-only, ADR-0009).
            # Runs AFTER the Advance loop (so _last_step_cells is final for
            # every mover) and BEFORE _resolve_tag_attempts (so Overwatch
            # shots flow through the SAME deterministic tag path — hit roll,
            # follow-up, victim reaction, last_shot_time — as a deliberate
            # tag, with no duplicated combat logic). Collection / LoS check /
            # dedupe iterate the already-shuffled all_alive and consume NO RNG
            # (SIM-07/SIM-08 internal determinism: serial == parallel).
            tag_attempts.extend(
                self._collect_overwatch_attempts(all_alive, second, movement_ctx)
            )

            if batch_resupply_requestors:
                # EventLog candidate: the legacy ``_batch_emit`` lambda
                # and ``_resupply_event_dict`` adapter are gone — the
                # helper takes ``ctx`` and emits through the verbs.
                resolve_resupply_requests(
                    batch_resupply_requestors,
                    all_alive,
                    second,
                    movement_ctx,
                    ctx=ctx,
                )

            if tag_attempts:
                self._resolve_tag_attempts(
                    tag_attempts,
                    second,
                    event_log,
                    pending_followups,
                    pending_reactions,
                    movement_ctx,
                    all_alive=all_alive,
                    ctx=ctx,
                )

            # Accumulate uptime AFTER combat resolves (TIME-01: tick-native,
            # +1 tick per iteration). Membership is recomputed post-combat so a
            # player eliminated this tick is excluded — their last uptime tick
            # is the previous one, making total uptime == was_eliminated_at
            # exactly and reconciling with dead = TICKS_PER_ROUND - elim_tick.
            for p in red_players + blue_players:
                if p.final_lives <= 0 or p.was_eliminated_at <= second:
                    continue
                if not p.is_active_at(second) and not p.is_taggable_at(second):
                    p.ticks_not_targetable += 1
                elif not p.is_active_at(second):
                    p.ticks_reset_window += 1
                else:
                    p.ticks_active += 1
                    # RV-02: fully active again → the medic reset chain ends.
                    p.down_chain_count = 0

            # --- check for team elimination ---
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]
            if not red_alive or not blue_alive:
                eliminated_at = second
                if not red_alive:
                    for p in blue_alive:
                        self._award_bases(p, ctx, second)
                if not blue_alive:
                    for p in red_alive:
                        self._award_bases(p, ctx, second)
                break

        # TIME-01: guarantee uptime reconciles to exactly TICKS_PER_ROUND per
        # player. If the loop broke early on a team wipe, survivors are credited
        # the remaining ticks as active (they were alive the whole round).
        # Eliminated players' remainder is dead-time (TICKS_PER_ROUND -
        # was_eliminated_at), accounted at report time, so no top-up is needed.
        for p in red_players + blue_players:
            if p.final_lives > 0:
                accounted = (
                    p.ticks_active + p.ticks_not_targetable + p.ticks_reset_window
                )
                if accounted < TICKS_PER_ROUND:
                    p.ticks_active += TICKS_PER_ROUND - accounted

        red_points = sum(p.points_scored for p in red_players)
        blue_points = sum(p.points_scored for p in blue_players)
        red_survivors = sum(1 for p in red_players if p.final_lives > 0)
        blue_survivors = sum(1 for p in blue_players if p.final_lives > 0)
        result = {
            "red_points": red_points,
            "blue_points": blue_points,
            "red_survivors": red_survivors,
            "blue_survivors": blue_survivors,
            "red_eliminated": all(p.final_lives <= 0 for p in red_players),
            "blue_eliminated": all(p.final_lives <= 0 for p in blue_players),
            "eliminated_at": eliminated_at,
        }
        return result, red_players, blue_players

    # ------------------------------------------------------------------ #
    # Action planning — reuses weight functions from weights.py
    # ------------------------------------------------------------------ #

    def _plan_action(self, player, all_alive, tick, movement_ctx=None):
        # TIME-01: BatchSimulator is fully tick-native — pass the integer tick
        # and select tick-domain thresholds in the shared planning helpers.
        return plan_action(player, all_alive, tick, movement_ctx, time_domain="ticks")

    def _advance_player(
        self,
        player,
        all_alive,
        second,
        movement_ctx,
        prev_action: str,
    ) -> None:
        """MOVE-01: always-on goal-directed Advance for one player this tick.

        Mirrors RBS._advance_player. Performed for every non-stationary player
        every tick, independent of the weighted action roll. Stationary set
        (suppress the Advance this tick): ``player.is_hiding`` is True, OR this
        tick's chosen action is ``capture_base`` (frozen, anchored to base).
        When this tick's action is ``only_move`` the Advance is a single 2×
        step in one astar_advance call. No-op when there is no map path.
        """
        if movement_ctx is None or player.cell_row is None:
            return
        chosen = getattr(player, "last_chosen_action", "")
        # Stationary set: hiding, holding (MOVE-03 Overwatch — anchored to its
        # current cell watching its sightline), or anchored to a base capture
        # this tick.
        if (
            player.is_hiding
            or getattr(player, "is_holding", False)
            or chosen == "capture_base"
        ):
            return
        goal_cell = choose_goal_cell(
            player,
            all_alive,
            movement_ctx.get_spawn_cells(),
            movement_ctx,
            prev_action,
            second,
            time_domain="ticks",
        )
        # only_move = one single 2× step; all other (non-stationary) actions
        # Advance the normal speed-scaled distance while they act.
        multiplier = 2 if chosen == "only_move" else 1
        self._move_player_in_memory(player, second, goal_cell, movement_ctx, multiplier)

    # NOTE: ``_record_down`` is gone — lifted to
    # ``sim_helpers.down.record_down`` as a pure free function by the
    # shot-resolver consolidation. All callers now pass ``ctx`` (a
    # ``RoundContext``) instead of relying on the RV-02 self-stash.
    # See ``.claude/worktrees/shot-resolver-seam-contract.md``.

    def _move_player_in_memory(
        self, player, second, goal_cell, movement_ctx, multiplier: int = 1
    ):
        if goal_cell is None or player.cell_row is None:
            return
        adj = movement_ctx["adj"]
        zone_data = movement_ctx["zone_data"]
        current = (player.cell_row, player.cell_col)
        if current == goal_cell or current not in adj:
            return
        # STAT-03 Phase 1: traverse speed-scaled cells per tick, not just one.
        # MOVE-01: an only_move roll covers a single 2× step (multiplier=2) in
        # ONE advance call.
        steps = cells_to_move(getattr(player, "speed", 50), zone_data) * multiplier
        # MOVE-02 (ADR-0008): re-step a goal-keyed cached A* route instead of
        # recomputing full A* every tick. choose_goal_cell still runs every
        # tick upstream (it does no A*); only the PATH is cached here. The 2×
        # only_move multiplier consumes 2×steps cells from the SAME committed
        # route — it is not a recompute trigger. BatchSimulator-only; RBS keeps
        # astar_advance. Consumes no RNG (serial == parallel still holds).
        next_cell = astar_advance_cached(player, current, goal_cell, adj, steps)
        if next_cell == current:
            # MOVE-01: record a move only when the cell actually changed. The
            # cache (if any) is preserved so the next tick re-steps the same
            # committed route rather than recomputing.
            return
        player.cell_row, player.cell_col = next_cell
        player.current_zone = zone_from_cell(
            next_cell[0], next_cell[1], movement_ctx["spawn_cells"]
        )
        # MOVE-01: append a compact (start, end, timestamp) entry to the
        # transient movement_trail (no DB column / no migration). _flush_to_db
        # turns these into compact movement GameEvents when a batch round is
        # persisted, mirroring RBS movement-event semantics. The intermediate
        # route is NOT stored; it is recomputed on demand at replay via
        # deterministic A* start->end.
        player.movement_trail.append((current, next_cell, second))

    def _collect_overwatch_attempts(
        self, all_alive: list, second: int, movement_ctx
    ) -> "list[dict]":
        """MOVE-03: Overwatch tag-attempts for this tick (BatchSim-only, ADR-0009).

        Called AFTER the Advance loop (so every mover's ``_last_step_cells`` is
        final) and BEFORE ``_resolve_tag_attempts`` (so Overwatch shots flow
        through the SAME deterministic tag path — hit roll, follow-up, victim
        reaction, ``last_shot_time`` — as a deliberate tag, no duplicated combat
        logic). Returns a list of ``{"attacker", "defender", "overwatch": True}``
        dicts to be appended to the tick's ``tag_attempts``.

        Collection / LoS check / dedupe iterate the already-shuffled
        ``all_alive`` and consume **no RNG** (SIM-07/SIM-08 internal
        determinism: serial == parallel). Returns ``[]`` on the 3-zone fallback
        (``movement_ctx is None``) — Overwatch needs cell LoS.

        Provenance scope: only the *initiating* Overwatch shot is flagged
        ``overwatch`` in event metadata. Any Follow-up / Reaction shots it
        chains via ``_resolve_tag_attempts`` are ordinary shots and remain
        unmarked by design (ADR-0009) — revisit only if per-chain Overwatch
        analytics are ever required.
        """
        if movement_ctx is None:
            return []
        # Live holder pool (enemy-agnostic; filtered per mover below).
        holders = [
            h
            for h in all_alive
            if getattr(h, "is_holding", False)
            and h.cell_row is not None
            and h.final_lives > 0
            and h.final_shots > 0
            and h.is_active_at(second)
            and h.is_taggable_at(second)
        ]
        if not holders:
            return []
        # Per-tick dedupe: a normal holder fires at most ONE Overwatch shot per
        # tick. A rapid-fire Scout holder (special active → shot_cooldown == 0)
        # may fire at every crossing enemy, so it is exempt from the dedupe set.
        attempts: list = []
        fired_holder_ids: set = set()
        for mover in all_alive:
            if mover.cell_row is None:
                continue
            # An enemy draws Overwatch if it is currently *in* the holder's LoS
            # OR its Advance this tick crossed it. _last_step_cells is the
            # popped committed-route slice (intermediate + end) — the
            # "moved *through* LoS in one Advance" guarantee (ADR-0009); the
            # current cell covers a stationary / just-arrived enemy. Dedup
            # while preserving order so the LoS scan is stable.
            traversed = getattr(mover, "_last_step_cells", []) or []
            check_cells = list(
                dict.fromkeys(
                    [tuple(c) for c in traversed] + [(mover.cell_row, mover.cell_col)]
                )
            )
            for h in holders:
                if h.team_color == mover.team_color or h is mover:
                    continue
                is_rapid_scout = h.role == "scout" and h.special_active_until > second
                if not is_rapid_scout and id(h) in fired_holder_ids:
                    continue
                # Shot-cooldown gate: free shot when cooldown is 0, else require
                # the gap to have elapsed.
                h_cd = shot_cooldown(h, second)
                if not (h_cd == 0.0 or (second - h.last_shot_time) >= h_cd):
                    continue
                holder_cell = (h.cell_row, h.cell_col)
                if any(movement_ctx.can_see(holder_cell, tc) for tc in check_cells):
                    attempts.append(
                        {"attacker": h, "defender": mover, "overwatch": True}
                    )
                    if not is_rapid_scout:
                        fired_holder_ids.add(id(h))
        return attempts

    # ------------------------------------------------------------------ #
    # Action resolution (no DB writes)
    # ------------------------------------------------------------------ #

    def _resolve_tag_attempts(
        self,
        attempts,
        second,
        event_log=None,
        pending_followups=None,
        pending_reactions=None,
        movement_ctx=None,
        all_alive=None,
        ctx: RoundContext | None = None,
    ):
        """Thin wrapper: dispatch each per-tick tag attempt to ``resolve_shot``.

        Shot-resolver consolidation: the 450-line per-attempt resolution
        body (initial-tag + immediate-reaction + immediate-follow-up
        loops, each duplicating the Shot → Hit → Tag → Down ladder) is
        gone. ``resolve_shot`` owns the 10 phases, the reaction-roll
        scheduling at phase 9, and the follow-up-roll chaining at phase
        10 (recursive when the cooldown rounds to 0 ticks).

        ``ctx`` is the per-round ``RoundContext`` built by
        ``_simulate_round``. The legacy kwargs (``event_log`` /
        ``pending_followups`` / ``pending_reactions`` / ``movement_ctx``
        / ``all_alive``) are still accepted for backward compatibility
        with tests that call the method directly; when ``ctx`` is not
        passed, one is synthesised from the legacy kwargs so the
        ``record_down`` chokepoint and the shot resolver see consistent
        state. The Overwatch ``kind`` is picked off ``a.get("overwatch")``
        — Overwatch attempts otherwise dispatch through the same
        initial-tag path (MOVE-03 / ADR-0009).
        """
        if ctx is None:
            if event_log is None:
                events_log = EventLog(persist=False)
            else:
                events_log = EventLog(persist=True, buffer=event_log)
            ctx = RoundContext(
                events=events_log,
                pending_nukes=[],
                pending_followups=(
                    pending_followups if pending_followups is not None else []
                ),
                pending_reactions=(
                    pending_reactions if pending_reactions is not None else []
                ),
                all_alive=all_alive if all_alive is not None else [],
                movement_ctx=movement_ctx,
            )
        for a in attempts:
            kind = (
                SHOT_KIND_OVERWATCH if a.get("overwatch", False) else SHOT_KIND_INITIAL
            )
            resolve_shot(a["attacker"], a["defender"], second, kind=kind, ctx=ctx)

    def _attempt_resupply(self, tagger, teammate, second, ctx: RoundContext):
        _attempt_resupply_shared(tagger, teammate, second, ctx=ctx)

    def _change_zone(self, player, towards=None):
        if player.current_zone == 1:
            player.current_zone = (
                towards if towards in (0, 2) else random.choice([0, 2])
            )
        else:
            player.current_zone = 1

    def _capture_base(
        self,
        player,
        base_id,
        ctx: RoundContext | None = None,
        second=0,
        movement_ctx=None,
    ):
        # ``ctx`` defaults to a fresh null-context for direct test
        # callsites (e.g. ``BatchSimulator()._capture_base(player, 14,
        # movement_ctx=ctx)`` in test_map.py) that don't care about
        # emits and want minimal setup.
        if ctx is None:
            ctx = RoundContext()
        _capture_base_shared(player, base_id, second, movement_ctx, ctx=ctx)

    def _award_bases(self, player, ctx: RoundContext | None = None, second=0):
        if ctx is None:
            ctx = RoundContext()
        _award_bases_shared(player, second, ctx=ctx)

    def _start_missile_lock(
        self,
        attacker,
        defender,
        second,
        movement_ctx=None,
        *,
        ctx: RoundContext | None = None,
    ):
        if ctx is None:
            ctx = RoundContext()
        return _start_missile_lock_shared(
            attacker, defender, second, movement_ctx, ctx=ctx
        )

    def _complete_missile(
        self,
        attacker,
        defender,
        second,
        ctx: RoundContext | None = None,
    ):
        # RES-03: always emit a 'missiled' resolution event when the missile
        # reaches resolution (gate below filters by active/taggable just like
        # the legacy path). result='hit' here; the dodge/los-broken paths emit
        # result='miss' from the caller.
        if attacker.is_active_at(second) and defender.is_taggable_at(second):
            if not defender.is_active_at(second) and defender.is_taggable_at(second):
                defender.times_tagged_in_reset_window += 1
            defender.shields = defender.max_shields
            defender.points_scored -= 100
            defender.final_lives = max(0, defender.final_lives - 2)
            if defender.final_lives <= 0:
                defender.was_eliminated_at = second
                if ctx is not None:
                    ctx.events.elimination(
                        attacker, defender, int(second), action="missile"
                    )
            record_down(defender, second, ctx)
            defender.times_missiled += 1

            attacker.points_scored += 500
            attacker.missile_points += 500
            attacker.final_missiles -= 1
            attacker.missiles_landed += 1
            if attacker.role != "heavy":
                attacker.final_special = min(
                    attacker.max_special, attacker.final_special + 2
                )
            if ctx is not None:
                ctx.events.missiled(
                    attacker,
                    defender,
                    int(second),
                    result="hit",
                    friendly_fire=(attacker.team_color == defender.team_color),
                )

    def _use_special(self, player, second, all_alive, ctx: RoundContext | None = None):
        if not (
            player.can_use_special
            and player.final_lives > 0
            and player.is_active_at(second)
        ):
            return None
        player.specials_used += 1
        if player.role == "commander":
            player.final_special -= player.special_cost
            # TIME-01: nuke fuse is 4-7 s -> 8-14 ticks (tick-native).
            countdown = random.randint(8, 14)
            player.special_active_until = second + countdown
            if ctx is not None:
                ctx.events.special(
                    player,
                    second,
                    description=f"{player.name} activates nuke",
                    metadata_extras={"fires_at": second + countdown},
                )
            return ("nuke", second + countdown, player)
        elif player.role == "scout":
            player.final_special -= player.special_cost
            # TIME-01: rapid fire lasts the whole round (tick-native sentinel).
            player.special_active_until = TICKS_PER_ROUND
            if ctx is not None:
                ctx.events.special(
                    player,
                    second,
                    description=f"{player.name} activates rapid fire",
                )
        elif player.role == "medic":
            player.final_special -= player.special_cost
            heal_chart = {"commander": 4, "heavy": 3, "scout": 5, "ammo": 2, "medic": 0}
            # RES-02b: collect affected teammates (excluding the medic actor)
            # so the event metadata carries per-target post-heal snapshots.
            healed_mates: list = []
            for mate in all_alive:
                if mate.team_color == player.team_color and mate.is_active_at(second):
                    amount = heal_chart.get(mate.role, 0)
                    pre_lives = mate.final_lives
                    mate.final_lives = min(mate.max_lives, mate.final_lives + amount)
                    if mate is not player:
                        healed_mates.append((mate, mate.final_lives - pre_lives))
            if ctx is not None:
                ctx.events.special(
                    player,
                    second,
                    description=f"{player.name} team heal special",
                    metadata_extras={
                        "targets": [
                            {
                                "pid": m.player_id,
                                "name": m.name,
                                "lives_delta": delta,
                                "shots": m.final_shots,
                                "lives": m.final_lives,
                                "points": m.points_scored,
                            }
                            for m, delta in healed_mates
                        ],
                    },
                )
        elif player.role == "ammo":
            player.final_special -= player.special_cost
            shot_chart = {
                "commander": 5,
                "heavy": 5,
                "scout": 10,
                "medic": 5,
                "ammo": 0,
            }
            # RES-02b: collect affected teammates (excluding the ammo actor)
            # so the event metadata carries per-target post-resupply snapshots.
            resupplied_mates: list = []
            for mate in all_alive:
                if mate.team_color == player.team_color and mate.is_active_at(second):
                    amount = shot_chart.get(mate.role, 0)
                    pre_shots = mate.final_shots
                    mate.final_shots = min(mate.max_shots, mate.final_shots + amount)
                    if mate is not player:
                        resupplied_mates.append((mate, mate.final_shots - pre_shots))
            if ctx is not None:
                ctx.events.special(
                    player,
                    second,
                    description=f"{player.name} team ammo special",
                    metadata_extras={
                        "targets": [
                            {
                                "pid": m.player_id,
                                "name": m.name,
                                "shots_delta": delta,
                                "shots": m.final_shots,
                                "lives": m.final_lives,
                                "points": m.points_scored,
                            }
                            for m, delta in resupplied_mates
                        ],
                    },
                )
        return None

    def _complete_nuke(
        self,
        player,
        second,
        opposing_players,
        ctx: RoundContext | None = None,
    ):
        if player.is_active_at(second) and player.final_lives > 0:
            player.points_scored += 500
            if ctx is not None:
                # RES-02b: build per-opp post-detonation snapshots BEFORE the
                # mutation loop so the detonation special event can carry the
                # post-event target values, while preserving the historical
                # emit order (detonation special first, then per-opp eliminations).
                projected_targets: list = []
                for opp in opposing_players:
                    if opp.final_lives <= 0:
                        continue
                    lives_taken = min(opp.final_lives, 3)
                    projected_targets.append(
                        {
                            "pid": opp.player_id,
                            "name": opp.name,
                            "lives_delta": -lives_taken,
                            "shots": opp.final_shots,
                            "lives": opp.final_lives - lives_taken,
                            "points": opp.points_scored,
                        }
                    )
                ctx.events.special(
                    player,
                    second,
                    description=f"{player.name} nuke detonates",
                    points=500,
                    metadata_extras={"targets": projected_targets},
                )
            for opp in opposing_players:
                if opp.final_lives <= 0:
                    continue
                lives_taken = min(opp.final_lives, 3)
                opp.final_lives -= lives_taken
                record_down(opp, second, ctx)
                opp.shields = opp.max_shields
                if opp.role == "commander" and opp.special_active_until > second:
                    opp.special_active_until = 0
                if opp.final_lives <= 0:
                    opp.was_eliminated_at = second
                    if ctx is not None:
                        ctx.events.elimination(player, opp, second, action="nuke")

    # ------------------------------------------------------------------ #
    # Seed-based exact replay and DB persistence
    # ------------------------------------------------------------------ #

    def replay_round(
        self,
        red_roster,
        blue_roster,
        seed: int,
        flipped: bool,
        movement_ctx=None,
    ):
        """Replay one round from a stored (seed, orientation) pair.

        SIM-08: ``flipped`` is the orientation carried alongside the seed.
        ``red_roster`` / ``blue_roster`` are the canonical team rosters; when
        ``flipped`` is ``True`` they are swapped before ``_simulate_round`` so
        the replayed game is byte-identical to the one ``run()`` scored under
        the same (seed, orientation). The returned ``red_players`` /
        ``blue_players`` therefore reflect the ACTUAL physical sides simulated
        (a flipped game's ``red_players`` are the team_blue roster's players).
        """
        events: list = []
        random.seed(seed)
        if flipped:
            sim_red, sim_blue = blue_roster, red_roster
        else:
            sim_red, sim_blue = red_roster, blue_roster
        result, red_players, blue_players = self._simulate_round(
            sim_red, sim_blue, event_log=events, movement_ctx=movement_ctx
        )
        return result, red_players, blue_players, events

    def save_games(
        self,
        team_red,
        team_blue,
        seeds: list[tuple[int, bool]],
        n,
        *,
        arena_map=None,
    ):
        """Replay and persist n games from carried (seed, orientation) pairs.

        SIM-08: ``seeds`` is a list of ``(seed, flipped)`` pairs. Session
        round-trips turn tuples into lists, so each pair is unpacked
        defensively to accept either shape. When a game was flipped, the team
        that physically played red is ``team_blue`` (the argument): the
        rosters are swapped into ``replay_round`` and the Team objects are
        swapped into ``_flush_to_db`` so the persisted ``GameRound.team_red``
        / ``team_blue`` and every ``PlayerRoundState.team_color`` match the
        sides actually simulated. The actual-sides storage implicitly encodes
        the orientation — no new column or migration is required.
        """
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)
        movement_ctx, zone_size = load_map_context(arena_map)
        saved = []
        for pair in seeds[:n]:
            seed, flipped = pair  # tuple or 2-element list (session-safe)
            flipped = bool(flipped)
            result, red_players, blue_players, events = self.replay_round(
                red_roster,
                blue_roster,
                seed,
                flipped,
                movement_ctx=movement_ctx,
            )
            # SIM-08: persist the ACTUAL sides. red_players/blue_players from
            # replay_round are already the physical sides; pass the matching
            # Team objects so GameRound FKs and PlayerRoundState.team_color
            # stay consistent with the simulated round.
            if flipped:
                db_team_red, db_team_blue = team_blue, team_red
            else:
                db_team_red, db_team_blue = team_red, team_blue
            # SIM-09: persist arena_map / zone_size on every saved round so
            # batch-saved games carry the same map metadata as match-path and
            # single-round-path games.
            gr = self._flush_to_db(
                db_team_red,
                db_team_blue,
                result,
                red_players,
                blue_players,
                events,
                rng_seed=seed,
                movement_ctx=movement_ctx,
                arena_map=arena_map,
                zone_size=zone_size,
            )
            saved.append(gr)
        return saved

    @transaction.atomic
    def _flush_to_db(
        self,
        team_red,
        team_blue,
        result,
        red_players,
        blue_players,
        events,
        *,
        rng_seed: int | None = None,
        movement_ctx=None,
        match: "Match | None" = None,
        round_number: int = 1,
        arena_map=None,
        zone_size: int | None = None,
    ):
        """Write a replayed in-memory round to DB as a ``GameRound``.

        MOVE-01: ``movement_ctx`` (optional) is used only to resolve each
        movement step's end-cell zone for the compact movement GameEvents
        flushed from each player's ``movement_trail``. When ``None`` the
        per-move ``new_zone`` falls back to the player's final zone.

        SIM-09: ``match`` / ``round_number`` allow the same flush path to
        persist either a standalone round (default: ``match=None``,
        ``round_number=1``) or the two rounds of a full Match (via
        ``simulate_match``). ``arena_map`` / ``zone_size`` are persisted on
        ``GameRound`` so saved batch / match / single-round games all carry
        the same map metadata.
        """
        from teams.models import Player as PlayerModel

        game_round = GameRound.objects.create(
            match=match,
            round_number=round_number,
            team_red=team_red,
            team_blue=team_blue,
            red_points=result["red_points"],
            blue_points=result["blue_points"],
            red_team_eliminated=result["red_eliminated"],
            blue_team_eliminated=result["blue_eliminated"],
            eliminated_at=result["eliminated_at"],
            is_completed=True,
            rng_seed=rng_seed,
            arena_map=arena_map,
            zone_size=zone_size,
        )
        # Trigger winner calculation
        game_round.save()

        # Build id → Player ORM object map (one query)
        all_pids = [p.player_id for p in red_players + blue_players if p.player_id]
        players_by_id = {p.id: p for p in PlayerModel.objects.filter(id__in=all_pids)}

        # Create PlayerRoundState rows
        for p in red_players + blue_players:
            player_obj = players_by_id.get(p.player_id)
            if not player_obj:
                continue
            PlayerRoundState.objects.create(
                game_round=game_round,
                player=player_obj,
                team_color=p.team_color,
                role=p.role,
                zone_fallback=p.current_zone,
                cell_row=p.cell_row,
                cell_col=p.cell_col,
                shields=p.shields,
                starting_lives=p.starting_lives,
                starting_shots=p.starting_shots,
                starting_special=0,
                starting_missiles=self.ROLE_STARTING_RESOURCES[p.role]["missiles"],
                final_lives=p.final_lives,
                final_shots=p.final_shots,
                final_special=p.final_special,
                final_missiles=p.final_missiles,
                neutral_base_destroyed=p.neutral_base_destroyed,
                opposing_base_destroyed=p.opposing_base_destroyed,
                special_active_until=p.special_active_until or 0,
                is_hiding=p.is_hiding,
                points_scored=p.points_scored,
                tags_made=p.tags_made,
                final_medic_hits=p.medic_hits,
                shots_missed=p.shots_missed,
                times_tagged=p.times_tagged,
                times_missiled=p.times_missiled,
                missiles_landed=p.missiles_landed,
                missile_points=p.missile_points,
                resupplies_given=p.resupplies_given,
                combo_resupply_count=p.combo_resupply_count,
                specials_used=p.specials_used,
                times_tagged_in_reset_window=p.times_tagged_in_reset_window,
                follow_up_shots=p.follow_up_shots,
                reaction_shots=p.reaction_shots,
                ticks_active=p.ticks_active,
                ticks_not_targetable=p.ticks_not_targetable,
                ticks_reset_window=p.ticks_reset_window,
                was_eliminated_at=p.was_eliminated_at,
            )

        # Create GameEvent rows
        for ev in events:
            actor_obj = players_by_id.get(ev["actor_id"])
            if not actor_obj:
                continue
            target_obj = (
                players_by_id.get(ev.get("target_id")) if ev.get("target_id") else None
            )
            GameEvent.objects.create(
                game_round=game_round,
                timestamp=ev["timestamp"],
                event_type=ev["event_type"],
                actor=actor_obj,
                target=target_obj,
                points_awarded=ev.get("points_awarded", 0),
                description=ev.get("description", ""),
                metadata=ev.get("metadata", {}),
            )

        # MOVE-01: flush each player's compact movement trail to movement
        # GameEvents (start cell + end cell + timestamp). Mirrors RBS
        # movement-event semantics; the exact intermediate route is recomputed
        # at replay by re-running deterministic A* start->end (not stored).
        spawn_cells = movement_ctx.get_spawn_cells() if movement_ctx else None
        for p in red_players + blue_players:
            actor_obj = players_by_id.get(p.player_id)
            if not actor_obj:
                continue
            for start_cell, end_cell, ts in p.movement_trail:
                if spawn_cells is not None:
                    new_zone = zone_from_cell(end_cell[0], end_cell[1], spawn_cells)
                else:
                    new_zone = p.current_zone
                # Movement events carry only movement-specific metadata. Per-tick
                # actor snapshots (shots/lives/points/sp) are NOT tracked on the
                # trail; using the player's end-of-round values here previously
                # poisoned the per-player chart series (every movement event
                # stamped the final value, so chart lines jumped to end-of-round
                # values immediately after spawn).
                GameEvent.objects.create(
                    game_round=game_round,
                    timestamp=ts,
                    event_type="movement",
                    actor=actor_obj,
                    target=None,
                    points_awarded=0,
                    description=f"{p.name} moves to cell ({end_cell[0]}, {end_cell[1]})",
                    metadata={
                        "actor_role": p.role,
                        "start_row": start_cell[0],
                        "start_col": start_cell[1],
                        "end_row": end_cell[0],
                        "end_col": end_cell[1],
                        "cell_row": end_cell[0],
                        "cell_col": end_cell[1],
                        "new_zone": new_zone,
                    },
                )

        # RES-04: cell-occupancy snapshot. Only populated when a map is active
        # (movement_ctx is not None); map-less rounds leave cell_occupancy_json
        # null. The map-active gate is required because reconstruct_cell_occupancy
        # needs an A* adjacency dict.
        if movement_ctx is not None:
            from matches.sim_helpers.cell_occupancy import reconstruct_cell_occupancy
            from matches.sim_helpers.time_constants import TICKS_PER_ROUND

            adj = movement_ctx.get_adjacency()
            elevation_data = movement_ctx.elevation_grid  # may be None — that's fine

            occupancy_json: dict[str, dict[str, int]] = {}
            for p in red_players + blue_players:
                if not p.player_id:
                    continue
                spawn_cell = (
                    p.movement_trail[0][0]
                    if p.movement_trail
                    else (p.cell_row, p.cell_col)
                )
                # Skip players who never had a cell position (no map, edge case).
                if spawn_cell[0] is None or spawn_cell[1] is None:
                    continue

                per_cell = reconstruct_cell_occupancy(
                    movement_trail=p.movement_trail,
                    spawn_cell=spawn_cell,
                    round_ticks=TICKS_PER_ROUND,
                    eliminated_at=p.was_eliminated_at,
                    adj=adj,
                    elevation_data=elevation_data,
                )

                occupancy_json[str(p.player_id)] = {
                    f"{r},{c}": ticks for (r, c), ticks in per_cell.items()
                }

            game_round.cell_occupancy_json = occupancy_json
            game_round.save(update_fields=["cell_occupancy_json"])

        # RV-02: build auto-flagged highlights from the in-memory event buffer
        # + result dict and persist. Runs on every path (map or 3-zone). Pure
        # function (no RNG); id->name / id->team maps keep it Django-free.
        from matches.sim_helpers.highlights import build_highlights
        from matches.sim_helpers.time_constants import TICKS_PER_ROUND

        name_by_id = {
            p.player_id: p.name for p in red_players + blue_players if p.player_id
        }
        team_by_id = {
            p.player_id: p.team_color for p in red_players + blue_players if p.player_id
        }
        game_round.highlights_json = build_highlights(
            events,
            result,
            round_ticks=TICKS_PER_ROUND,
            name_by_id=name_by_id,
            team_by_id=team_by_id,
        )
        game_round.save(update_fields=["highlights_json"])

        # HX-02: bump the global role-benchmark cache version. bulk_create
        # skips post_save, so this hook covers the batch save path; the
        # call is cheap (one cache op) and monotonic — if the surrounding
        # @transaction.atomic rolls back, the next view request just
        # re-scans against the new version (invalidation is never wrong).
        from teams.role_benchmarks_cache import invalidate_role_benchmarks

        invalidate_role_benchmarks()

        return game_round


# ---------------------------------------------------------------------------
# Parallel worker helpers live in matches.sim_helpers.parallel_worker, which
# has no top-level Django imports so it is safe to import inside a spawned
# worker process before django.setup() has been called.
#
# Re-exported here for backward-compat with any external callers.
# ---------------------------------------------------------------------------
from matches.sim_helpers.parallel_worker import (  # noqa: E402
    batch_round_worker as _batch_worker,
    worker_django_init as _worker_django_init,
)
