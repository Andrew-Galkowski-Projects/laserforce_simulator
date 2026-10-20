"""ANG-01f — talent seeding shared by Tournament create and participant import."""

from statistics import mean

from teams.models import Team


def team_mean_rating(team: Team) -> float:
    """Mean active-player overall_rating for a Team (LG-01c draft-preview
    formula verbatim). 0.0 when the Team has no active players.
    """
    players = list(team.active_players)
    if not players:
        return 0.0
    return mean(p.overall_rating for p in players)
