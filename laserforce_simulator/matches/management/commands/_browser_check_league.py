"""The ``seed_browser_check --league`` League: hand-built rows, no simulator.

Not a command (the leading underscore keeps Django from listing it). The row
shapes follow ``matches/tests/league_api_fixtures.py`` (``add_win``,
``make_completed_season``, ``add_round_states``), copied here because
production code never imports test modules.
"""

from datetime import date

from django.contrib.auth.models import AbstractBaseUser

from matches.models import GameRound, League, Match, PlayerRoundState, Season
from teams.models import Team

LEAGUE_NAME = "Browser Check League"

_SLOTS = (
    ("slot_commander", "commander"),
    ("slot_heavy", "heavy"),
    ("slot_scout_1", "scout"),
    ("slot_scout_2", "scout"),
    ("slot_medic", "medic"),
    ("slot_ammo", "ammo"),
)
_WIN_PTS = 42000
_LOSE_PTS = 31000


def _add_round_states(game_round: GameRound) -> None:
    """One ``PlayerRoundState`` per slotted player on both sides, fixed stats
    that differ by slot and by result so the awards have a single leader."""
    rows = []
    for team, color in (
        (game_round.team_red, "red"),
        (game_round.team_blue, "blue"),
    ):
        won = (game_round.red_points > game_round.blue_points) == (color == "red")
        for idx, (slot, role) in enumerate(_SLOTS):
            rows.append(
                PlayerRoundState(
                    game_round=game_round,
                    player=getattr(team, slot),
                    team_color=color,
                    role=role,
                    points_scored=(6000 if won else 4000) + idx * 250,
                    tags_made=(12 if won else 8) + idx,
                    times_tagged=6 if won else 10,
                    shots_missed=22 if won else 28,
                )
            )
    PlayerRoundState.objects.bulk_create(rows)


def _add_win(season: Season, winner: Team, loser: Team) -> Match:
    """A completed 2-0 Match for ``winner`` (sides swap in round 2), with
    player stats on both Rounds."""
    match = Match.objects.create(
        team_red=winner,
        team_blue=loser,
        season=season,
        red_round1_points=_WIN_PTS,
        blue_round1_points=_LOSE_PTS,
        red_round2_points=_WIN_PTS,
        blue_round2_points=_LOSE_PTS,
        is_completed=True,
    )
    for round_number, red, blue, red_pts, blue_pts in (
        (1, winner, loser, _WIN_PTS, _LOSE_PTS),
        (2, loser, winner, _LOSE_PTS, _WIN_PTS),
    ):
        _add_round_states(
            GameRound.objects.create(
                match=match,
                team_red=red,
                team_blue=blue,
                round_number=round_number,
                red_points=red_pts,
                blue_points=blue_pts,
                is_completed=True,
            )
        )
    return match


def _season(
    league: League, teams: list[Team], name: str, start: date, state: str
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start,
        schedule_format="single_round_robin",
        state=state,
        starting_team_ids_json=sorted(t.id for t in teams),
    )
    season.teams.add(*teams)
    return season


def seed_browser_check_league(
    manager: AbstractBaseUser, teams: list[Team]
) -> tuple[League, list[Season]]:
    """A career, finance-enabled League owned by ``manager`` over ``teams``:
    two completed ranked round-robin Seasons (a different champion each) and
    one active Season with one played Match. Returns the League and its
    Seasons, oldest first."""
    league = League.objects.create(
        name=LEAGUE_NAME,
        mode="league",
        state="active",
        manager=manager,
        current_team=teams[0],
        finance_enabled=True,
    )
    seasons = []
    for number, order in ((1, teams), (2, teams[1:] + teams[:1])):
        season = _season(
            league, teams, f"Season {number}", date(2024 + number, 1, 1), "completed"
        )
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                _add_win(season, order[i], order[j])
        season.champion_team = order[0]
        season.save(update_fields=["champion_team"])
        seasons.append(season)
    active = _season(league, teams, "Season 3", date(2027, 1, 1), "active")
    _add_win(active, teams[0], teams[1])
    seasons.append(active)
    return league, seasons
