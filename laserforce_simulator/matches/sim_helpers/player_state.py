from dataclasses import dataclass, field
from typing import Any, Optional

from matches.sim_helpers.modes import Mode, default_mode
from matches.sim_helpers.player_counters import PlayerCounters
from matches.sim_helpers.support_survival import LastSingle, Visit
from matches.sim_helpers.role_constants import (
    MAX_LIVES as _MAX_LIVES,
    MAX_SHOTS as _MAX_SHOTS,
    ROLE_STATS as _ROLE_STATS,
    SPECIAL_COST as _SPECIAL_COST,
)
from matches.sim_helpers.time_constants import (
    NOT_TARGETABLE_TICKS,
    RESPAWN_TICKS,
    SCORE_BROADCAST_PERIOD_TICKS,
    SURVIVED_SENTINEL,
)


@dataclass
class PlayerState:
    """In-memory player state for batch simulation. No DB writes ever occur."""

    tag_id: str  # unique string e.g. "red_commander", "blue_scout_1"
    name: str
    team_color: str
    role: str
    accuracy: int  # 0-100, from Player model
    survival: int  # 0-100, from Player model

    starting_lives: int
    starting_shots: int
    final_lives: int
    final_shots: int
    final_special: int = 0
    decision_making: int = 50
    stamina: int = 50
    special_usage: int = 50
    resupply_efficiency: int = 50
    resupply_synergy: int = 50
    teamwork: int = 50
    communication: int = 50
    speed: int = 50  # 0-100, from Player model; 5-10 ft/s (MOVE-08)
    # MOVE-06: sharpens the positioning-goal draw (goal_scoring.temperature).
    positioning: int = 50
    final_missiles: int = 0
    shields: int = 1
    player_awareness: int = 50  # 0-100, from Player model
    game_awareness: int = 50  # 0-100, from Player model
    resource_awareness: int = 50  # 0-100, from Player model

    current_zone: int = 0
    cell_row: Optional[int] = None
    cell_col: Optional[int] = None
    was_eliminated_at: int = SURVIVED_SENTINEL
    last_downed_time: Optional[int] = None
    special_active_until: int = 0
    is_hiding: bool = False
    # MOVE-03: transient Overwatch flag (mirrors is_hiding). Set on a ``hold``
    # roll. On a map round it lasts until ``hold_until`` or an early end; on a
    # map-less one it carries over until a non-Hold Action or a Down/respawn. No DB
    # column / no migration (PlayerRoundState has no such attr by default —
    # read everywhere via getattr(player, "is_holding", False)).
    is_holding: bool = False
    # MECH-17: the tick a map-round Hold ends (roll + HOLD_TICKS). None on map-less
    # rounds and when no Hold stands.
    hold_until: Optional[int] = None
    # RV-02: Medic reset-chain counter (GLOSSARY.md "Medic reset chain").
    # Incremented in BatchSimulator._record_down (before stamping
    # last_downed_time) when the player is re-Downed before recovering
    # (``not is_active_at(second)``); fires a one-shot ``medic_reset`` event
    # when it reaches 2 for a medic; reset to 0 once the player is fully active
    # again. Transient — no DB column / no migration. Consumed only for the
    # highlight; no RNG, no mechanics effect.
    down_chain_count: int = 0
    last_tagged_id: Optional[str] = None

    # base bookkeeping
    neutral_base_destroyed: bool = False
    opposing_base_destroyed: bool = False
    # MECH-17: set by ``combat.plan_action`` on map rounds: whether a capture can
    # happen on this cell right now. None on map-less rounds, which keep the zone logic.
    base_in_reach: Optional[bool] = None
    # MECH-17: set by ``combat.plan_action`` on map rounds: ``modes.known_threats``.
    # None on map-less rounds, which is how the weights tell the two apart.
    known_threats: Optional[int] = None

    # DB identity — populated by _make_players so _flush_to_db can resolve FKs
    player_id: int = 0

    # 18 per-round counter accumulators (see sim_helpers/player_counters.py).
    # Writers say ``player.counters.tags_made += 1``; ``_flush_to_db`` splats the
    # whole bundle via ``**asdict(p.counters)``. See ADR-0018.
    counters: PlayerCounters = field(default_factory=PlayerCounters)

    # Hits landed on an enemy Medic. Drives PlayerRoundState.final_medic_hits
    # (flushed at round end) and the MVP bonus in score_calculator. Not in
    # PlayerCounters because the PRS-side column is named ``final_medic_hits``,
    # not ``medic_hits`` — keeping the rename adapter local to ``_flush_to_db``.
    medic_hits: int = 0
    last_shot_time: float = -99.0  # transient; tracks shot cooldown enforcement
    last_chosen_action: str = ""  # action chosen in previous tick; guides movement goal

    # MOVE-01: transient compact movement trail — list of
    # (start_cell, end_cell, timestamp) tuples, one per actual cell change.
    # No DB column / no migration; _flush_to_db turns these into compact
    # movement GameEvents (start+end+timestamp) when a batch round is saved.
    # The exact intermediate route is recomputed on demand at replay via
    # deterministic A* start->end (not stored here).
    movement_trail: list = field(default_factory=list)

    # Playback overlay: transient per-Advance route (the exact cells the player
    # walked this Advance — ``_last_step_cells``, excluding the start cell,
    # ending at the end cell), appended in lockstep with ``movement_trail`` so
    # the round-playback map can draw the TRUE path rather than re-deriving it
    # with an independent A* per Advance (which diverges/zig-zags). No DB
    # column / no migration; ``_flush_to_db`` writes it to each movement
    # GameEvent's ``metadata["route"]``.
    movement_routes: list = field(default_factory=list)

    # MOVE-02: transient goal-keyed A* path cache (BatchSimulator only —
    # ADR-0008). None or a (goal_cell, remaining_cells, anchor) tuple:
    # remaining_cells is the ordered list of cells still to walk (head = next
    # cell); anchor is the cell the previous re-step left the player on, used
    # to detect off-route displacement. Lets move ticks re-step a committed
    # route instead of recomputing full A* over the ~3700-cell graph every
    # tick. No DB column / no migration (mirrors movement_trail); default None
    # so it is never a required ctor arg and never crosses the parallel-worker
    # process boundary. Managed by pathfinding.astar_advance_cached; cleared
    # to None on Down/respawn via BatchSimulator._record_down.
    _path_cache: Optional[tuple] = None

    # MOVE-04: transient goal-commitment tuple (BatchSimulator only —
    # ADR-0010). None or a ``(goal_cell, from_action, expires_at_tick)``
    # 3-tuple: ``goal_cell`` is the committed Goal cell, ``from_action`` is
    # True iff ``choose_goal_cell`` step 2 (``_goal_from_action``) produced it
    # (False for step 3 / step 4 / None-fallback), and ``expires_at_tick`` is
    # the tick at which the commitment lapses (set to ``second +
    # GOAL_RECOMPUTE_PERIOD_TICKS`` on each recompute). Lets steady-state
    # goal selection (``choose_goal_cell`` steps 2–4) run every N ticks
    # instead of every tick; reactive overrides (steps 0/1/1b) still run
    # every tick and clear the commitment as a side effect when they fire.
    # No DB column / no migration (mirrors ``_path_cache``); default None so
    # it is never a required ctor arg and never crosses the parallel-worker
    # process boundary. Managed by ``pathfinding.choose_goal_cell``; cleared
    # to None on Down/respawn via ``BatchSimulator._record_down`` iff the
    # committed goal came from an action (from_action=True) — positioning
    # goals (from_action=False) survive Down. Also cleared on the non-hide /
    # non-hold action-roll transitions in ``combat.plan_action``.
    _committed_goal: Optional[tuple[tuple[int, int], bool, int]] = None

    # MOVE-03: transient list of cells actually traversed by the most recent
    # astar_advance_cached call this tick (intermediate + end cells walked,
    # excluding the start cell); [] when nothing moved. Used by the
    # BatchSimulator Overwatch resolution to test whether a mover crossed a
    # holder's line of sight mid-Advance. No DB column / no migration; never
    # crosses the parallel-worker process boundary.
    _last_step_cells: list = field(default_factory=list)

    # MECH-04: transient nuke-reaction flag — reset each tick, never persisted to DB
    reacting_to_nuke: bool = False

    # CAL #558 (2026-11-07): the SP threshold drawn for the Commander's next nuke
    # (``nuke_arming.banked_enough``); None until drawn, None again once it fires.
    # Transient, never persisted to DB.
    nuke_sp_threshold: Optional[int] = None
    # CAL #558 (2026-11-07): this nuke's slip roll (``nuke_arming.fuse_lit``): True
    # when the Commander's supports may still resupply it while the fuse runs.
    # Transient, never persisted to DB.
    nuke_resupplyable: bool = False

    # MECH-06: per-player memory — {tag_id: {"cell": (r,c), "timestamp": s, "role": role}}
    # Transient; never persisted to DB.
    player_memory: dict = field(default_factory=dict)

    # MECH-06: medic-under-fire tracking — list of seconds at which this Medic was hit.
    # Only meaningful for the medic role.
    medic_hit_times: list = field(default_factory=list)

    # MECH-06: score broadcast state — set every SCORE_BROADCAST_PERIOD_TICKS
    # by the simulator tick loop.
    # {"winning_team": "red"|"blue"|"tied", "timestamp": tick}  or None if not yet broadcast.
    score_broadcast_state: dict = field(default_factory=dict)

    # MECH-06: next tick at which the score broadcast should fire for this player.
    score_broadcast_next: int = SCORE_BROADCAST_PERIOD_TICKS

    # stamina tracking (transient — not persisted to DB)
    stamina_penalty_count: int = 0
    stamina_next_check_pct: int = 10  # next 10% checkpoint to evaluate

    # MECH-10: resupply death % inputs (transient — not persisted to DB).
    # ``last_resupplied_at`` is the tick this player last received a
    # Resupply (stamped by ``combat.attempt_resupply``); ``record_down``
    # counts every Down in ``downs_taken`` and a Down within
    # ``RESPAWN_TICKS`` of that Resupply in ``downs_in_resupply_cooldown``.
    last_resupplied_at: Optional[int] = None
    # CAL-03 rule 1: the last single Resupply received (kind, tick, supporter),
    # read by ``support_survival.double_window_open``; None before the first.
    last_single: Optional[LastSingle] = None
    # CAL-03 rule 9: the tick the Ammo left its Medic (``medic_with_ammo`` turned
    # False), None while with it; set and cleared by ``combat._support_link``.
    link_left_at: Optional[int] = None
    # CAL-04c: the first plan the Ammo was more than ``LEASH_SQUARES`` from its
    # memory's cell for its Medic, None within reach or with no remembered cell;
    # set and cleared by ``combat._support_link``, read by ``_leash_goal``.
    leash_far_since: Optional[int] = None
    # CAL-03 rule 6: the current Resupply visit, or None (``resupply_visit``); read
    # by ``support_survival.hold_allows`` and ``choose_goal_cell``.
    visit: Optional[Visit] = None
    # CAL-03 rule 6: the earliest tick a new visit may start (the cooldown).
    visit_next_tick: int = 0
    # CAL-04b: the earliest tick a Critical visit may start (its own cooldown).
    critical_visit_next_tick: int = 0
    # CAL-03 rule 10: a Commander's or Scout's last known backline position.
    backline_cell: Optional[tuple[int, int]] = None
    # CAL-03 rule 7 (supports): call-outs from teammates at 1-3 lives, freshest
    # first (``resupply_visit.Callout``).
    support_callouts: list = field(default_factory=list)
    downs_taken: int = 0
    downs_in_resupply_cooldown: int = 0

    # MECH-10 Follow rule state (transient — not persisted to DB), managed
    # by ``follow_rule``: this player's open Trail (``follow_rule.Trail`` or
    # None) and, as an attacker, Chase progress per enemy keyed by
    # ``tag_id_key`` (``follow_rule.Chase``).
    _trail: Any = None
    _chases: dict = field(default_factory=dict)

    # MOVE-08 Downed option (transient — not persisted to DB), managed by
    # ``downed_option``: the plan rolled at the last Down or Resupply
    # (``downed_option.DownedPlan`` or None), and the earliest detonation
    # tick of a pending enemy Nuke (set each tick with ``reacting_to_nuke``).
    downed_plan: Any = None
    nuke_detonates_at: Optional[int] = None
    # The tick of the last resupply-trail roll (one roll per combo Resupply).
    resupply_rolled_at: Optional[float] = None
    # MOVE-08: the fraction of a square carried to the next tick's Advance
    # (``pathfinding.squares_this_tick``).
    move_carry: float = 0.0

    # MOVE-07: this player's fixed route-noise seed for the round (drawn at
    # round start; ``pathfinding.route_noise``), None = plain shortest routes.
    route_seed: Optional[int] = None
    # MOVE-06: True when the current goal came from the scored draw, so
    # ``choose_goal_cell`` holds it until reached.
    _goal_was_drawn: bool = False

    # MECH-07 Mode state (transient — not persisted to DB), managed by ``modes``:
    # the tick the current Mode started, the earliest tick of the next switch
    # check, the ticks spent in each Mode, and the deadline of a Control hold on a
    # reached goal. ``mode`` itself starts in ``default_mode(role)``. Map rounds only.
    mode_since: int = 0
    mode_next_check: int = 0
    mode_ticks: dict[Mode, int] = field(default_factory=dict)
    mode_hold_until: Optional[int] = None

    # uptime breakdown in ticks (accumulated +1 each tick)
    ticks_active: int = 0
    ticks_not_targetable: int = 0
    ticks_reset_window: int = 0

    # Role-derived constants — cached once in __post_init__, never change per instance.
    # Storing as plain fields avoids repeated dict lookups inside the hot tick loop.
    max_lives: int = field(init=False)
    max_shots: int = field(init=False)
    max_shields: int = field(init=False)
    shot_power: int = field(init=False)
    special_cost: int = field(init=False)
    mode: Mode = field(init=False)

    def __post_init__(self) -> None:
        self.mode = default_mode(self.role)
        self.max_lives = _MAX_LIVES.get(self.role, 15)
        self.max_shots = _MAX_SHOTS.get(self.role, 30)
        role_stats = _ROLE_STATS.get(self.role, {})
        self.max_shields = role_stats.get("shield", 1)
        self.shot_power = role_stats.get("shot_power", 1)
        self.special_cost = _SPECIAL_COST.get(self.role, 100)

    # ------------------------------------------------------------------ #
    # Properties matching PlayerRoundState interface used by weights.py
    # and battle resolution so the weight functions work unchanged.
    # ------------------------------------------------------------------ #

    @property
    def max_special(self) -> int:
        return 99

    @property
    def missiles_used(self) -> int:
        return self.counters.missiles_landed

    @property
    def can_use_special(self) -> bool:
        return self.final_special >= self.special_cost

    @property
    def can_capture_base_in_current_zone(self) -> bool:
        if self.base_in_reach is not None:
            return self.base_in_reach
        if self.current_zone == 1 and not self.neutral_base_destroyed:
            return True
        if (
            self.team_color == "red"
            and self.current_zone == 2
            and not self.opposing_base_destroyed
        ):
            return True
        if (
            self.team_color == "blue"
            and self.current_zone == 0
            and not self.opposing_base_destroyed
        ):
            return True
        return False

    def is_active_at(self, tick: int) -> bool:
        """TIME-01: BatchSimulator is fully tick-native; ``tick`` is an integer
        tick index and the respawn cooldown is compared in ticks."""
        if self.final_lives == 0:
            return False
        if (
            self.last_downed_time is not None
            and tick - self.last_downed_time < RESPAWN_TICKS
        ):
            return False
        return True

    def is_taggable_at(self, tick: int) -> bool:
        """TIME-01: tick-domain not-targetable window (was < 4 s)."""
        if self.final_lives == 0:
            return False
        if (
            self.last_downed_time is not None
            and tick - self.last_downed_time < NOT_TARGETABLE_TICKS
        ):
            return False
        return True

    def is_resupplyable_at(self, tick: int) -> bool:
        """Active, and not a Commander whose nuke fuse runs (``nuke_arming``)."""
        from matches.sim_helpers import nuke_arming

        return self.is_active_at(tick) and nuke_arming.resupplyable(self, tick)

    @property
    def stamina_hit_modifier(self) -> float:
        return max(0.5, 1.0 - 0.05 * self.stamina_penalty_count)

    @property
    def tag_id_key(self) -> str:
        """Common tag-identity accessor used by choose_tag_target in mechanics.py."""
        return self.tag_id

    # ------ Stats wired in MECH-01 ------
    # resupply_efficiency: affects request_resupply action weight; doubles speed for Medic/Ammo
    # resupply_synergy: keeps support pairs together; scales double-resupply chance

    # ------ Stats wired in MECH-06 ------
    # teamwork: the Defense closeness term in modes.mode_goal (MECH-07)
    # communication: % chance to broadcast spotted enemy positions to nearby teammates
    # Global broadcasts: score delta every 3 min, medic-under-fire alert (a nuke
    # activation sets reacting_to_nuke only; it shares no cell, CAL #558)
