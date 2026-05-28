"""In-memory counter bundle for PlayerState.

Bundles the 18 per-round accumulators so the simulator can mutate them
through one named subobject (locality at writer sites) and
``_flush_to_db`` can persist them with a single ``**asdict(p.counters)``
splat.

This is purely the in-memory shape on PlayerState. PlayerRoundState (the
DB model) keeps the same 18 flat columns; analytics readers continue to
read ``prs.tags_made`` unchanged. See ADR-0018 for why the
counters/events split is deliberate.
"""

from dataclasses import dataclass


@dataclass
class PlayerCounters:
    points_scored: int = 0
    tags_made: int = 0
    shots_missed: int = 0
    times_tagged: int = 0
    specials_used: int = 0
    own_specials_cancelled: int = 0
    enemy_nuke_cancels: int = 0
    ally_nuke_cancels: int = 0
    medic_lives_removed_from_nuke: int = 0
    lives_lost_to_nukes: int = 0
    missiles_landed: int = 0
    times_missiled: int = 0
    resupplies_given: int = 0
    combo_resupply_count: int = 0
    times_tagged_in_reset_window: int = 0
    follow_up_shots: int = 0
    reaction_shots: int = 0
    missile_points: int = 0
