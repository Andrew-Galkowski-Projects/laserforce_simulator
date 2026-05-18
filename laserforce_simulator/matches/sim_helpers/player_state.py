from dataclasses import dataclass, field
from typing import Optional

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
    speed: int = 50  # 0-100, from Player model; cells traversed per move tick
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
    last_tagged_id: Optional[str] = None

    # missile bookkeeping
    missiles_landed: int = 0

    # base bookkeeping
    neutral_base_destroyed: bool = False
    opposing_base_destroyed: bool = False

    # DB identity — populated by _make_players so _flush_to_db can resolve FKs
    player_id: int = 0

    # aggregate stats for batch results
    points_scored: int = 0
    tags_made: int = 0
    times_tagged: int = 0
    shots_missed: int = 0
    times_missiled: int = 0
    resupplies_given: int = 0
    combo_resupply_count: int = 0
    specials_used: int = 0
    times_tagged_in_reset_window: int = 0
    missile_points: int = 0
    follow_up_shots: int = 0
    reaction_shots: int = 0
    last_shot_time: float = -99.0  # transient; tracks shot cooldown enforcement
    last_chosen_action: str = ""  # action chosen in previous tick; guides movement goal

    # MOVE-01: transient compact movement trail — list of
    # (start_cell, end_cell, timestamp) tuples, one per actual cell change.
    # No DB column / no migration; _flush_to_db turns these into compact
    # movement GameEvents (start+end+timestamp) when a batch round is saved.
    # The exact intermediate route is recomputed on demand at replay via
    # deterministic A* start->end (not stored here).
    movement_trail: list = field(default_factory=list)

    # MECH-04: transient nuke-reaction flag — reset each tick, never persisted to DB
    reacting_to_nuke: bool = False

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

    def __post_init__(self) -> None:
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
        return self.missiles_landed

    @property
    def can_use_special(self) -> bool:
        return self.final_special >= self.special_cost

    @property
    def can_capture_base_in_current_zone(self) -> bool:
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
        return self.is_active_at(tick)

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
    # teamwork: scales ally-cover bias in _goal_from_role (pathfinding.py)
    # communication: % chance to broadcast spotted enemy positions to nearby teammates
    # Global broadcasts: nuke activation, score delta every 3 min, medic-under-fire alert
