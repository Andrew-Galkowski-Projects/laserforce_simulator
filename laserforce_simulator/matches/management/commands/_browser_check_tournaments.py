"""``seed_browser_check --tournaments``: one sandbox Tournament per format and stage.

Built through the same services the API calls (``create_tournament``,
``lock_tournament``, ``play_next``, the pool and the Draw), so every row is
what the screens read. Matches are played one at a time with ``play_next``:
Play All needs a Celery broker, and the seed runs without one (ANG-06-13b
retro). The leading underscore keeps the module out of the command list.
"""

from django.contrib.auth.base_user import AbstractBaseUser
from django.core.management.base import CommandError
from django.db.models import QuerySet

from matches.models import Match, Tournament
from matches.tournament_services.create import TournamentCreateForm, create_tournament
from matches.tournament_services.draw import (
    PoolGenerateForm,
    generate_pool_players,
    run_draw,
)
from matches.tournament_services.play import play_next
from matches.tournament_services.setup import lock_tournament
from teams.models import Player

# Name -> (create fields, Matches to play after lock; None = to the end,
# and no lock at all for a Tournament left in setup).
_PRESET: tuple[tuple[str, dict, int | None, bool], ...] = (
    # A Bo3 Final, quarterfinals and one semifinal played: Play Next stays live.
    (
        "BC Single Elim",
        {"format": "single_elimination", "generate_count": 8, "final_series_length": 3},
        5,
        True,
    ),
    # Played out: Winners / Losers / Grand Final and a champion.
    (
        "BC Double Elim",
        {"format": "double_elimination", "generate_count": 4},
        None,
        True,
    ),
    # Part of the double round robin: a crosstable with played and open legs.
    ("BC Round Robin", {"format": "round_robin", "generate_count": 4}, 5, True),
    # Still in the seeding stage: Standings with the WB / Out cut column.
    (
        "BC RR to DE",
        {"format": "round_robin_double_elim", "generate_count": 6, "rrde_combo": "4/0"},
        6,
        True,
    ),
    # Round 1 played, so round 2 is built from the Standings.
    ("BC Swiss", {"format": "swiss", "generate_count": 6}, 3, True),
    # Seeding and Lock & Build Bracket.
    ("BC Setup", {"format": "single_elimination", "generate_count": 4}, 0, False),
)
POOL_NAME = "BC Player Pool"
NAMES: tuple[str, ...] = tuple(name for name, *_ in _PRESET) + (POOL_NAME,)

# A Tournament that never ends would loop forever; no seeded field comes close.
_MAX_MATCHES = 200


def _create(user: AbstractBaseUser, name: str, fields: dict) -> Tournament:
    form = TournamentCreateForm({"name": name, **fields}, user=user)
    if not form.is_valid():
        raise CommandError(f"{name}: create form rejected: {form.errors}")
    return create_tournament(form, user=user)


def _play(tournament: Tournament, matches: int | None) -> None:
    """Play ``matches`` ready Matches, or every one when ``None``."""
    limit = _MAX_MATCHES if matches is None else matches
    for _ in range(limit):
        tournament.refresh_from_db()
        if tournament.state != "active" or tournament.find_next_playable_node() is None:
            return
        play_next(tournament)


def seed_browser_check_tournaments(user: AbstractBaseUser) -> list[Tournament]:
    """``user``'s seven Tournaments, in ``NAMES`` order."""
    seeded = []
    for name, fields, matches, lock in _PRESET:
        tournament = _create(user, name, fields)
        if lock:
            lock_tournament(tournament)
            _play(tournament, matches)
        seeded.append(tournament)

    pool = _create(
        user,
        POOL_NAME,
        {"format": "round_robin_double_elim", "team_assembly": "player_pool"},
    )
    generate = PoolGenerateForm({"count": 24})
    if not generate.is_valid():
        raise CommandError(f"Pool generate form rejected: {generate.errors}")
    generate_pool_players(pool, generate)
    run_draw(pool, user=user)
    seeded.append(pool)
    return seeded


def delete_browser_check_tournaments(users: QuerySet) -> int:
    """Delete ``users``' Tournaments with their Matches and generated pool
    Players; return how many Tournaments went.

    A Tournament's Matches hang off its Series (not its manager) and its pool
    Players sit on the global Free Agents Team, so both are collected before
    the Tournaments cascade their nodes, Series and entries.
    """
    tournaments = Tournament.objects.filter(manager__in=users)
    match_ids = list(
        Match.objects.filter(
            series_match__node__tournament__in=tournaments
        ).values_list("id", flat=True)
    )
    pool_player_ids = list(
        Player.objects.filter(
            tournament_entries__tournament__in=tournaments
        ).values_list("id", flat=True)
    )
    count = tournaments.count()
    tournaments.delete()
    Match.objects.filter(id__in=match_ids).delete()
    Player.objects.filter(id__in=pool_player_ids, team__manager__isnull=True).delete()
    return count
