"""N+1 guardrail for every ``/api/`` list route on the DRF router.

For each route: seed 2 owned rows, GET the list and count queries; seed 4 more
and count again. The counts must be equal -- a list page runs a constant number
of queries however many rows it renders. Every seeder builds a realistic row
(the relations its list serializer renders), owned by the logged-in Account
through the ``manager`` seam (``accounts/permissions.py``).

Adding a router route means adding one ``SEEDERS`` entry;
``test_every_router_list_route_has_a_seeder`` fails until you do.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from itertools import count
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from conftest import get_shared_manager
from laserforce_simulator.api_urls import router
from matches.models import (
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
    SeasonPhase,
    Tournament,
    TournamentParticipant,
)
from matches.tests.conftest import make_team_with_slots
from matches.tests.league_api_fixtures import confirmed_map
from teams.models import Player, Team

_seq = count()
_tournament_seq = count()


def _owned_team(user, label: str) -> Team:
    """A fully slotted Team (6 Players) owned by ``user``."""
    team, _players = make_team_with_slots(f"{label}-{next(_seq)}")
    team.manager = user
    team.save(update_fields=["manager"])
    return team


def _match_with_rounds(user, *, player_states: bool = False) -> Match:
    """A completed sandbox Match owned by ``user`` with both GameRounds."""
    red = _owned_team(user, "Red")
    blue = _owned_team(user, "Blue")
    match = Match.objects.create(
        team_red=red,
        team_blue=blue,
        manager=user,
        red_round1_points=100,
        blue_round1_points=50,
        red_round2_points=100,
        blue_round2_points=50,
        winner=red,
        is_completed=True,
    )
    for number, (r, b) in enumerate(((red, blue), (blue, red)), start=1):
        game_round = GameRound.objects.create(
            match=match,
            round_number=number,
            team_red=r,
            team_blue=b,
            red_points=100 if r is red else 50,
            blue_points=50 if r is red else 100,
            winner=red,
            is_completed=True,
        )
        if player_states:
            for team, color in ((r, "red"), (b, "blue")):
                for player in team.players.all():
                    PlayerRoundState.objects.create(
                        game_round=game_round,
                        player=player,
                        team_color=color,
                        points_scored=1000,
                    )
    return match


# --- seeders: each call adds exactly ONE row to its route's list ----------


def seed_team(user, _media: Path) -> None:
    _owned_team(user, "Team")


def seed_player(user, _media: Path) -> None:
    team = Team.objects.create(name=f"Solo-{next(_seq)}", manager=user)
    Player.objects.create(team=team, name=f"Solo player {next(_seq)}")


def seed_match(user, _media: Path) -> None:
    _match_with_rounds(user)


def seed_round(user, _media: Path) -> None:
    # The Match carries two rounds; drop one so each call adds exactly one.
    match = _match_with_rounds(user, player_states=True)
    match.game_rounds.filter(round_number=2).delete()


def seed_map(_user, _media: Path) -> None:
    confirmed_map(f"Arena-{next(_seq)}")


def _league_with_seasons(user) -> tuple[League, Season]:
    """A League owned by ``user`` with a completed Season (champion set) and
    an active Season."""
    champion = _owned_team(user, "Champ")
    league = League.objects.create(
        name=f"League {next(_seq)}", manager=user, current_team=champion
    )
    completed = Season.objects.create(
        league=league,
        name="Season 1",
        start_date=date(2025, 1, 1),
        state="completed",
        champion_team=champion,
    )
    completed.teams.add(champion)
    active = Season.objects.create(
        league=league, name="Season 2", start_date=date(2026, 1, 1), state="active"
    )
    active.teams.add(champion)
    return league, active


def seed_league(user, _media: Path) -> None:
    _league_with_seasons(user)


def seed_season(user, _media: Path) -> None:
    champion = _owned_team(user, "Champ")
    league = League.objects.create(
        name=f"League {next(_seq)}", manager=user, current_team=champion
    )
    season = Season.objects.create(
        league=league,
        name="Season 1",
        start_date=date(2025, 1, 1),
        state="completed",
        champion_team=champion,
    )
    season.teams.add(champion)


def seed_tournament(user, _media: Path) -> None:
    """Alternates a sandbox Tournament (champion + participants) with a
    Season-embedded one, so both ``season`` ref paths are rendered."""
    teams = [_owned_team(user, "Entrant") for _ in range(2)]
    tournament = Tournament.objects.create(
        name=f"Cup {next(_seq)}", manager=user, state="completed", champion=teams[0]
    )
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    if next(_tournament_seq) % 2:
        _league, season = _league_with_seasons(user)
        SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="tournament", tournament=tournament
        )


Seeder = Callable[..., None]

# Route prefix (as registered on ``api_urls.router``) -> seeder.
SEEDERS: dict[str, Seeder] = {
    "teams": seed_team,
    "players": seed_player,
    "matches": seed_match,
    "rounds": seed_round,
    "maps": seed_map,
    "leagues": seed_league,
    "seasons": seed_season,
    "tournaments": seed_tournament,
}

# Routes with a known N+1 today: prefix -> reason. The case is a strict xfail,
# so fixing the N+1 turns it red until the entry is removed.
KNOWN_N_PLUS_ONE: dict[str, str] = {
    "leagues": (
        "N+1: LeagueSerializer.get_active_season reads League.active_season, "
        "one matches_season SELECT per League row"
    ),
}


def _cases() -> list:
    return [
        pytest.param(
            prefix,
            id=prefix,
            marks=(
                [pytest.mark.xfail(strict=True, reason=KNOWN_N_PLUS_ONE[prefix])]
                if prefix in KNOWN_N_PLUS_ONE
                else []
            ),
        )
        for prefix in SEEDERS
    ]


def _list_count(api: APIClient, url: str) -> tuple[int, int, list[str]]:
    """GET ``url``; return (row count from meta, query count, the SQL)."""
    with CaptureQueriesContext(connection) as ctx:
        response = api.get(url)
    assert response.status_code == 200, response.content
    return (
        response.json()["meta"]["count"],
        len(ctx.captured_queries),
        [q["sql"] for q in ctx.captured_queries],
    )


@pytest.mark.django_db
@pytest.mark.parametrize("prefix", _cases())
def test_list_query_count_is_constant(prefix: str, settings, tmp_path: Path) -> None:
    settings.MEDIA_ROOT = tmp_path
    user = get_shared_manager()
    api = APIClient()
    api.force_login(user)
    url = f"/api/{prefix}/"
    seeder = SEEDERS[prefix]

    for _ in range(2):
        seeder(user, tmp_path)
    api.get(url)  # warm-up: one-off per-process lookups are not N+1
    rows_small, queries_small, _sql = _list_count(api, url)

    for _ in range(4):
        seeder(user, tmp_path)
    rows_large, queries_large, sql_large = _list_count(api, url)

    assert rows_large - rows_small == 4, "seeder must add one listed row per call"
    assert queries_large == queries_small, (
        f"/api/{prefix}/ ran {queries_small} queries for {rows_small} rows but "
        f"{queries_large} for {rows_large} rows (N+1). Queries:\n"
        + "\n".join(sql_large)
    )


def test_every_router_list_route_has_a_seeder() -> None:
    registered = {prefix for prefix, _viewset, _basename in router.registry}
    missing = registered - set(SEEDERS)
    assert not missing, (
        f"Router list routes with no N+1 seeder: {sorted(missing)}. "
        "Add a SEEDERS entry in test_list_query_counts.py."
    )
    assert not set(SEEDERS) - registered, "SEEDERS names an unregistered route"
