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
#: [NOT_TARGETABLE_TICKS, RESPAWN_TICKS), per the GLOSSARY.md glossary.
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

# --- Memory staleness (MECH-06) --------------------------------------------

#: Staleness threshold for slow/stationary roles' memory entries
#: (Heavy/Medic/Ammo; was 60 s).
STALENESS_SLOW_TICKS: int = 120

#: Staleness threshold for mobile roles' memory entries
#: (Scout/Commander; was 15 s).
STALENESS_FAST_TICKS: int = 30

#: Staleness thresholds in SECONDS by role of the REMEMBERED player, the default
#: table for unit tests that pass seconds. Stationary support roles stay valid
#: longer; mobile roles go stale quickly.
_STALE_THRESHOLD: dict[str, int] = {
    "heavy": 60,
    "medic": 60,
    "ammo": 60,
    "scout": 15,
    "commander": 15,
}

#: TIME-01: the same table in ticks, for ``time_domain == "ticks"`` (BatchSimulator).
_STALE_THRESHOLD_TICKS: dict[str, int] = {
    "heavy": STALENESS_SLOW_TICKS,
    "medic": STALENESS_SLOW_TICKS,
    "ammo": STALENESS_SLOW_TICKS,
    "scout": STALENESS_FAST_TICKS,
    "commander": STALENESS_FAST_TICKS,
}


def _stale_table(time_domain: str) -> dict[str, int]:
    """Return the staleness threshold table for the caller's time domain.

    MECH-07: lives here so ``modes`` can read it without importing ``pathfinding``.
    """
    return _STALE_THRESHOLD_TICKS if time_domain == "ticks" else _STALE_THRESHOLD


# --- Medic-under-fire alert (MECH-06) -------------------------------------

#: Sliding window for the "Medic hit 2x" alert (was 12 s).
MEDIC_UNDER_FIRE_WINDOW_TICKS: int = 24

# --- Goal recompute throttling (MOVE-04 / ADR-0010) ----------------------

#: Tick cadence for the steady-state ``choose_goal_cell`` recompute (steps
#: 2–4). Reactive overrides (steps 0/1/1b) bypass the throttle and run every
#: tick. BatchSimulator only — RBS path is unchanged. (MOVE-04 / ADR-0010.)
GOAL_RECOMPUTE_PERIOD_TICKS: int = 4

#: MOVE-06: the commitment expiry of a drawn positioning goal: after the round,
#: so it is held until reached (action goals keep the 4-tick cadence).
HELD_GOAL_EXPIRY: int = TICKS_PER_ROUND + 1

# --- Hold (MECH-17) --------------------------------------------------------

#: How long a ``hold`` roll commits the player to Overwatch on a map round: it draws
#: no Action until then, unless the Hold ends early. Tunable by ``round_probe``.
HOLD_TICKS: int = 7

# --- Missile Lock (MECH-21, ADR-0050) --------------------------------------

#: How long a Lock runs before its missile launches (1.0 s): a lock started at
#: tick T launches at the top of tick T + LOCK_TICKS. The only lock length:
#: ``PendingMissileLock``'s default and the ``locking`` row's metadata read it.
LOCK_TICKS: int = 2
