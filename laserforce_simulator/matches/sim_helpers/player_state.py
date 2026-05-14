from dataclasses import dataclass
from typing import Optional

from matches.sim_helpers.role_constants import (
    MAX_LIVES as _MAX_LIVES,
    MAX_SHOTS as _MAX_SHOTS,
    ROLE_STATS as _ROLE_STATS,
    SPECIAL_COST as _SPECIAL_COST,
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
    final_missiles: int = 0
    shields: int = 1
    player_awareness: int = 50  # 0-100, from Player model

    current_zone: int = 0
    cell_row: Optional[int] = None
    cell_col: Optional[int] = None
    was_eliminated_at: int = 901
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
    specials_used: int = 0
    times_tagged_in_reset_window: int = 0
    missile_points: int = 0
    follow_up_shots: int = 0
    reaction_shots: int = 0
    last_shot_time: float = -99.0  # transient; tracks shot cooldown enforcement
    last_chosen_action: str = ""  # action chosen in previous tick; guides movement goal

    # uptime breakdown in seconds (accumulated each tick)
    seconds_active: int = 0
    seconds_not_targetable: int = 0
    seconds_reset_window: int = 0

    # ------------------------------------------------------------------ #
    # Properties matching PlayerRoundState interface used by weights.py
    # and battle resolution so the weight functions work unchanged.
    # ------------------------------------------------------------------ #

    @property
    def max_lives(self) -> int:
        return _MAX_LIVES.get(self.role, 15)

    @property
    def max_shots(self) -> int:
        return _MAX_SHOTS.get(self.role, 30)

    @property
    def max_special(self) -> int:
        return 99

    @property
    def max_shields(self) -> int:
        return _ROLE_STATS.get(self.role, {}).get("shield", 1)

    @property
    def shot_power(self) -> int:
        return _ROLE_STATS.get(self.role, {}).get("shot_power", 1)

    @property
    def special_cost(self) -> int:
        return _SPECIAL_COST.get(self.role, 100)

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

    def is_active_at(self, second: int) -> bool:
        if self.final_lives == 0:
            return False
        if self.last_downed_time is not None and second - self.last_downed_time < 8:
            return False
        return True

    def is_taggable_at(self, second: int) -> bool:
        if self.final_lives == 0:
            return False
        if self.last_downed_time is not None and second - self.last_downed_time < 4:
            return False
        return True

    def is_resupplyable_at(self, second: int) -> bool:
        return self.is_active_at(second)

    @property
    def tag_id_key(self) -> str:
        """Common tag-identity accessor used by choose_tag_target in mechanics.py."""
        return self.tag_id
