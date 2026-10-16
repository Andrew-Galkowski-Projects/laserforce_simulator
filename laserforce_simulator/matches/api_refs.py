"""ANG-01b — flatten model instances to small reference dicts for API ``data``.

Shared by ``matches.api_views`` and ``teams.api_views``: builder outputs are
passed through verbatim, but any model instance a response carries is reduced
to one of these refs so the envelope never serializes an ORM object.
"""

from __future__ import annotations

from matches.models import GameRound
from teams.models import Player, Team


def team_ref(team: Team) -> dict:
    """``{"id", "name"}`` for a Team."""
    return {"id": team.id, "name": team.name}


def player_ref(player: Player) -> dict:
    """``{"id", "name", "team": team_ref | None}`` for a Player."""
    return {
        "id": player.id,
        "name": player.name,
        "team": team_ref(player.team) if player.team_id is not None else None,
    }


def round_ref(game_round: GameRound) -> dict:
    """``{"id", "round_number", "match_id", "date_played", "team_red", "team_blue"}``."""
    return {
        "id": game_round.id,
        "round_number": game_round.round_number,
        "match_id": game_round.match_id,
        "date_played": game_round.date_played,
        "team_red": team_ref(game_round.team_red),
        "team_blue": team_ref(game_round.team_blue),
    }
