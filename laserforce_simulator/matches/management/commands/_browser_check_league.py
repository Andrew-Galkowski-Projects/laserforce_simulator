"""The ``seed_browser_check --league`` League: hand-built rows, no simulator.

Not a command (the leading underscore keeps Django from listing it). The row
shapes follow ``matches/tests/league_api_fixtures.py`` (``add_win``,
``make_completed_season``, ``add_round_states``), copied here because
production code never imports test modules.
"""

from datetime import date

from django.contrib.auth.models import AbstractBaseUser

from matches.development import STAT_FIELDS
from matches.models import (
    GameRound,
    League,
    Match,
    PlayerRoundState,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
)
from teams.models import Player, Team

LEAGUE_NAME = "Browser Check League"
POOL_NAME = "Browser Check Free Agents"
_POOL_SIZE = 3
# Each older Season's snapshot sits this far below the next, so the ratings
# history chart draws a rising line rather than a flat one.
_RATING_STEP = 2.0

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


def _playoff_season(league: League, teams: list[Team]) -> Season:
    """The active Season at its playoffs: a round-robin phase, hand-played
    (``teams`` best-first, each pairing 2-0 to the better Team), then a
    tournament phase whose seeded bracket the real activation builds.

    The shape of ``test_regional_playoffs._flat_season`` + ``_hand_play_rr``.
    """
    season = Season.objects.create(
        league=league,
        name="Season 3",
        start_date=date(2027, 1, 1),
        schedule_format="single_round_robin",
    )
    season.teams.add(*teams)
    rr_phase = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament", tournament_mode="standings"
    )
    season.start_season()
    rank = {team.id: position for position, team in enumerate(teams)}
    by_id = {team.id: team for team in teams}
    fixtures = next(
        fixtures
        for phase, fixtures in season.scheduled_fixtures_by_phase()
        if phase.pk == rr_phase.pk
    )
    grouped: dict[tuple[frozenset, int], list] = {}
    for fixture in fixtures:
        key = (frozenset({fixture.team_a_id, fixture.team_b_id}), fixture.leg)
        grouped.setdefault(key, []).append(fixture)
    for (_pair, leg), pair_fixtures in grouped.items():
        red = by_id[pair_fixtures[0].team_a_id]
        blue = by_id[pair_fixtures[0].team_b_id]
        red_wins = rank[red.id] < rank[blue.id]
        red_pts, blue_pts = (_WIN_PTS, _LOSE_PTS) if red_wins else (_LOSE_PTS, _WIN_PTS)
        match = Match.objects.create(
            team_red=red,
            team_blue=blue,
            season=season,
            season_phase=rr_phase,
            leg=leg,
            is_completed=True,
            winner=red if red_wins else blue,
            red_round1_points=red_pts,
            blue_round1_points=blue_pts,
            red_round2_points=red_pts,
            blue_round2_points=blue_pts,
        )
        for fixture in pair_fixtures:
            _add_round_states(
                GameRound.objects.create(
                    match=match,
                    round_number=fixture.round_number,
                    team_red=red,
                    team_blue=blue,
                    red_points=red_pts,
                    blue_points=blue_pts,
                    is_completed=True,
                )
            )
    season.refresh_from_db()
    season.activate_pending_tournament_phase()
    return season


def _free_agent_pool(manager: AbstractBaseUser, source: Team) -> Team:
    """A ``manager``-owned pool Team holding copies of ``source``'s first
    Players, renamed "FA <name>" (ANG-06-16 retro: the Free Agents table)."""
    pool = Team.objects.create(name=POOL_NAME, manager=manager)
    for player in Player.objects.filter(team=source).order_by("id")[:_POOL_SIZE]:
        player.pk = None
        player.id = None
        player.name = f"FA {player.name}"
        player.team = pool
        player.save()
    return pool


def _ratings_history(seasons: list[Season], teams: list[Team]) -> None:
    """One ``PlayerSeasonRating`` per Player per Season, Overall rising
    ``_RATING_STEP`` a Season up to the live rating (ANG-06-16 retro: the
    League player ratings-history chart)."""
    rows = []
    for player in Player.objects.filter(team__in=teams):
        for seasons_ago, season in enumerate(reversed(seasons)):
            rows.append(
                PlayerSeasonRating(
                    player=player,
                    season=season,
                    age=None if player.age is None else player.age - seasons_ago,
                    overall_rating=player.overall_rating - seasons_ago * _RATING_STEP,
                    **{name: getattr(player, name) for name in STAT_FIELDS},
                )
            )
    PlayerSeasonRating.objects.bulk_create(rows)


def seed_browser_check_league(
    manager: AbstractBaseUser, teams: list[Team]
) -> tuple[League, list[Season]]:
    """A career, finance-enabled League owned by ``manager`` over ``teams``:
    two completed ranked round-robin Seasons (a different champion each) and
    one active Season at its playoffs (round robin played, bracket built,
    ANG-06-15 retro), a three-Player free-agent pool and a ratings snapshot per
    Player per Season (ANG-06-16 retro). Returns the League and its Seasons,
    oldest first."""
    league = League.objects.create(
        name=LEAGUE_NAME,
        mode="league",
        state="active",
        manager=manager,
        current_team=teams[0],
        finance_enabled=True,
        free_agent_pool=_free_agent_pool(manager, teams[0]),
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
    seasons.append(_playoff_season(league, teams))
    _ratings_history(seasons, teams)
    return league, seasons
