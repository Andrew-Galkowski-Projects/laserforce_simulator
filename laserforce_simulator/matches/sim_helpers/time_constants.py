"""Single source of truth for every absolute time constant in the simulator.

Pure Python, ZERO imports (like ``role_constants.py``) so it is safe to import
from anywhere — workers, the ORM layer, or the in-memory engine — without a
Django dependency.

The canonical internal time unit is the **tick**: 1 tick = 0.5 real seconds and
a round is 1800 ticks (15 minutes). Every value the code compares, stores, or
returns from the API is in ticks. Seconds exist only as a display-only ``÷2``
derivation applied at HTML templates and the score_averages / game_analysis
CLI — never here, never in game logic.

TIME-01 (see docs/adr/0001-time-unit-seconds-now-tick-native-later.md). The
seconds value each constant was converted from is noted in a comment so the
audit is reviewable in one place.
"""

# --- Tick fundamentals -----------------------------------------------------

#: Real seconds represented by one simulator tick.
TICK_SECONDS: float = 0.5

#: Ticks in a full 15-minute round (900 s / 0.5 s).
TICKS_PER_ROUND: int = 1800

#: "Survived the whole round" sentinel for ``was_eliminated_at`` and the
#: per-round ``*_eliminated_at`` columns. One past the last tick index
#: (was 901 = 900 s + 1 pre-TIME-01).
SURVIVED_SENTINEL: int = 1801

# --- Respawn cooldown (was 8 s total: <4 s not-targetable, 4-7 s reset) ----

#: Total respawn cooldown after a Down before fully active again (was < 8 s).
RESPAWN_TICKS: int = 16

#: Not-targetable window after a Down — cannot be Tagged (was < 4 s). The
#: Reset window (taggable but not active) is the derived span
#: [NOT_TARGETABLE_TICKS, RESPAWN_TICKS), per the CONTEXT.md glossary.
NOT_TARGETABLE_TICKS: int = 8

# --- End-game / base-rush thresholds --------------------------------------

#: Players stop being awarded uncaptured bases on elimination once the round
#: has run this long; i.e. team-elim base bonus only with > 1 min remaining
#: (was second < 840 -> 840 s).
TEAM_ELIM_BONUS_CUTOFF_TICKS: int = 1680

#: End-game base-rush kicks in at/after this tick (was second >= 840 -> 840 s).
ENDGAME_RUSH_TICKS: int = 1680

#: Commander base-capture "early bonus" applies before this tick
#: (was second < 300 -> 300 s, weights.py _COMMANDER base_early_threshold).
COMMANDER_BASE_EARLY_TICKS: int = 600

# --- Score broadcast (MECH-06) --------------------------------------------

#: Score broadcast fires every this many ticks (was every 180 s).
SCORE_BROADCAST_PERIOD_TICKS: int = 360

#: Score-broadcast "seek medic" movement override only when this many ticks
#: still remain to be played, i.e. broadcast occurs at/after this tick
#: (was second >= 360 -> 360 s).
SCORE_BROADCAST_MIN_REMAINING_TICKS: int = 720

# --- Memory staleness (MECH-06, pathfinding._STALE_THRESHOLD) -------------

#: Staleness threshold for slow/stationary roles' memory entries
#: (Heavy/Medic/Ammo; was 60 s).
STALENESS_SLOW_TICKS: int = 120

#: Staleness threshold for mobile roles' memory entries
#: (Scout/Commander; was 15 s).
STALENESS_FAST_TICKS: int = 30

# --- Medic-under-fire alert (MECH-06) -------------------------------------

#: Sliding window for the "Medic hit 2x" alert (was 12 s).
MEDIC_UNDER_FIRE_WINDOW_TICKS: int = 24
