"""ANG-01f — talent seeding shared by Tournament create and participant import."""

from collections.abc import Iterable
from statistics import mean

from teams.models import Team

# The six roster-slot FKs ``Team.active_players`` reads.
_ACTIVE_SLOT_FIELDS: tuple[str, ...] = (
    "slot_commander",
    "slot_heavy",
    "slot_scout_1",
    "slot_scout_2",
    "slot_medic",
    "slot_ammo",
)


def team_mean_rating(team: Team) -> float:
    """Mean active-player overall_rating for a Team (LG-01c draft-preview
    formula verbatim). 0.0 when the Team has no active players.
    """
    players = list(team.active_players)
    if not players:
        return 0.0
    return mean(p.overall_rating for p in players)


def mean_ratings_by_team(team_ids: Iterable[int]) -> dict[int, float]:
    """``{team_id: team_mean_rating(team)}`` for ``team_ids`` in one query (the
    active Players are joined, not fetched per Team)."""
    teams = Team.objects.filter(id__in=list(team_ids)).select_related(
        *_ACTIVE_SLOT_FIELDS
    )
    return {team.id: team_mean_rating(team) for team in teams}
