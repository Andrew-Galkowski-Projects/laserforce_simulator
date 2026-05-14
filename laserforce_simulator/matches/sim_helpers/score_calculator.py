"""
Pure MVP scoring logic extracted from PlayerRoundState.get_mvp.

Accepts a PlayerRoundState instance (or any duck-typed equivalent that exposes
the same attributes) and returns the SM5 MVP float score.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from matches.models import PlayerRoundState


def calculate_mvp(player_state: "PlayerRoundState") -> float:
    """
    Compute the SM5 MVP score for a player at the end of a round.

    All attribute access is duck-typed so this works with both
    PlayerRoundState (ORM) and PlayerState (in-memory dataclass).
    """
    score = 0.0

    # Accuracy bonus — rounded up to nearest 0.5
    accuracy = player_state.get_accuracy
    score += math.ceil(accuracy * 0.1 * 2) / 2

    score += player_state.final_medic_hits * 1
    score += player_state.enemy_nuke_cancels * 3
    score -= player_state.ally_nuke_cancels * 3
    score -= player_state.times_missiled * 1

    if player_state.role != "medic" and player_state.final_lives == 0:
        score -= 1

    gr = player_state.game_round
    team_eliminated_opponent = (
        gr.blue_team_eliminated
        if player_state.team_color == "red"
        else gr.red_team_eliminated
    )
    if team_eliminated_opponent:
        time_remaining = 900 - gr.eliminated_at
        extra_seconds_above_3_min = max(0, time_remaining - 180)
        score += 4 + extra_seconds_above_3_min / 60

    if player_state.role == "commander":
        score += player_state.missiles_landed * 1
        successful_nukes = max(
            0, player_state.specials_used - player_state.own_specials_cancelled
        )
        score += successful_nukes * 1
        score += max(0, player_state.points_scored - 10_000) / 1000
        score -= player_state.own_specials_cancelled * 1

    elif player_state.role == "heavy":
        score += player_state.missiles_landed * 2
        score += max(0, player_state.points_scored - 7_000) / 1000

    elif player_state.role == "scout":
        from matches.models import PlayerRoundState as PRS

        if player_state.team_color == "red":
            cmd_key = str(PRS.tag_id.blue_commander)
            hvy_key = str(PRS.tag_id.blue_heavy)
        else:
            cmd_key = str(PRS.tag_id.red_commander)
            hvy_key = str(PRS.tag_id.red_heavy)
        cmd_hits = player_state.specific_tags.get(cmd_key, {}).get("tags", 0)
        hvy_hits = player_state.specific_tags.get(hvy_key, {}).get("tags", 0)
        score += (cmd_hits + hvy_hits) * 0.2
        score += max(0, player_state.points_scored - 6_000) / 1000

    elif player_state.role == "ammo":
        score += player_state.specials_used * 3
        score += max(0, player_state.points_scored - 3_000) / 1000

    elif player_state.role == "medic":
        score += player_state.specials_used * 3
        if player_state.final_lives > 0:
            score += 2
        score += 2 * max(0, player_state.points_scored - 2_000) / 1000

    return round(score, 2)
