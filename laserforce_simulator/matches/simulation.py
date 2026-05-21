import math as _math
import random
import logging
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
from .sim_helpers.tick_engine import (
    drain_nukes,
    drain_reactions,
    drain_followups,
)
from .sim_helpers.pending_events import (
    PendingMissileLock,
    PendingNuke,
    PendingFollowup,
    PendingReaction,
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
    TICK_SECONDS,
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


# ----------------------------------------------------------------------------
# RES-02b — Universal event metadata snapshot helpers.
# Every event_log.append site below uses these to attach post-event actor and
# (optionally) target resource snapshots to the event metadata. The seam
# contract (.claude/worktrees/res02b-parity-contract.md) pins the key set.
# ----------------------------------------------------------------------------
def _actor_meta(actor) -> dict:
    """Universal actor snapshot block (post-event values)."""
    return {
        "actor_role": actor.role,
        "actor_shots": actor.final_shots,
        "actor_lives": actor.final_lives,
        "actor_points": actor.points_scored,
        "sp": actor.final_special,
    }


def _target_meta(target) -> dict:
    """Universal target snapshot block (post-event values)."""
    return {
        "target_role": target.role,
        "target_shots": target.final_shots,
        "target_lives": target.final_lives,
        "target_points": target.points_scored,
    }


def _build_meta(actor, target=None, **extras) -> dict:
    """Build event metadata with actor block, optional target block, and extras."""
    md = _actor_meta(actor)
    if target is not None:
        md.update(_target_meta(target))
    md.update(extras)
    return md


def _resupply_event_dict(event_type: str, kwargs: dict, tick_second: float) -> dict:
    """Convert kwargs-style resupply emit into the standard event buffer dict.

    For single resupply events (resupply_lives/ammo) the adapter in _do_resupply
    passes actor=support, target=requestor.  For combo_resupply only requestor is
    passed (no actor kwarg); actor_id is resolved from the requestor kwarg instead.
    """
    actor = kwargs.get("actor") or kwargs.get("requestor")
    target = kwargs.get("target")
    ts = kwargs.get("second", tick_second)
    return {
        "event_type": event_type,
        "actor_id": actor.player_id if actor is not None else None,
        "target_id": target.player_id if target is not None else None,
        "timestamp": int(ts),
        "points_awarded": 0,
        "description": f"resupply request: {event_type}",
        "metadata": kwargs.get("metadata", {}),
    }


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


def _cooldown_ticks(player, tick) -> int:
    """TIME-01: shot_cooldown() returns SECONDS (0.0 / 0.5 / 1.0). The
    tick-native BatchSimulator schedules pending shots at integer tick offsets,
    so convert the seconds cooldown to whole ticks: 0.0s -> 0, 0.5s -> 1,
    1.0s -> 2 (round to nearest tick)."""
    cooldown_seconds = shot_cooldown(player, tick)
    return int(round(cooldown_seconds / TICK_SECONDS))


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
        n=100,
        *,
        arena_map=None,
        workers: int | None = None,
        master_seed: int | None = None,
    ):
        """Simulate n rounds and return aggregate statistics.

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
        # Read rosters once — list of (role, Player) tuples
        red_roster = list(team_red.active_roster)
        blue_roster = list(team_blue.active_roster)

        movement_ctx, _ = load_map_context(arena_map)

        if master_seed is None:
            master_seed = random.Random().getrandbits(63)
        gen = random.Random(master_seed)

        if workers and workers > 1:
            return self._run_parallel(
                red_roster, blue_roster, n, movement_ctx, workers, gen
            )

        # SIM-08: simulate each game under its index-determined orientation,
        # collecting (result, seed, flipped) triples; the shared
        # _aggregate_batch helper de-flips to team position and builds the
        # result dict (identical path to the parallel run).
        games: list[tuple[dict, int, bool]] = []
        for i in range(n):
            s = gen.getrandbits(63)
            side_red, side_blue, flipped = self._side_order(i, red_roster, blue_roster)
            random.seed(s)
            result, _, _ = self._simulate_round(
                side_red, side_blue, movement_ctx=movement_ctx
            )
            games.append((result, s, flipped))

        return self._aggregate_batch(games, n)

    def _run_parallel(
        self,
        red_roster,
        blue_roster,
        n: int,
        movement_ctx,
        workers: int,
        gen: random.Random,
    ) -> dict:
        """Simulate n rounds using a multiprocessing worker pool.

        Pre-serializes all player data in the parent so workers never touch
        the ORM.  Returns the same aggregate dict as run().

        Per-round seeds are drawn from the same master generator the serial
        path uses, so a given master_seed produces identical games whether
        run serially or in parallel.
        """
        from concurrent.futures import ProcessPoolExecutor

        red_data = _precompute_roster(red_roster)
        blue_data = _precompute_roster(blue_roster)

        # Generate n distinct integer seeds in the parent before spawning.
        seeds = [gen.getrandbits(63) for _ in range(n)]

        # SIM-08: orientation is a pure function of the ordered game index
        # (single source of truth: _is_flipped), so it is computed in the
        # parent and shipped to each worker. The worker swaps which
        # precomputed roster it treats as red vs blue when flipped; the
        # parent feeds the same _aggregate_batch helper the serial path
        # uses, so serial and parallel produce identical team-position
        # aggregates AND identical side_advantage for a given master_seed.
        flips = [self._is_flipped(i) for i in range(n)]

        args_list = [
            (red_data, blue_data, movement_ctx, s, f) for s, f in zip(seeds, flips)
        ]
        chunksize = max(1, n // (workers * 4))

        from matches.sim_helpers.parallel_worker import (
            batch_round_worker,
            worker_django_init,
        )

        with ProcessPoolExecutor(
            max_workers=workers, initializer=worker_django_init
        ) as executor:
            results = list(
                executor.map(batch_round_worker, args_list, chunksize=chunksize)
            )

        # SIM-08: same shared aggregation path as the serial run() — the
        # workers returned physical-side results in submission order, so
        # zip them back with their seeds and orientations and de-flip.
        games = list(zip(results, seeds, flips))
        return self._aggregate_batch(games, n)

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
                        if event_log is not None:
                            # missile_dodge: actor = dodging defender, target = missile-attacker
                            event_log.append(
                                {
                                    "event_type": "missile_dodge",
                                    "actor_id": lock.defender.player_id,
                                    "target_id": lock.attacker.player_id,
                                    "timestamp": int(second),
                                    "points_awarded": 0,
                                    "description": f"{lock.defender.name} dodges missile from {lock.attacker.name}",
                                    "metadata": _build_meta(
                                        lock.defender, lock.attacker
                                    ),
                                }
                            )
                            # RES-03: also emit a 'missiled' resolution row so
                            # the missile log records this fired missile as a
                            # miss (dodged).
                            event_log.append(
                                {
                                    "event_type": "missiled",
                                    "actor_id": lock.attacker.player_id,
                                    "target_id": lock.defender.player_id,
                                    "timestamp": int(second),
                                    "points_awarded": 0,
                                    "description": (
                                        f"{lock.attacker.name} misses "
                                        f"{lock.defender.name} with missile"
                                    ),
                                    "metadata": _build_meta(
                                        lock.attacker,
                                        lock.defender,
                                        result="miss",
                                        friendly_fire=bool(
                                            lock.attacker.team_color
                                            == lock.defender.team_color
                                        ),
                                    ),
                                }
                            )
                    else:
                        self._complete_missile(
                            lock.attacker, lock.defender, second, event_log
                        )
                # "miss": missile already consumed, no further action
            pending_missile_locks = still_locking_b

            # --- process pending reactions (deferred by shot cooldown) ---
            due_rx, pending_reactions = drain_reactions(pending_reactions, second)
            for rx in due_rx:
                r_attacker, r_defender = rx.attacker, rx.defender
                if r_attacker.final_lives <= 0 or r_defender.final_lives <= 0:
                    continue
                if not r_attacker.is_active_at(second):
                    continue
                if r_attacker.final_shots <= 0 and r_attacker.role != "ammo":
                    continue
                r_attacker.reaction_shots += 1
                r_attacker.last_shot_time = second
                _rx_elev_mod = _elevation_hit_modifier(
                    r_attacker.cell_row,
                    r_attacker.cell_col,
                    r_defender.cell_row,
                    r_defender.cell_col,
                    movement_ctx,
                )
                hit_chance = max(
                    10,
                    min(
                        95,
                        int(
                            (70 + r_attacker.accuracy - r_defender.survival)
                            * _rx_elev_mod
                            * r_attacker.stamina_hit_modifier
                        ),
                    ),
                )
                react_hit = random.randint(1, 100) < hit_chance
                if r_attacker.role != "ammo":
                    r_attacker.final_shots = max(0, r_attacker.final_shots - 1)
                if react_hit:
                    r_attacker.tags_made += 1
                    if r_defender.role == "medic":
                        r_attacker.medic_hits += 1
                    if r_attacker.role != "heavy":
                        r_attacker.final_special = min(
                            r_attacker.max_special, r_attacker.final_special + 1
                        )
                    r_attacker.points_scored += 100
                    r_attacker.last_tagged_id = r_defender.tag_id
                    r_defender.times_tagged += 1
                    r_defender.points_scored -= 20
                    if not r_defender.is_active_at(
                        second
                    ) and r_defender.is_taggable_at(second):
                        r_defender.times_tagged_in_reset_window += 1
                    r_defender.shields = max(
                        0, r_defender.shields - r_attacker.shot_power
                    )
                    if r_defender.shields == 0:
                        r_defender.final_lives = max(0, r_defender.final_lives - 1)
                        BatchSimulator._record_down(r_defender, second)
                        r_defender.shields = r_defender.max_shields
                        if r_defender.final_lives <= 0:
                            r_defender.was_eliminated_at = second
                            if event_log is not None:
                                event_log.append(
                                    {
                                        "event_type": "elimination",
                                        "actor_id": r_attacker.player_id,
                                        "target_id": r_defender.player_id,
                                        "timestamp": second,
                                        "points_awarded": 0,
                                        "description": f"{r_attacker.name} eliminates {r_defender.name} (reaction)",
                                        "metadata": _build_meta(
                                            r_attacker,
                                            r_defender,
                                            elimination_action="reaction",
                                        ),
                                    }
                                )
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "tag",
                                "actor_id": r_attacker.player_id,
                                "target_id": r_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 100,
                                "description": f"{r_attacker.name} reacts to {r_defender.name}",
                                "metadata": _build_meta(
                                    r_attacker, r_defender, is_reaction=True
                                ),
                            }
                        )
                else:
                    r_attacker.shots_missed += 1
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "miss",
                                "actor_id": r_attacker.player_id,
                                "target_id": r_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 0,
                                "description": f"{r_attacker.name} reaction miss on {r_defender.name}",
                                "metadata": _build_meta(
                                    r_attacker, r_defender, is_reaction=True
                                ),
                            }
                        )

            # --- process pending follow-ups (deferred by shot cooldown) ---
            due_fu, pending_followups = drain_followups(pending_followups, second)
            for fu in due_fu:
                fu_attacker, fu_defender, chain = (
                    fu.attacker,
                    fu.defender,
                    fu.chain_depth,
                )
                if fu_attacker.final_lives <= 0 or fu_defender.final_lives <= 0:
                    continue
                if fu_attacker.final_shots <= 0 and fu_attacker.role != "ammo":
                    continue
                fu_attacker.follow_up_shots += 1
                fu_attacker.last_shot_time = second
                _def_fu_elev_mod = _elevation_hit_modifier(
                    fu_attacker.cell_row,
                    fu_attacker.cell_col,
                    fu_defender.cell_row,
                    fu_defender.cell_col,
                    movement_ctx,
                )
                hit_chance = max(
                    10,
                    min(
                        95,
                        int(
                            (70 + fu_attacker.accuracy - fu_defender.survival)
                            * _def_fu_elev_mod
                            * fu_attacker.stamina_hit_modifier
                        ),
                    ),
                )
                fu_hit = random.randint(1, 100) < hit_chance
                if fu_attacker.role != "ammo":
                    fu_attacker.final_shots = max(0, fu_attacker.final_shots - 1)
                if fu_hit:
                    fu_attacker.tags_made += 1
                    if fu_defender.role == "medic":
                        fu_attacker.medic_hits += 1
                    if fu_attacker.role != "heavy":
                        fu_attacker.final_special = min(
                            fu_attacker.max_special, fu_attacker.final_special + 1
                        )
                    fu_attacker.points_scored += 100
                    fu_attacker.last_tagged_id = fu_defender.tag_id
                    fu_defender.times_tagged += 1
                    fu_defender.points_scored -= 20
                    if not fu_defender.is_active_at(
                        second
                    ) and fu_defender.is_taggable_at(second):
                        fu_defender.times_tagged_in_reset_window += 1
                    fu_defender.shields = max(
                        0, fu_defender.shields - fu_attacker.shot_power
                    )
                    downed = fu_defender.shields == 0
                    if downed:
                        fu_defender.final_lives = max(0, fu_defender.final_lives - 1)
                        BatchSimulator._record_down(fu_defender, second)
                        fu_defender.shields = fu_defender.max_shields
                        if fu_defender.final_lives <= 0:
                            fu_defender.was_eliminated_at = second
                            if event_log is not None:
                                event_log.append(
                                    {
                                        "event_type": "elimination",
                                        "actor_id": fu_attacker.player_id,
                                        "target_id": fu_defender.player_id,
                                        "timestamp": second,
                                        "points_awarded": 0,
                                        "description": f"{fu_attacker.name} eliminates {fu_defender.name} (follow-up)",
                                        "metadata": _build_meta(
                                            fu_attacker,
                                            fu_defender,
                                            elimination_action="follow_up_tag",
                                        ),
                                    }
                                )
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "tag",
                                "actor_id": fu_attacker.player_id,
                                "target_id": fu_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 100,
                                "description": f"{fu_attacker.name} follow-up tags {fu_defender.name}",
                                "metadata": _build_meta(
                                    fu_attacker,
                                    fu_defender,
                                    is_follow_up=True,
                                    chain=chain,
                                ),
                            }
                        )
                    if not downed and chain < 2 and fu_defender.final_lives > 0:
                        if fu_defender.player_awareness < random.randint(0, 100):
                            cd_ticks = _cooldown_ticks(fu_attacker, second)
                            if cd_ticks == 0:
                                due_fu.append(
                                    PendingFollowup(
                                        second, fu_attacker, fu_defender, chain + 1
                                    )
                                )
                            else:
                                pending_followups.append(
                                    PendingFollowup(
                                        second + cd_ticks,
                                        fu_attacker,
                                        fu_defender,
                                        chain + 1,
                                    )
                                )
                else:
                    fu_attacker.shots_missed += 1
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "miss",
                                "actor_id": fu_attacker.player_id,
                                "target_id": fu_defender.player_id,
                                "timestamp": second,
                                "points_awarded": 0,
                                "description": f"{fu_attacker.name} follow-up miss on {fu_defender.name}",
                                "metadata": _build_meta(
                                    fu_attacker, fu_defender, is_follow_up=True
                                ),
                            }
                        )

            # --- process pending nukes (MECH-05: after reactions/followups so tag-cancels land first) ---
            fired_n, pending_nukes = drain_nukes(pending_nukes, second)
            for n in fired_n:
                # MECH-05: check both liveness AND that the fuse was not disarmed via tag-cancel
                nuke_armed = n.player.special_active_until >= n.complete_time
                if n.player.final_lives > 0 and nuke_armed:
                    opposing = (
                        blue_players if n.player.team_color == "red" else red_players
                    )
                    self._complete_nuke(n.player, n.complete_time, opposing, event_log)

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
                    self._attempt_resupply(actor, plan["target"], second, event_log)
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
                        event_log,
                        second,
                        movement_ctx=plan.get("movement_ctx"),
                    )
                elif ptype == "missile":
                    # RES-03: emit a 'locking' event on lock start by routing
                    # event_log.append through the helper's emit_event seam.
                    scheduled = self._start_missile_lock(
                        actor,
                        plan["target"],
                        second,
                        movement_ctx,
                        emit_event=(
                            event_log.append if event_log is not None else None
                        ),
                    )
                    if scheduled:
                        pending_missile_locks.append(scheduled)
                elif ptype == "use_special":
                    scheduled = self._use_special(actor, second, all_alive, event_log)
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

                def _batch_emit(event_type: str, **kwargs) -> None:
                    if event_log is not None:
                        event_log.append(
                            _resupply_event_dict(event_type, kwargs, second)
                        )

                resolve_resupply_requests(
                    batch_resupply_requestors,
                    all_alive,
                    second,
                    movement_ctx,
                    emit_event=_batch_emit,
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

            # --- check for team elimination ---
            red_alive = [p for p in red_players if p.final_lives > 0]
            blue_alive = [p for p in blue_players if p.final_lives > 0]
            if not red_alive or not blue_alive:
                eliminated_at = second
                if not red_alive:
                    for p in blue_alive:
                        self._award_bases(p, event_log, second)
                if not blue_alive:
                    for p in red_alive:
                        self._award_bases(p, event_log, second)
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

    @staticmethod
    def _record_down(player, second) -> None:
        """Stamp a life-loss tick and drop the committed A* route.

        Centralises the two things every BatchSim life-loss site must do:
        record ``last_downed_time`` (drives the respawn cooldown) and clear
        ``_path_cache`` so the next move recomputes (MOVE-02 / ADR-0008 — a
        Down knocks the player off its committed route). One call site makes
        "every life-loss site clears the cache" structural rather than
        something a reviewer must re-verify seven times. Deliberately does
        *not* touch lives/shields — those differ per site
        (tag / follow-up / reaction / missile / nuke).

        MOVE-03: also force-clears Overwatch (``is_holding``) — a Down/respawn
        ends Hold, mirroring how it knocks the player off its committed route.
        Hanging this off the same helper keeps "every life-loss site clears
        the hold" structural rather than per-site review.
        """
        player.last_downed_time = second
        player._path_cache = None
        player.is_holding = False
        # MOVE-04 / ADR-0010: action-driven committed goals (from_action=True)
        # drop on a Down — the action that picked them is no longer current.
        # Positioning goals (from_action=False: step 3 / step 4 / default) are
        # role/map-derived and stay valid through a respawn, so they survive.
        if player._committed_goal is not None and player._committed_goal[1]:
            player._committed_goal = None

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
    ):
        outcomes = []
        for a in attempts:
            attacker, defender = a["attacker"], a["defender"]
            # MOVE-03: carry the Overwatch provenance flag from the attempt
            # into the outcome so the tag/miss event metadata can mark it.
            overwatch = a.get("overwatch", False)
            if attacker.final_shots <= 0 or defender.final_lives <= 0:
                outcomes.append(
                    {
                        "attacker": attacker,
                        "defender": defender,
                        "result": "invalid",
                        "overwatch": overwatch,
                    }
                )
                continue
            if defender.is_hiding and random.random() > 0.5:
                outcomes.append(
                    {
                        "attacker": attacker,
                        "defender": defender,
                        "result": "miss_hid",
                        "overwatch": overwatch,
                    }
                )
                continue
            elev_mod = _elevation_hit_modifier(
                attacker.cell_row,
                attacker.cell_col,
                defender.cell_row,
                defender.cell_col,
                movement_ctx,
            )
            hit_chance = max(
                10,
                min(
                    95,
                    int(
                        (70 + attacker.accuracy - defender.survival)
                        * elev_mod
                        * attacker.stamina_hit_modifier
                    ),
                ),
            )
            hit = random.randint(1, 100) < hit_chance
            outcomes.append(
                {
                    "attacker": attacker,
                    "defender": defender,
                    "result": "hit" if hit else "miss",
                    "overwatch": overwatch,
                }
            )

        for o in outcomes:
            attacker, defender = o["attacker"], o["defender"]
            if o["result"] == "invalid":
                continue
            if o["result"] == "miss_hid":
                if attacker.role != "ammo":
                    attacker.final_shots = max(0, attacker.final_shots - 1)
                attacker.shots_missed += 1
                attacker.last_shot_time = second
                if event_log is not None:
                    _miss_hid_extras: dict = {"reason": "hiding"}
                    # MOVE-03: Overwatch-origin provenance (reuses
                    # event_type="miss"; scoring/accuracy unchanged).
                    if o.get("overwatch", False):
                        _miss_hid_extras["overwatch"] = True
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{attacker.name} misses {defender.name} (hiding)",
                            "metadata": _build_meta(
                                attacker, defender, **_miss_hid_extras
                            ),
                        }
                    )
                continue

            if o["result"] == "hit":
                attacker.tags_made += 1
                if defender.role == "medic":
                    attacker.medic_hits += 1
                if attacker.role != "heavy":
                    attacker.final_special = min(
                        attacker.max_special, attacker.final_special + 1
                    )
                attacker.points_scored += 100
                attacker.last_tagged_id = defender.tag_id
                attacker.final_shots = max(0, attacker.final_shots - 1)
                attacker.last_shot_time = second

                defender.times_tagged += 1
                defender.points_scored -= 20
                defender.shields = max(0, defender.shields - attacker.shot_power)
                o["downed"] = defender.shields == 0

                # MECH-06: medic-under-fire alert
                if defender.role == "medic" and all_alive is not None:
                    _check_medic_under_fire(defender, all_alive, second)

                if event_log is not None:
                    _tag_extras: dict = {}
                    # MOVE-03: mark Overwatch-origin shots so the event carries
                    # provenance. Reuses event_type="tag" — scoring / MVP /
                    # accuracy paths are unchanged (analytics marker only).
                    if o.get("overwatch", False):
                        _tag_extras["overwatch"] = True
                    event_log.append(
                        {
                            "event_type": "tag",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 100,
                            "description": f"{attacker.name} tags {defender.name}",
                            "metadata": _build_meta(attacker, defender, **_tag_extras),
                        }
                    )

                if not defender.is_active_at(second) and defender.is_taggable_at(
                    second
                ):
                    defender.times_tagged_in_reset_window += 1
                if defender.shields == 0:
                    if (
                        defender.role == "commander"
                        and defender.special_active_until > second
                    ):
                        defender.special_active_until = 0
                    defender.final_lives = max(0, defender.final_lives - 1)
                    BatchSimulator._record_down(defender, second)
                    defender.shields = defender.max_shields
                    if defender.final_lives <= 0:
                        defender.was_eliminated_at = second
                        if event_log is not None:
                            event_log.append(
                                {
                                    "event_type": "elimination",
                                    "actor_id": attacker.player_id,
                                    "target_id": defender.player_id,
                                    "timestamp": second,
                                    "points_awarded": 0,
                                    "description": f"{defender.name} eliminated by {attacker.name}",
                                    "metadata": _build_meta(
                                        attacker, defender, elimination_action="tag"
                                    ),
                                }
                            )
                # MECH-06: tag confirms enemy position and status — update memory and broadcast
                if movement_ctx is not None and all_alive is not None:
                    _update_player_memory(attacker, [defender], second)
                    _broadcast_communication(attacker, all_alive, movement_ctx, second)
            else:
                attacker.final_shots = max(0, attacker.final_shots - 1)
                attacker.shots_missed += 1
                attacker.last_shot_time = second
                if event_log is not None:
                    _miss_extras: dict = {}
                    # MOVE-03: Overwatch-origin miss provenance (reuses
                    # event_type="miss"; scoring/accuracy unchanged).
                    if o.get("overwatch", False):
                        _miss_extras["overwatch"] = True
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{attacker.name} misses {defender.name}",
                            "metadata": _build_meta(attacker, defender, **_miss_extras),
                        }
                    )

        # Reactions: hit or miss may trigger a player_awareness roll.
        # Rapid-fire scouts react this tick; everyone else is scheduled for their next eligible shot.
        immediate_reactions = []
        for o in outcomes:
            if o["result"] not in ("hit", "miss"):
                continue
            r_reactor = o["defender"]
            r_target = o["attacker"]
            if not r_reactor.is_active_at(second) or r_reactor.final_lives <= 0:
                continue
            if r_reactor.final_shots <= 0 and r_reactor.role != "ammo":
                continue
            if r_target.final_lives <= 0:
                continue
            if r_reactor.player_awareness >= random.randint(0, 100):
                cd_ticks = _cooldown_ticks(r_reactor, second)
                if cd_ticks == 0:
                    immediate_reactions.append(
                        {"attacker": r_reactor, "defender": r_target}
                    )
                elif pending_reactions is not None:
                    pending_reactions.append(
                        PendingReaction(second + cd_ticks, r_reactor, r_target)
                    )

        for ra in immediate_reactions:
            r_attacker = ra["attacker"]
            r_defender = ra["defender"]
            if r_defender.final_lives <= 0:
                continue
            _imm_rx_elev_mod = _elevation_hit_modifier(
                r_attacker.cell_row,
                r_attacker.cell_col,
                r_defender.cell_row,
                r_defender.cell_col,
                movement_ctx,
            )
            hit_chance = max(
                10,
                min(
                    95,
                    int(
                        (70 + r_attacker.accuracy - r_defender.survival)
                        * _imm_rx_elev_mod
                        * r_attacker.stamina_hit_modifier
                    ),
                ),
            )
            react_hit = random.randint(1, 100) < hit_chance
            r_attacker.reaction_shots += 1
            r_attacker.last_shot_time = second
            if r_attacker.role != "ammo":
                r_attacker.final_shots = max(0, r_attacker.final_shots - 1)
            if react_hit:
                r_attacker.tags_made += 1
                if r_defender.role == "medic":
                    r_attacker.medic_hits += 1
                if r_attacker.role != "heavy":
                    r_attacker.final_special = min(
                        r_attacker.max_special, r_attacker.final_special + 1
                    )
                r_attacker.points_scored += 100
                r_attacker.last_tagged_id = r_defender.tag_id
                r_defender.times_tagged += 1
                r_defender.points_scored -= 20
                if not r_defender.is_active_at(second) and r_defender.is_taggable_at(
                    second
                ):
                    r_defender.times_tagged_in_reset_window += 1
                r_defender.shields = max(0, r_defender.shields - r_attacker.shot_power)
                if r_defender.shields == 0:
                    r_defender.final_lives = max(0, r_defender.final_lives - 1)
                    BatchSimulator._record_down(r_defender, second)
                    r_defender.shields = r_defender.max_shields
                    if r_defender.final_lives <= 0:
                        r_defender.was_eliminated_at = second
                        if event_log is not None:
                            event_log.append(
                                {
                                    "event_type": "elimination",
                                    "actor_id": r_attacker.player_id,
                                    "target_id": r_defender.player_id,
                                    "timestamp": second,
                                    "points_awarded": 0,
                                    "description": f"{r_attacker.name} eliminates {r_defender.name} (reaction)",
                                    "metadata": _build_meta(
                                        r_attacker,
                                        r_defender,
                                        elimination_action="reaction",
                                    ),
                                }
                            )
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "tag",
                            "actor_id": r_attacker.player_id,
                            "target_id": r_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 100,
                            "description": f"{r_attacker.name} reacts to {r_defender.name}",
                            "metadata": _build_meta(
                                r_attacker, r_defender, is_reaction=True
                            ),
                        }
                    )
            else:
                r_attacker.shots_missed += 1
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": r_attacker.player_id,
                            "target_id": r_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{r_attacker.name} reaction miss on {r_defender.name}",
                            "metadata": _build_meta(
                                r_attacker, r_defender, is_reaction=True
                            ),
                        }
                    )

        # Follow-up tags: if a hit did NOT down the defender (shields still > 0 after
        # the shot), the attacker may fire again. A hit that takes shields to 0 is
        # never eligible — a heavy one-shotting a commander never generates follow-ups.
        # Rapid-fire scouts fire this tick; everyone else is scheduled for their next eligible shot.
        immediate_follow_ups = []
        for o in outcomes:
            if o["result"] != "hit" or o.get("downed", False):
                continue
            if o["defender"].final_lives <= 0:
                continue
            if o["attacker"].final_shots <= 0 and o["attacker"].role != "ammo":
                continue
            if o["defender"].player_awareness < random.randint(0, 100):
                cd_ticks = _cooldown_ticks(o["attacker"], second)
                if cd_ticks == 0:
                    immediate_follow_ups.append(
                        {
                            "attacker": o["attacker"],
                            "defender": o["defender"],
                            "chain": 1,
                        }
                    )
                elif pending_followups is not None:
                    pending_followups.append(
                        PendingFollowup(
                            second + cd_ticks, o["attacker"], o["defender"], 1
                        )
                    )

        for fu in immediate_follow_ups:
            fu_attacker = fu["attacker"]
            fu_defender = fu["defender"]
            if fu_defender.final_lives <= 0:
                continue
            if fu_attacker.final_shots <= 0 and fu_attacker.role != "ammo":
                continue
            _imm_fu_elev_mod = _elevation_hit_modifier(
                fu_attacker.cell_row,
                fu_attacker.cell_col,
                fu_defender.cell_row,
                fu_defender.cell_col,
                movement_ctx,
            )
            hit_chance = max(
                10,
                min(
                    95,
                    int(
                        (70 + fu_attacker.accuracy - fu_defender.survival)
                        * _imm_fu_elev_mod
                        * fu_attacker.stamina_hit_modifier
                    ),
                ),
            )
            fu_hit = random.randint(1, 100) < hit_chance
            fu_attacker.follow_up_shots += 1
            fu_attacker.last_shot_time = second
            if fu_attacker.role != "ammo":
                fu_attacker.final_shots = max(0, fu_attacker.final_shots - 1)
            if fu_hit:
                fu_attacker.tags_made += 1
                if fu_defender.role == "medic":
                    fu_attacker.medic_hits += 1
                if fu_attacker.role != "heavy":
                    fu_attacker.final_special = min(
                        fu_attacker.max_special, fu_attacker.final_special + 1
                    )
                fu_attacker.points_scored += 100
                fu_attacker.last_tagged_id = fu_defender.tag_id
                fu_defender.times_tagged += 1
                fu_defender.points_scored -= 20
                if not fu_defender.is_active_at(second) and fu_defender.is_taggable_at(
                    second
                ):
                    fu_defender.times_tagged_in_reset_window += 1
                fu_defender.shields = max(
                    0, fu_defender.shields - fu_attacker.shot_power
                )
                downed = fu_defender.shields == 0
                if downed:
                    fu_defender.final_lives = max(0, fu_defender.final_lives - 1)
                    BatchSimulator._record_down(fu_defender, second)
                    fu_defender.shields = fu_defender.max_shields
                    if fu_defender.final_lives <= 0:
                        fu_defender.was_eliminated_at = second
                        if event_log is not None:
                            event_log.append(
                                {
                                    "event_type": "elimination",
                                    "actor_id": fu_attacker.player_id,
                                    "target_id": fu_defender.player_id,
                                    "timestamp": second,
                                    "points_awarded": 0,
                                    "description": f"{fu_attacker.name} eliminates {fu_defender.name} (follow-up)",
                                    "metadata": _build_meta(
                                        fu_attacker,
                                        fu_defender,
                                        elimination_action="follow_up_tag",
                                    ),
                                }
                            )
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "tag",
                            "actor_id": fu_attacker.player_id,
                            "target_id": fu_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 100,
                            "description": f"{fu_attacker.name} follow-up tags {fu_defender.name}",
                            "metadata": _build_meta(
                                fu_attacker,
                                fu_defender,
                                is_follow_up=True,
                                chain=fu["chain"],
                            ),
                        }
                    )
                if not downed and fu["chain"] < 2 and fu_defender.final_lives > 0:
                    if fu_defender.player_awareness < random.randint(0, 100):
                        # rapid-fire: chain immediately in this loop
                        immediate_follow_ups.append(
                            {
                                "attacker": fu_attacker,
                                "defender": fu_defender,
                                "chain": fu["chain"] + 1,
                            }
                        )
            else:
                fu_attacker.shots_missed += 1
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "miss",
                            "actor_id": fu_attacker.player_id,
                            "target_id": fu_defender.player_id,
                            "timestamp": second,
                            "points_awarded": 0,
                            "description": f"{fu_attacker.name} follow-up miss on {fu_defender.name}",
                            "metadata": _build_meta(
                                fu_attacker, fu_defender, is_follow_up=True
                            ),
                        }
                    )

    def _attempt_resupply(self, tagger, teammate, second, event_log=None):
        emit = event_log.append if event_log is not None else None
        _attempt_resupply_shared(tagger, teammate, second, emit_event=emit)

    def _change_zone(self, player, towards=None):
        if player.current_zone == 1:
            player.current_zone = (
                towards if towards in (0, 2) else random.choice([0, 2])
            )
        else:
            player.current_zone = 1

    def _capture_base(
        self, player, base_id, event_log=None, second=0, movement_ctx=None
    ):
        emit = event_log.append if event_log is not None else None
        _capture_base_shared(player, base_id, second, movement_ctx, emit_event=emit)

    def _award_bases(self, player, event_log=None, second=0):
        emit = event_log.append if event_log is not None else None
        _award_bases_shared(player, second, emit_event=emit)

    def _start_missile_lock(
        self, attacker, defender, second, movement_ctx=None, *, emit_event=None
    ):
        return _start_missile_lock_shared(
            attacker, defender, second, movement_ctx, emit_event=emit_event
        )

    def _complete_missile(self, attacker, defender, second, event_log=None):
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
                if event_log is not None:
                    event_log.append(
                        {
                            "event_type": "elimination",
                            "actor_id": attacker.player_id,
                            "target_id": defender.player_id,
                            "timestamp": int(second),
                            "points_awarded": 0,
                            "description": f"{defender.name} eliminated by missile from {attacker.name}",
                            "metadata": _build_meta(
                                attacker, defender, elimination_action="missile"
                            ),
                        }
                    )
            BatchSimulator._record_down(defender, second)
            defender.times_missiled += 1

            attacker.points_scored += 500
            attacker.missile_points += 500
            attacker.final_missiles -= 1
            attacker.missiles_landed += 1
            if attacker.role != "heavy":
                attacker.final_special = min(
                    attacker.max_special, attacker.final_special + 2
                )
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "missiled",
                        "actor_id": attacker.player_id,
                        "target_id": defender.player_id,
                        "timestamp": int(second),
                        "points_awarded": 500,
                        "description": f"{attacker.name} hits {defender.name} with missile",
                        "metadata": _build_meta(
                            attacker,
                            defender,
                            result="hit",
                            friendly_fire=bool(
                                attacker.team_color == defender.team_color
                            ),
                        ),
                    }
                )

    def _use_special(self, player, second, all_alive, event_log=None):
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
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} activates nuke",
                        "metadata": _build_meta(player, fires_at=second + countdown),
                    }
                )
            return ("nuke", second + countdown, player)
        elif player.role == "scout":
            player.final_special -= player.special_cost
            # TIME-01: rapid fire lasts the whole round (tick-native sentinel).
            player.special_active_until = TICKS_PER_ROUND
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} activates rapid fire",
                        "metadata": _build_meta(player),
                    }
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
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} team heal special",
                        "metadata": _build_meta(
                            player,
                            targets=[
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
                        ),
                    }
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
            if event_log is not None:
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 0,
                        "description": f"{player.name} team ammo special",
                        "metadata": _build_meta(
                            player,
                            targets=[
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
                        ),
                    }
                )
        return None

    def _complete_nuke(self, player, second, opposing_players, event_log=None):
        if player.is_active_at(second) and player.final_lives > 0:
            player.points_scored += 500
            if event_log is not None:
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
                event_log.append(
                    {
                        "event_type": "special",
                        "actor_id": player.player_id,
                        "target_id": None,
                        "timestamp": second,
                        "points_awarded": 500,
                        "description": f"{player.name} nuke detonates",
                        "metadata": _build_meta(player, targets=projected_targets),
                    }
                )
            for opp in opposing_players:
                if opp.final_lives <= 0:
                    continue
                lives_taken = min(opp.final_lives, 3)
                opp.final_lives -= lives_taken
                BatchSimulator._record_down(opp, second)
                opp.shields = opp.max_shields
                if opp.role == "commander" and opp.special_active_until > second:
                    opp.special_active_until = 0
                if opp.final_lives <= 0:
                    opp.was_eliminated_at = second
                    if event_log is not None:
                        event_log.append(
                            {
                                "event_type": "elimination",
                                "actor_id": player.player_id,
                                "target_id": opp.player_id,
                                "timestamp": second,
                                "points_awarded": 0,
                                "description": f"{opp.name} eliminated by nuke",
                                "metadata": _build_meta(
                                    player, opp, elimination_action="nuke"
                                ),
                            }
                        )

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
