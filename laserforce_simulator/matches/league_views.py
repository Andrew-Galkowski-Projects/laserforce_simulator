"""League / Season views — the LG-01..LG-01f stack.

Extracted from ``matches/views.py`` to keep the league lifecycle (model
list, create flow, dashboards, Play Season POSTs, season standings /
schedule pages, league history) in one file. URL configs in
``matches/league_urls.py`` and ``matches/season_urls.py`` point at the
callables here; URL names are unchanged.
"""

import random
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from math import ceil
from typing import Iterable, Optional

from celery.result import AsyncResult
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Player, Team
from teams.views import _coerce_dir, _generate_free_agents, _generate_teams

from . import development
from .development import STAT_FIELDS
from .forms import CreateLeagueForm
from .models import (
    GameRound,
    League,
    Match,
    PlayerRoundState,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
)
from .season_awards import (
    AwardSet,
    AwardWinner,
    ROLE_KEYS,
    compute_season_awards,
    pick_finals_mvp,
)
from .season_dashboard import (
    LeaderRow,
    compute_leaders,
    find_next_fixture,
    round_progress,
    select_play_fixtures,
)
from .simulation import BatchSimulator
from .standings import StandingsRow, compute_standings
from .tasks import play_playoffs_task, play_season_task
from .views import _celery_state_to_job_status

# Abbreviated column headers for the wide rating tables (Player Ratings,
# Free Agents). Each entry is ``(key, abbr, full)``: ``key`` matches the
# LG-00c ``teams.views._SORT_KEYS_DISPLAY`` key byte-for-byte (so sort links
# + ``*-th-{key}`` DOM ids are unchanged) — only the rendered label is
# shortened, with the full name kept in a ``title`` tooltip. Scoped to the
# league rating screens; the shared LG-00c ``/players/`` table keeps its full
# labels.
RATING_SORT_KEYS_DISPLAY: tuple[tuple[str, str, str], ...] = (
    ("name", "Name", "Name"),
    ("team", "Team", "Team"),
    ("preferred_roles", "Roles", "Preferred Roles"),
    ("overall_rating", "Ovr", "Overall"),
    ("player_awareness", "PlAw", "Player Awareness"),
    ("game_awareness", "GmAw", "Game Awareness"),
    ("resource_awareness", "RsAw", "Resource Awareness"),
    ("decision_making", "Dec", "Decision Making"),
    ("positioning", "Pos", "Positioning"),
    ("stamina", "Sta", "Stamina"),
    ("speed", "Spd", "Speed"),
    ("flexibility", "Flx", "Flexibility"),
    ("adaptability", "Adp", "Adaptability"),
    ("communication", "Comm", "Communication"),
    ("teamwork", "Team", "Teamwork"),
    ("offensive_synergy", "OffSyn", "Offensive Synergy"),
    ("defensive_synergy", "DefSyn", "Defensive Synergy"),
    ("midfield_synergy", "MidSyn", "Midfield Synergy"),
    ("resupply_synergy", "RsupSyn", "Resupply Synergy"),
    ("resupply_efficiency", "RsupEff", "Resupply Efficiency"),
    ("accuracy", "Acc", "Accuracy"),
    ("survival", "Surv", "Survival"),
    ("special_usage", "SpcUse", "Special Usage"),
    ("potential", "Pot", "Potential"),
)

# ====================================================================
# LG-01 — Season views
# ====================================================================


def _compute_team_overall(team: Team) -> float:
    """Mean ``overall_rating`` over a team's six active-roster players.

    Returns ``0.0`` when the team has no slots filled (e.g. the Free
    Agents Team) so the draft-preview sort never trips on an empty
    iterable. Used only by the LG-01 draft-preview ordering on
    ``season_standings``.
    """
    actives = team.active_players
    if not actives:
        return 0.0
    return sum(p.overall_rating for p in actives) / len(actives)


# LG-06g — sortable Standings columns. Every column (the 9 LG-01 columns
# plus the 8 form / side-detail columns) is sortable via the LG-06c
# ``_coerce_sort_key`` / ``_coerce_dir`` pattern; sorting runs view-side on
# the materialised rows and never renumbers ``rank`` (it stays the frozen
# standings position, mirroring the LG-06c League-Leaders precedent).
_STANDINGS_SORT_KEYS: frozenset[str] = frozenset(
    {
        "rank",
        "team",
        "matches_played",
        "wins",
        "losses",
        "ties",
        "league_points",
        "round_wins",
        "total_score",
        "match_streak",
        "match_l5",
        "round_streak",
        "round_l5",
        "red_wlt",
        "blue_wlt",
        "red_points_for",
        "blue_points_for",
    }
)
_STANDINGS_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("rank", "Rank"),
    ("team", "Team"),
    ("matches_played", "MP"),
    ("wins", "W"),
    ("losses", "L"),
    ("ties", "T"),
    ("league_points", "Pts"),
    ("round_wins", "RW"),
    ("total_score", "TS"),
    ("match_streak", "Streak"),
    ("match_l5", "L5"),
    ("round_streak", "R Streak"),
    ("round_l5", "R L5"),
    ("red_wlt", "Red Rec"),
    ("blue_wlt", "Blue Rec"),
    ("red_points_for", "Red PF"),
    ("blue_points_for", "Blue PF"),
)


def _standings_row_attr(row: "StandingsRow | dict", key: str):
    """Attr-or-key access so the LG-06g sort works on BOTH ``StandingsRow``
    dataclasses (active / completed) and the zeroed draft-preview dicts
    (mirrors the existing ``_row_team_id`` attr-or-key precedent)."""
    if hasattr(row, key):
        return getattr(row, key)
    return row[key]


def _streak_sort_value(streak: tuple) -> int:
    """Signed run length for a ``(kind, length)`` streak: ``W`` ⇒ ``+length``,
    ``L`` ⇒ ``-length``, ``T`` / ``""`` ⇒ ``0``."""
    kind, length = streak
    if kind == "W":
        return length
    if kind == "L":
        return -length
    return 0


def _standings_sort_value(row: "StandingsRow | dict", team_name: str, key: str):
    """LG-06g — sort-value extraction over a ``StandingsRow`` (or draft dict).

    Record / L5 columns sort by ``(wins desc, losses asc)`` under one
    direction via a ``(wins, -losses)`` key; streaks by signed run length;
    ``team`` by the team name; everything else by the raw int.
    """
    if key == "team":
        return team_name
    if key in ("match_streak", "round_streak"):
        return _streak_sort_value(_standings_row_attr(row, key))
    if key in ("match_l5", "round_l5", "red_wlt", "blue_wlt"):
        wins, losses, _ties = _standings_row_attr(row, key)
        return (wins, -losses)
    return _standings_row_attr(row, key)


def season_awards(request, season_id: int) -> HttpResponse:
    """LG-03 — Season-end awards page.

    Read-only, GET-only. Recomputes the Season's award set on render (no
    persisted award rows) from the frozen regular-season ``PlayerRoundState``
    corpus plus, for a bracket-format playoff phase, the Finals MVP. Renders
    an empty notice when the Season has no completed regular-season rounds.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    league = season.league
    displayed_season = season
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active=None
    )

    awards = _compute_season_award_set(season)
    has_rounds = PlayerRoundState.objects.filter(
        game_round__match__season=season
    ).exists()

    # ``kd_by_role`` rendered as ordered (role, label, winner) tuples so the
    # template can emit the 5 ``season-awards-kd-{role}`` rows without
    # dict-indexing by a loop variable.
    kd_rows = [
        (role, _KD_ROLE_LABELS[role], awards.kd_by_role.get(role)) for role in ROLE_KEYS
    ]

    context = {
        "season": season,
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
        "awards": awards,
        "kd_rows": kd_rows,
        "has_rounds": has_rounds,
    }
    return render(request, "seasons/awards.html", context)


def season_standings(request, season_id: int) -> HttpResponse:
    """LG-01 — Standings page for a Season.

    Draft preview: when ``season.state == "draft"`` the page lists the
    enrolled teams sorted by computed ``team_overall`` (mean of the 6
    active-roster players' ``overall_rating``, 0.0 when no slots are
    filled), then by team name. Rows are emitted as zeroed
    ``StandingsRow``-shaped dicts so the template renders the same 9
    columns whether or not the Season has started.

    Active / completed: aggregates the Season's completed Matches via
    ``compute_standings``.
    """
    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    is_draft_preview = season.state == "draft"
    rows: list = []
    teams_by_id: dict[int, Team] = {}

    if is_draft_preview:
        teams = list(season.teams.all())
        teams.sort(key=lambda t: (-_compute_team_overall(t), t.name))
        teams_by_id = {t.id: t for t in teams}
        for index, team in enumerate(teams):
            rows.append(
                {
                    "team_id": team.id,
                    "matches_played": 0,
                    "wins": 0,
                    "losses": 0,
                    "ties": 0,
                    "league_points": 0,
                    "round_wins": 0,
                    "total_score": 0,
                    "rank": index + 1,
                    # LG-06g — zeroed form / side-detail cells.
                    "match_streak": ("", 0),
                    "match_l5": (0, 0, 0),
                    "round_streak": ("", 0),
                    "round_l5": (0, 0, 0),
                    "red_wlt": (0, 0, 0),
                    "blue_wlt": (0, 0, 0),
                    "red_points_for": 0,
                    "blue_points_for": 0,
                }
            )
    else:
        completed_qs = Match.objects.filter(season=season, is_completed=True)
        completed_matches: list[dict] = []
        for match in completed_qs:
            completed_matches.append(
                {
                    "match_id": match.id,
                    "team_red_id": match.team_red_id,
                    "team_blue_id": match.team_blue_id,
                    "winner_team_id": match.winner_id,
                    "red_rounds_won": match.red_rounds_won,
                    "blue_rounds_won": match.blue_rounds_won,
                    "red_total_points": match.red_total_points,
                    "blue_total_points": match.blue_total_points,
                    "date_played": match.date_played,
                }
            )

        # LG-06g — every persisted Season Round (incl. Rounds of in-progress
        # Matches) for the Round-grain form + per-physical-side split. The
        # stored ``team_red_id`` / ``team_blue_id`` are the actual physical
        # sides (SIM-08), so red/blue points map straight to each side.
        season_rounds = [
            {
                "round_id": r["id"],
                "team_red_id": r["team_red_id"],
                "team_blue_id": r["team_blue_id"],
                "red_points": r["red_points"],
                "blue_points": r["blue_points"],
                "date_played": r["date_played"],
            }
            for r in GameRound.objects.filter(match__season=season).values(
                "id",
                "team_red_id",
                "team_blue_id",
                "red_points",
                "blue_points",
                "date_played",
            )
        ]

        if season.starting_team_ids_json is not None:
            team_ids = list(season.starting_team_ids_json)
        else:
            team_ids = sorted(t.id for t in season.teams.all())

        enrolled_teams = list(
            Team.objects.filter(id__in=team_ids).values_list("id", "name")
        )
        rows = compute_standings(completed_matches, enrolled_teams, season_rounds)
        teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}

    def _row_team_id(row) -> int:
        if hasattr(row, "team_id"):
            return row.team_id
        return row["team_id"]

    # LG-06g — view-side sort over the materialised rows. ``rank`` is left
    # frozen (we never renumber it); a no-``?sort`` request resolves to
    # ``("rank", "asc")`` so the page renders in standings order unchanged.
    sort = _coerce_sort_key(request.GET.get("sort"), _STANDINGS_SORT_KEYS, "rank")
    direction = _coerce_dir(request.GET.get("dir"))
    name_by_id = {tid: team.name for tid, team in teams_by_id.items()}
    rows.sort(
        key=lambda r: (
            _standings_sort_value(r, name_by_id.get(_row_team_id(r), ""), sort),
            _row_team_id(r),
        ),
        reverse=(direction == "desc"),
    )

    qs_no_sort_dir = request.GET.copy()
    qs_no_sort_dir.pop("sort", None)
    qs_no_sort_dir.pop("dir", None)
    querystring_without_sort_dir = qs_no_sort_dir.urlencode()

    rows_with_teams = [(row, teams_by_id.get(_row_team_id(row))) for row in rows]

    league = season.league
    sidebar_displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, sidebar_displayed_season, "standings"
    )

    context = {
        "season": season,
        "rows": rows,
        "rows_with_teams": rows_with_teams,
        "is_draft_preview": is_draft_preview,
        "sidebar_active": "standings",
        "sidebar_links": sidebar_links,
        "sort": sort,
        "dir": direction,
        "sort_keys": _STANDINGS_SORT_KEYS_DISPLAY,
        "querystring_without_sort_dir": querystring_without_sort_dir,
    }
    return render(request, "seasons/standings.html", context)


def season_schedule(request, season_id: int) -> HttpResponse:
    """LG-01 — Schedule page for a Season.

    Renders the deterministic fixture list from
    ``Season.scheduled_fixtures()`` with persisted ``GameRound``s
    overlaid. Fixtures are grouped by
    matchday; the display date for matchday ``n`` is
    ``season.start_date + (n - 1) * 7 days``.
    """
    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    league = season.league
    sidebar_displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, sidebar_displayed_season, "schedule"
    )

    if season.state == "draft":
        team_ids = sorted(t.id for t in season.teams.all())
    else:
        team_ids = list(season.starting_team_ids_json or [])

    if len(team_ids) < 2:
        # Cannot generate a schedule with fewer than 2 teams — render
        # an empty schedule with the empty-state notice.
        context = {
            "season": season,
            "matchdays": [],
            "sidebar_active": "schedule",
            "sidebar_links": sidebar_links,
        }
        return render(request, "seasons/schedule.html", context)

    # LG-02-Part2a — source fixtures through the Season chokepoint. The
    # ``< 2``-team early empty-render branch above is kept; for >= 2 teams
    # ``scheduled_fixtures()`` reproduces the identical fixture list.
    fixtures = season.scheduled_fixtures()

    teams_by_id: dict[int, Team] = {
        t.id: t for t in Team.objects.filter(id__in=team_ids)
    }

    # Index played GameRounds by (frozenset of team ids, round_number, leg).
    # LG-02-Part2c-3a — leg distinguishes a double_round_robin phase's two legs.
    rounds_qs = GameRound.objects.filter(match__season=season).select_related("match")
    played_by_key: dict[tuple[frozenset[int], int, int], GameRound] = {}
    for game_round in rounds_qs:
        match = game_round.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        key = (
            frozenset({match.team_red_id, match.team_blue_id}),
            game_round.round_number,
            match.leg,
        )
        played_by_key[key] = game_round

    # Build per-fixture dicts.
    per_fixture: list[dict] = []
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        game_round = played_by_key.get(key)
        if game_round is not None:
            played = True
            game_round_id = game_round.id
            red_score = game_round.red_points
            blue_score = game_round.blue_points
        else:
            played = False
            game_round_id = None
            red_score = None
            blue_score = None

        fixture_date = season.start_date + timedelta(days=(fixture.matchday - 1) * 7)
        per_fixture.append(
            {
                "matchday": fixture.matchday,
                "round_number": fixture.round_number,
                "team_a_id": fixture.team_a_id,
                "team_b_id": fixture.team_b_id,
                "team_a": teams_by_id.get(fixture.team_a_id),
                "team_b": teams_by_id.get(fixture.team_b_id),
                "played": played,
                "game_round_id": game_round_id,
                "red_score": red_score,
                "blue_score": blue_score,
                "date": fixture_date,
            }
        )

    # Group by matchday in matchday-asc order.
    matchdays: list[dict] = []
    grouped: dict[int, list[dict]] = defaultdict(list)
    for f in per_fixture:
        grouped[f["matchday"]].append(f)
    for matchday in sorted(grouped.keys()):
        matchday_fixtures = grouped[matchday]
        matchday_date = matchday_fixtures[0]["date"]
        matchdays.append(
            {
                "matchday": matchday,
                "date": matchday_date,
                "fixtures": matchday_fixtures,
            }
        )

    context = {
        "season": season,
        "matchdays": matchdays,
        "sidebar_active": "schedule",
        "sidebar_links": sidebar_links,
    }
    return render(request, "seasons/schedule.html", context)


def league_list(request) -> HttpResponse:
    """LG-01a — flat list of all Leagues (active + archived sections)."""
    active_leagues = list(League.objects.filter(state="active").order_by("-id"))
    archived_leagues = list(League.objects.filter(state="archived").order_by("-id"))
    return render(
        request,
        "leagues/list.html",
        {
            "active_leagues": active_leagues,
            "archived_leagues": archived_leagues,
        },
    )


# ---------------------------------------------------------------------------
# LG-04 — ZenGM player development + per-Season ratings history
# ---------------------------------------------------------------------------


def _developing_players(league: League) -> "list[Player]":
    """LG-04 — the rolling League's snapshot Teams' players (active slots +
    bench) plus the ``league.free_agent_pool`` players. De-duplicated by pk.

    Snapshot Teams come from the just-completed Season's frozen
    ``starting_team_ids_json`` (the same source ``next_season`` uses for the
    team-id carry-forward). ``team.players.all()`` covers both active-slot and
    bench players (``Player.team`` membership includes both). Free agents come
    from ``league.free_agent_pool.players.all()`` when the pool is not None.
    """
    latest_completed = league.seasons.filter(state="completed").order_by("-id").first()
    team_ids: list[int] = []
    if latest_completed is not None:
        team_ids = list(latest_completed.starting_team_ids_json or [])

    seen: set[int] = set()
    players: list[Player] = []

    for team in Team.objects.filter(id__in=team_ids):
        for player in team.players.all():
            if player.pk not in seen:
                seen.add(player.pk)
                players.append(player)

    if league.free_agent_pool is not None:
        for player in league.free_agent_pool.players.all():
            if player.pk not in seen:
                seen.add(player.pk)
                players.append(player)

    return players


def _write_baseline_ratings(season: Season, players: "Iterable[Player]") -> None:
    """LG-04 — write an as-generated PlayerSeasonRating baseline row for each
    founding Player (current stats, current age, current overall_rating).
    No development. Bulk-created in one query.

    LG-05 — also computes each Player's ``potential`` (a scouting estimate at
    founding) via a fresh ``pot_rng`` (one gauss draw per player), sets it on
    the Player, writes it into the baseline rating row, and persists the
    Player.potential mutations in one ``bulk_update``.
    """
    players = list(players)
    pot_rng = random.Random()
    rows = []
    for p in players:
        pot = development.compute_potential(
            {name: getattr(p, name) for name in STAT_FIELDS},
            p.age if p.age is not None else 25,
            pot_rng,
        )
        p.potential = pot
        rows.append(
            PlayerSeasonRating(
                player=p,
                season=season,
                age=p.age,
                overall_rating=p.overall_rating,
                potential=pot,
                **{name: getattr(p, name) for name in STAT_FIELDS},
            )
        )
    PlayerSeasonRating.objects.bulk_create(rows)
    Player.objects.bulk_update(players, ["potential"])


def _develop_league_for_new_season(
    league: League, new_season: Season, latest_completed: Season
) -> None:
    """LG-04 — age + develop every Player in the rolling League's developing
    set, tick total_games, and write one PlayerSeasonRating row tagged to
    new_season. Called inside next_season's atomic block, after carry-forward.

    Builds a fresh ``random.Random()`` (no stored seed). League-isolated; NO
    cross-League guard.
    """
    rng = random.Random()
    # LG-05 — a SECOND, independent Random instance for the potential gauss
    # draw, so LG-04's pinned develop RNG sequence (1 gauss + 19 uniform per
    # player) stays byte-unchanged.
    pot_rng = random.Random()
    players = _developing_players(league)
    if not players:
        return

    # Active-Team player-id set, derived from the already-loaded developing set:
    # a developing player is on an active Team iff its team_id is one of the
    # just-completed Season's snapshot Teams (free-agent-pool players carry the
    # pool team_id, which is never in the snapshot). Avoids a redundant second
    # pass over Team.players.
    active_team_id_set = set(latest_completed.starting_team_ids_json or [])
    active_pks: set[int] = {p.pk for p in players if p.team_id in active_team_id_set}

    # Regular-season appearance counts in the just-completed Season, scoped to
    # latest_completed. Playoff rounds carry match.season = NULL (Part2c-1 #3),
    # so they are naturally excluded — only regular-season appearances count.
    appearances = dict(
        PlayerRoundState.objects.filter(game_round__match__season=latest_completed)
        .values("player_id")
        .annotate(n=Count("id"))
        .values_list("player_id", "n")
    )

    # median_active: the median over the active-Team players of their season
    # appearance count. Degenerate no-active case => 0.
    active_counts = sorted(appearances.get(pk, 0) for pk in active_pks)
    median_active = 0
    if active_counts:
        mid = len(active_counts) // 2
        if len(active_counts) % 2 == 1:
            median_active = active_counts[mid]
        else:
            median_active = (active_counts[mid - 1] + active_counts[mid]) // 2

    rating_rows: list[PlayerSeasonRating] = []
    for player in players:
        # Age coalesce: None -> 25 for the develop math; live age written as +1.
        raw_age = player.age if player.age is not None else 25
        player.age = raw_age + 1

        new_stats = development.develop_player_stats(
            {name: getattr(player, name) for name in STAT_FIELDS},
            player.age,
            rng,
        )
        for name, val in new_stats.items():
            setattr(player, name, val)

        # total_games tick (cosmetic — never a develop input).
        if player.pk in active_pks:
            player.total_games += appearances.get(player.pk, 0)
        else:
            player.total_games += development.free_agent_games_tick(median_active, rng)

        # LG-05 — recompute potential on the POST-development stats + the
        # already-incremented age, with the independent pot_rng.
        pot = development.compute_potential(
            {name: getattr(player, name) for name in STAT_FIELDS},
            player.age,
            pot_rng,
        )
        player.potential = pot

        overall = sum(new_stats.values()) / len(STAT_FIELDS)
        rating_rows.append(
            PlayerSeasonRating(
                player=player,
                season=new_season,
                age=player.age,
                overall_rating=overall,
                potential=pot,
                **new_stats,
            )
        )

    Player.objects.bulk_update(
        players, [*STAT_FIELDS, "age", "total_games", "potential"]
    )
    PlayerSeasonRating.objects.bulk_create(rating_rows)


@transaction.atomic
def league_create(request) -> HttpResponse:
    """LG-01b — Create-League flow.

    GET renders the empty form; POST validates the form, generates
    ``num_teams`` Teams (each with 6 Players) via the LG-00 generator,
    creates the League + draft Season, enrols the new Teams, and
    redirects to the Season standings view.
    """
    if request.method != "POST":
        return render(
            request,
            "leagues/create.html",
            {"form": CreateLeagueForm()},
        )

    form = CreateLeagueForm(request.POST)
    if not form.is_valid():
        return render(request, "leagues/create.html", {"form": form})

    cleaned = form.cleaned_data
    rng = random.Random()
    team_names_pool = list(TEAM_NAMES)
    player_names_pool = list(PLAYER_NAMES)

    created_teams = _generate_teams(
        cleaned["num_teams"],
        6,
        rng=rng,
        mean=cleaned["mean"],
        std_dev=cleaned["std_dev"],
        team_names_pool=team_names_pool,
        player_names_pool=player_names_pool,
    )

    league = League.objects.create(
        name=cleaned["league_name"],
        mode="league",
        state="active",
    )
    # This League's dedicated free-agent pool Team. Hidden from
    # ``Team.objects.regular()`` via the ``free_agent_pool`` FK, so it
    # never appears in competitive team lists.
    pool_team = Team.objects.create(name=f"{cleaned['league_name']} Free Agents")
    league.free_agent_pool = pool_team
    # LG-01g: auto-set the manager's current_team to the alphabetically-first
    # generated Team so the TEAM > Schedule sidebar entry has a default
    # target on the next render.
    league.current_team = sorted(created_teams, key=lambda t: t.name)[0]
    league.save(update_fields=["current_team", "free_agent_pool"])

    # Seed a pool of 100–200 free agents (Players on no competitive
    # roster) into THIS League's pool so its Free Agents screen is
    # populated from the start.
    _generate_free_agents(
        rng.randint(100, 200),
        rng=rng,
        mean=cleaned["mean"],
        std_dev=cleaned["std_dev"],
        player_names_pool=player_names_pool,
        team=pool_team,
    )
    season = Season.objects.create(
        league=league,
        name=cleaned["season_name"],
        start_date=cleaned["start_date"],
        state="draft",
        schedule_format=cleaned["schedule_format"],
        # LG-01j — persist the picked map_mode at create-League time.
        map_mode=cleaned["map_mode"],
    )
    season.teams.add(*created_teams)
    # LG-01j — materialise the M2M map_pool rows in the same atomic
    # block. ``cleaned["map_pool"]`` is the ModelMultipleChoiceField's
    # QuerySet; ``.set()`` accepts an iterable of objects or PKs.
    season.map_pool.set(cleaned["map_pool"])
    # LG-02-Part2b — create the composed phases inside the same atomic
    # block (a rollback drops them with the Season). ``phase_specs`` is the
    # parsed composer output stashed by ``CreateLeagueForm.clean()``; the
    # ``tournament`` FK is ALWAYS NULL in Part2b.
    for spec in form.cleaned_data["phase_specs"]:
        SeasonPhase.objects.create(
            season=season,
            ordinal=spec.ordinal,
            phase_type=spec.phase_type,
            schedule_format=spec.schedule_format,
            tournament=None,
            # LG-02-Part2c-3c — the composer writes the tournament_mode
            # (standings / strength / unseeded) into the spec.
            tournament_mode=spec.tournament_mode,
            # LG-02-Part2c-3d — top-N participant cut (0 = no cut).
            tournament_cut=spec.tournament_cut,
            # LG-02-Part2c-3e — per-format sub-config (now-live tournament_format
            # plus the 7 sub-config fields).
            tournament_format=spec.tournament_format,
            final_series_length=spec.final_series_length,
            semifinal_series_length=spec.semifinal_series_length,
            quarterfinal_series_length=spec.quarterfinal_series_length,
            earlier_series_length=spec.earlier_series_length,
            wb_advancers=spec.wb_advancers,
            lb_advancers=spec.lb_advancers,
            swiss_rounds=spec.swiss_rounds,
        )

    # LG-04 — write an as-generated PlayerSeasonRating baseline row for every
    # founding Player (competitive-Team active + bench players plus the
    # free-agent pool). No development at baseline. At create time there is no
    # completed Season yet, so source the founding set directly from the just-
    # created Teams + the pool (the snapshot _developing_players relies on does
    # not exist until a Season completes).
    founding_players: list[Player] = []
    seen_founding: set[int] = set()
    for team in created_teams:
        for p in team.players.all():
            if p.pk not in seen_founding:
                seen_founding.add(p.pk)
                founding_players.append(p)
    for p in pool_team.players.all():
        if p.pk not in seen_founding:
            seen_founding.add(p.pk)
            founding_players.append(p)
    _write_baseline_ratings(season, founding_players)

    return redirect("season_standings", season_id=season.id)


# ---------------------------------------------------------------------------
# LG-01c — League / Season dashboard
# ---------------------------------------------------------------------------


def _build_dashboard_context(
    displayed_season: Optional[Season], season_mode: str
) -> dict:
    """Shared body context for the League and Season dashboards.

    Returns the 11-key body context dict described by the LG-01c seam
    contract. Branches on ``season_mode`` to materialise the standings
    snippet, next-fixture dict, round count, leader snippets, and the
    placeholder action-button label / state.
    """
    standings_snippet: list = []
    next_fixture: Optional[dict] = None
    round_count_completed = 0
    round_count_total = 0
    leaders_points: list[LeaderRow] = []
    leaders_tags: list[LeaderRow] = []
    leaders_ratio: list[LeaderRow] = []

    if season_mode == "none":
        action_button_label = "No Season"
        action_button_state = "none"
    elif season_mode == "draft":
        action_button_label = "Start Season"
        action_button_state = "start_season"

        # Zero-filled top-3 standings preview: teams sorted by name asc.
        teams = sorted(displayed_season.teams.all(), key=lambda t: t.name)
        top_teams = teams[:3]
        for index, team in enumerate(top_teams):
            row = {
                "team_id": team.id,
                "matches_played": 0,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "league_points": 0,
                "round_wins": 0,
                "total_score": 0,
                "rank": index + 1,
            }
            standings_snippet.append((row, team))
    else:
        # season_mode in {"active", "completed"}.
        if season_mode == "active":
            action_button_label = "Play Next"
            action_button_state = "play_next"
        else:
            action_button_label = "Start Next Season"
            action_button_state = "start_next_season"

        # --- Standings snippet (real compute_standings, top 3) --------
        completed_qs = Match.objects.filter(season=displayed_season, is_completed=True)
        completed_matches: list[dict] = []
        for match in completed_qs:
            completed_matches.append(
                {
                    "match_id": match.id,
                    "team_red_id": match.team_red_id,
                    "team_blue_id": match.team_blue_id,
                    "winner_team_id": match.winner_id,
                    "red_rounds_won": match.red_rounds_won,
                    "blue_rounds_won": match.blue_rounds_won,
                    "red_total_points": match.red_total_points,
                    "blue_total_points": match.blue_total_points,
                }
            )

        if displayed_season.starting_team_ids_json is not None:
            team_ids = list(displayed_season.starting_team_ids_json)
        else:
            team_ids = sorted(t.id for t in displayed_season.teams.all())

        enrolled_teams = list(
            Team.objects.filter(id__in=team_ids).values_list("id", "name")
        )
        rows = compute_standings(completed_matches, enrolled_teams)
        teams_by_id = Team.objects.in_bulk(team_ids)
        top_rows = rows[:3]
        snippet_rows: list = []
        for row in top_rows:
            row_dict = {
                "team_id": row.team_id,
                "matches_played": row.matches_played,
                "wins": row.wins,
                "losses": row.losses,
                "ties": row.ties,
                "league_points": row.league_points,
                "round_wins": row.round_wins,
                "total_score": row.total_score,
                "rank": row.rank,
            }
            snippet_rows.append((row_dict, teams_by_id.get(row.team_id)))
        standings_snippet = snippet_rows

        # --- Schedule + next fixture + round progress -----------------
        # LG-02-Part2a — route through the Season chokepoint (returns []
        # for < 2 teams, matching the prior guard).
        fixtures = displayed_season.scheduled_fixtures()

        rounds_qs = GameRound.objects.filter(
            match__season=displayed_season
        ).select_related("match")
        # LG-02-Part2c-3a — played_keys gain ``leg`` so a double_round_robin
        # phase's two legs are distinct.
        played_keys: set = set()
        for game_round in rounds_qs:
            match = game_round.match
            if match is None or match.team_red_id is None or match.team_blue_id is None:
                continue
            played_keys.add(
                (
                    frozenset({match.team_red_id, match.team_blue_id}),
                    game_round.round_number,
                    match.leg,
                )
            )

        round_count_completed, round_count_total = round_progress(fixtures, played_keys)

        fixture = find_next_fixture(fixtures, played_keys)
        if fixture is not None:
            fixture_teams = Team.objects.in_bulk([fixture.team_a_id, fixture.team_b_id])
            team_a = fixture_teams.get(fixture.team_a_id)
            team_b = fixture_teams.get(fixture.team_b_id)
            fixture_date = displayed_season.start_date + timedelta(
                days=(fixture.matchday - 1) * 7
            )
            next_fixture = {
                "matchday": fixture.matchday,
                "round_number": fixture.round_number,
                "team_a_id": fixture.team_a_id,
                "team_a_name": team_a.name if team_a is not None else "",
                "team_b_id": fixture.team_b_id,
                "team_b_name": team_b.name if team_b is not None else "",
                "date": fixture_date,
            }

        # --- Leaders snippets ----------------------------------------
        prs_qs = (
            PlayerRoundState.objects.filter(game_round__match__season=displayed_season)
            .select_related(
                "player",
                "game_round__match",
                "game_round__team_red",
                "game_round__team_blue",
            )
            .order_by("id")
        )
        player_rounds: list[dict] = []
        for prs in prs_qs:
            game_round = prs.game_round
            if prs.team_color == "red":
                team = game_round.team_red
            elif prs.team_color == "blue":
                team = game_round.team_blue
            else:
                team = None
            player_rounds.append(
                {
                    "player_id": prs.player_id,
                    "player_name": prs.player.name,
                    "role": prs.role,
                    "team_id": team.id if team is not None else 0,
                    "team_name": team.name if team is not None else "",
                    "tags_made": prs.tags_made,
                    "times_tagged": prs.times_tagged,
                    "points_scored": prs.points_scored,
                }
            )

        leaders_points = compute_leaders(player_rounds, "points_per_game", limit=3)
        leaders_tags = compute_leaders(player_rounds, "tags_per_game", limit=3)
        leaders_ratio = compute_leaders(player_rounds, "tag_ratio", limit=3)

    # LG-01j — read-only map-config label for the dashboard "Map: ..."
    # line. 4 cases in pinned precedence order. Active / completed
    # Seasons read from the FROZEN SNAPSHOT; draft Seasons read the
    # live M2M (the snapshot is None pre-activation).
    map_config_label = _build_map_config_label(displayed_season, season_mode)

    # LG-02-Part2c-1 — playoff-cursor keys, derived from the displayed
    # Season's phase cursor.
    (
        playoff_phase_active,
        playoff_tournament_id,
        playoff_completed,
        has_following_tournament_phase,
        following_tournament_is_final,
    ) = _playoff_cursor_keys(displayed_season)

    return {
        "displayed_season": displayed_season,
        "season_mode": season_mode,
        "standings_snippet": standings_snippet,
        "next_fixture": next_fixture,
        "round_count_completed": round_count_completed,
        "round_count_total": round_count_total,
        "leaders_points": leaders_points,
        "leaders_tags": leaders_tags,
        "leaders_ratio": leaders_ratio,
        "action_button_label": action_button_label,
        "action_button_state": action_button_state,
        # LG-01j — 12th key.
        "map_config_label": map_config_label,
        # LG-02-Part2c-1 — playoff-cursor keys.
        "playoff_phase_active": playoff_phase_active,
        "playoff_tournament_id": playoff_tournament_id,
        "playoff_completed": playoff_completed,
        "has_following_tournament_phase": has_following_tournament_phase,
        # LG-02-Part2c-3c — terminal-label split.
        "following_tournament_is_final": following_tournament_is_final,
    }


def _playoff_cursor_keys(
    displayed_season: Optional[Season],
) -> tuple[bool, Optional[int], bool, bool, bool]:
    """LG-02-Part2c-1 — derive the dashboard playoff-cursor keys.

    Returns ``(playoff_phase_active, playoff_tournament_id,
    playoff_completed, has_following_tournament_phase,
    following_tournament_is_final)``:

    * ``playoff_phase_active`` — ``current_phase()`` is a built + active
      tournament phase (``tournament_id is not None`` AND
      ``tournament.state == "active"``).
    * ``playoff_tournament_id`` — the tournament id of a built tournament
      phase (active OR completed), else ``None``. When ``current_phase()``
      is ``None`` (Season finished), inspect the final phase.
    * ``playoff_completed`` — a built tournament phase exists with
      ``tournament.state == "completed"``.
    * ``has_following_tournament_phase`` — the phase list contains a
      ``tournament`` phase at an ordinal AFTER the current phase.
    * ``following_tournament_is_final`` — LG-02-Part2c-3c terminal-label
      split: the next tournament phase (at an ordinal > the current phase's)
      is the FINAL phase (its ordinal == the last phase's ordinal). Drives the
      "Until Playoffs" (final) vs "Until Tournament" (mid-season) relabel.
    """
    if displayed_season is None:
        return (False, None, False, False, False)

    phases = displayed_season.ordered_phases()
    current = displayed_season.current_phase()

    playoff_phase_active = False
    playoff_tournament_id: Optional[int] = None
    playoff_completed = False
    has_following_tournament_phase = False
    following_tournament_is_final = False

    if current is not None:
        following_tournament_ordinals = [
            phase.ordinal
            for phase in phases
            if phase.phase_type == "tournament" and phase.ordinal > current.ordinal
        ]
        has_following_tournament_phase = bool(following_tournament_ordinals)
        if has_following_tournament_phase:
            last_ordinal = phases[-1].ordinal
            following_tournament_is_final = (
                min(following_tournament_ordinals) == last_ordinal
            )
        if current.phase_type == "tournament" and current.tournament_id is not None:
            playoff_tournament_id = current.tournament_id
            if current.tournament.state == "active":
                playoff_phase_active = True
            elif current.tournament.state == "completed":
                playoff_completed = True
    else:
        # Season finished — inspect the final phase for a completed
        # tournament (the tournament-completed sub-state).
        final_phase = phases[-1]
        if (
            final_phase.phase_type == "tournament"
            and final_phase.tournament_id is not None
        ):
            playoff_tournament_id = final_phase.tournament_id
            if final_phase.tournament.state == "completed":
                playoff_completed = True

    return (
        playoff_phase_active,
        playoff_tournament_id,
        playoff_completed,
        has_following_tournament_phase,
        following_tournament_is_final,
    )


def _build_map_config_label(
    displayed_season: Optional[Season], season_mode: str
) -> str:
    """LG-01j — render the per-Season Map: <...> dashboard label.

    Locked precedence (the 4-case ladder in pinned order):

        1. ``displayed_season is None`` OR ``season_mode == "none"``
           (LG-01c ``season_mode`` — distinct from ``Season.map_mode``
           — the "no Season picked" case) ⇒
           ``"Map: 3-zone fallback (no map)"``.
        2. ``displayed_season.map_mode == "none"`` ⇒
           ``"Map: 3-zone fallback (no map)"``.
        3. ``displayed_season.map_mode == "single"`` ⇒ resolve the lone
           map and render ``f"Map: Single — {name}"`` (em-dash U+2014)
           or ``"Map: Single — (map deleted)"`` when the map row was
           deleted between activation and render.
        4. ``displayed_season.map_mode == "random_per_round"`` ⇒
           ``"Map: Random per Round ({n} maps: {names})"`` (names
           alphabetical asc) or ``"Map: Random per Round (no maps)"``
           when the pool is empty / all entries deleted.

    For active / completed Seasons the pool ids come from the FROZEN
    SNAPSHOT (``starting_map_pool_ids_json``); for draft Seasons the
    snapshot is ``None`` so the live M2M is read instead.
    """
    if displayed_season is None or season_mode == "none":
        return "Map: 3-zone fallback (no map)"

    from core.models import ArenaMap

    mode = displayed_season.map_mode
    if mode == "none":
        return "Map: 3-zone fallback (no map)"

    # Resolve pool ids — snapshot for active/completed, live M2M for draft.
    if season_mode in ("active", "completed"):
        pool_ids = displayed_season.starting_map_pool_ids_json or []
    else:
        pool_ids = list(displayed_season.map_pool.values_list("id", flat=True))

    if mode == "single":
        if not pool_ids:
            return "Map: Single — (map deleted)"
        map_obj = ArenaMap.objects.filter(id=pool_ids[0]).first()
        if map_obj is None:
            return "Map: Single — (map deleted)"
        return f"Map: Single — {map_obj.name}"

    if mode == "random_per_round":
        names = list(
            ArenaMap.objects.filter(id__in=pool_ids)
            .order_by("name")
            .values_list("name", flat=True)
        )
        if not names:
            return "Map: Random per Round (no maps)"
        return f"Map: Random per Round ({len(names)} maps: {', '.join(names)})"

    # Defensive fallback — an unknown enum value (admin-side raw write)
    # surfaces as the 3-zone label rather than crashing the dashboard.
    return "Map: 3-zone fallback (no map)"


# ---------------------------------------------------------------------------
# LG-01f — League history + sidebar helpers
# ---------------------------------------------------------------------------


_LG01F_PER_PAGE_OPTIONS: tuple[int, ...] = (10, 25, 50, 100)


def _pick_displayed_season(league: League) -> Season | None:
    """LG-01f — the Season the sidebar's live LEAGUE entries target.

    Active Season takes precedence; fallback to the most-recent completed
    Season; ``None`` when the League has zero Seasons. Single-sourced so
    the 5 League-context views and the league-history view agree on
    which Season the sidebar's Standings / Schedule links resolve to.
    """
    active = league.active_season
    if active is not None:
        return active
    return league.seasons.filter(state="completed").order_by("-id").first()


def _coerce_per_page(raw: str | None, default: int = 10) -> int:
    """LG-01f — coerce ``?per_page=`` to one of the whitelisted values.

    Whitelist is ``(10, 25, 50, 100)``. Any other value (``None``,
    non-digit strings, negative, zero, > 100, not in the whitelist)
    ⇒ return ``default``.
    """
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value in _LG01F_PER_PAGE_OPTIONS:
        return value
    return default


def _coerce_page(raw: str | None, default: int = 1) -> int:
    """LG-01f — coerce ``?page=`` to a positive int.

    Non-digit / non-positive / missing ⇒ ``default``. Django's
    ``Paginator.get_page(...)`` further clamps a too-large value to the
    last page silently.
    """
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return value


def _coerce_sort_key(raw: str | None, allowed: frozenset[str], default: str) -> str:
    """LG-06c — coerce ``?<…>sort=`` to a whitelisted sort key, else default.

    Returns ``raw`` iff ``raw`` is in ``allowed``; otherwise ``default``.
    ``None`` / empty / unknown all map to ``default``. Mirrors the forgiving
    ``_coerce_per_page`` / ``_coerce_team_id`` precedent in this file. The
    single source of sort-key coercion for all five LG-06c screens.
    """
    if raw is not None and raw in allowed:
        return raw
    return default


def _coerce_season(
    raw: str | None, valid_season_ids: set[int], default: int | None
) -> int | str | None:
    """LG-06d — coerce ``?season=`` to a Season id, the ``"career"`` sentinel, or default.

    Returns the literal string ``"career"`` iff ``raw == "career"``; else the
    int Season id iff ``raw`` parses as an int AND is present in
    ``valid_season_ids`` (a ``set[int]`` of this League's Season ids); else
    ``default`` (the ``displayed_season`` id, or ``None`` when the League has
    no Season). Mirrors the forgiving ``_coerce_team_id`` / ``_coerce_per_page``
    precedent — ``None`` / empty / malformed / non-belonging values all fall
    back to ``default`` (the current displayed-Season scope, fully
    backward-compatible). The single source of ``?season=`` coercion for all
    six LG-06d screens.
    """
    if raw == "career":
        return "career"
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value in valid_season_ids:
        return value
    return default


def _coerce_rate(raw: str | None, default: str = "total") -> str:
    """LG-06d — coerce ``?rate=`` to a whitelisted rate mode, else default.

    Accepted: ``"total"`` / ``"per_game"`` / ``"per_10"``. Anything else
    (``None`` / empty / unknown) ⇒ ``default`` (``"total"``). Mirrors the
    forgiving ``_coerce_per_page`` / ``_coerce_team_id`` precedent. Player
    Stats is the only screen that carries a rate toggle.
    """
    if raw in ("total", "per_game", "per_10"):
        return raw
    return default


def _season_param(selected_season: int | str | None) -> str:
    """LG-06d — serialise a coerced ``selected_season`` to a querystring value.

    ``"career"`` → ``"career"``; an int Season id → its ``str``; ``None``
    (empty-state) → ``""`` (the param is dropped). Used by the six screens'
    querystring-carry helpers + hidden form inputs so changing another control
    preserves the chosen Season scope.
    """
    if selected_season is None:
        return ""
    return str(selected_season)


def _resolve_season_scope(
    request: HttpRequest, league: League, displayed_season: Season | None
) -> tuple[list[Season], int | str | None, list[dict], dict | None]:
    """LG-06d — resolve the ``?season=`` selector into a scope for a screen.

    Single source for the six LG-06d season-selector screens. Builds the
    Season picker options (newest-first by ``start_date`` then ``id``),
    coerces ``?season=`` via :func:`_coerce_season` defaulting to
    ``displayed_season`` (fully backward-compatible), and derives the
    round/match queryset filter for the chosen scope.

    Returns a 4-tuple ``(seasons, selected_season, season_options, season_filter)``:

    - ``seasons`` — ``list[Season]`` newest-first (the materialised picker
      source; reused by the view to resolve a picked Season id without an
      extra query).
    - ``selected_season`` — ``"career"`` | ``int`` Season id | ``None`` (the
      empty-state, when the League has no Season).
    - ``season_options`` — ``list[dict]`` of ``{"id", "name", "year"}``
      newest-first; the template appends the "Career" entry.
    - ``season_filter`` — the ORM lookup dict to apply to a round / match
      queryset (joined via ``match__season…``): ``{"match__season__league":
      league}`` for Career, ``{"match__season": <Season>}`` for a single
      Season, or ``None`` in the empty-state.
    """
    seasons = list(league.seasons.order_by("-start_date", "-id"))
    valid_ids = {s.id for s in seasons}
    default_id = displayed_season.id if displayed_season is not None else None
    selected_season = _coerce_season(request.GET.get("season"), valid_ids, default_id)

    season_options = [
        {
            "id": s.id,
            "name": s.name,
            "year": s.start_date.year if s.start_date is not None else None,
        }
        for s in seasons
    ]

    if selected_season == "career":
        season_filter: dict | None = {"match__season__league": league}
    elif selected_season is None:
        season_filter = None
    else:
        season_obj = next((s for s in seasons if s.id == selected_season), None)
        season_filter = (
            {"match__season": season_obj} if season_obj is not None else None
        )

    return seasons, selected_season, season_options, season_filter


def _watched_player_ids(request: HttpRequest, league_id: int) -> set[int]:
    """LG-06f — the per-League watched-player id set for this browser session.

    Single source: reads ``request.session["watch_lists"].get(str(league_id),
    [])``, coerces each entry to int (silently dropping non-ints), returns a
    ``set[int]``. Consumed by BOTH ``core.context_processors.watch_list`` AND
    ``matches.league_screens.watch_list.watch_list``. Never raises; a missing
    key ⇒ ``set()``.

    Coercion rule: an entry already ``int`` passes; a ``str`` that
    ``int()``-parses passes; anything else (``None``, non-numeric str, float,
    dict) is dropped. De-dup is implicit via ``set``.
    """
    session = getattr(request, "session", None)
    if session is None:
        return set()
    lists = session.get("watch_lists", {})
    if not isinstance(lists, dict):
        return set()
    raw_ids = lists.get(str(league_id), [])
    if not isinstance(raw_ids, (list, tuple)):
        return set()
    out: set[int] = set()
    for entry in raw_ids:
        if isinstance(entry, bool):
            # bool is an int subclass — exclude defensively (a stray True/False
            # is not a real player id).
            continue
        if isinstance(entry, int):
            out.add(entry)
            continue
        if isinstance(entry, str):
            try:
                out.add(int(entry))
            except (TypeError, ValueError):
                continue
    return out


def _coerce_team_id(raw: str | None, enrolled_ids: set[int]) -> int | None:
    """LG-06b — coerce ``?team_id=`` to an enrolled Team id, else ``None``.

    Returns the int id iff ``raw`` parses as an int AND is present in
    ``enrolled_ids``. ``None`` / empty / malformed / non-enrolled values
    all map to ``None`` ("All Teams", no filter). Mirrors the forgiving
    ``_coerce_per_page`` / ``_coerce_page`` precedent.
    """
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value in enrolled_ids:
        return value
    return None


def _resolve_current_team_for_sidebar(
    league: League,
    displayed_season: Season | None,
) -> Team | None:
    """LG-01g — pick the Team the TEAM > Schedule sidebar entry targets.

    Resolution chain:
        (a) ``league.current_team`` if that Team is enrolled in
            ``displayed_season`` (defensive — admin may have removed
            the Team from the Season's M2M between the auto-set and
            this render).
        (b) The alphabetically-first Team in ``displayed_season.teams``.
        (c) ``None`` — no Team in Season; the sidebar entry stays
            disabled.

    Returns ``None`` immediately when ``displayed_season is None`` so
    the league dashboard's no-Season fallback keeps the entry disabled.
    """
    if displayed_season is None:
        return None
    # Read current_team_id (the FK column) first to avoid an extra
    # SELECT when the row isn't cached.
    current_team_id = league.current_team_id
    if current_team_id is not None:
        in_season_ids = set(displayed_season.teams.values_list("id", flat=True))
        if current_team_id in in_season_ids:
            return league.current_team
    return displayed_season.teams.order_by("name").first()


def _build_league_sidebar_links(
    league: League,
    displayed_season: Season | None,
    sidebar_active: str | None,
) -> list[dict]:
    """LG-01f / LG-01h — build the 23-entry League sidebar list.

    Order (locked, LG-01h): 1 top + 6 LEAGUE + 4 TEAM + 6 PLAYERS + 6 STATS.

    Live entries:
        * top.dashboard ⇒ ``league_dashboard``.
        * league.standings / league.schedule ⇒ the displayed Season's
          standings / schedule when ``displayed_season is not None``,
          else disabled.
        * league.playoffs / league.finances / league.power_rankings ⇒
          their ``coming_soon_*`` placeholder routes (LG-01h).
        * league.history ⇒ ``league_history``.
        * team.roster / team.finances_team / team.history_team ⇒
          their ``coming_soon_team_*`` placeholder routes (LG-01h).
        * team.schedule_team (LG-01g) ⇒ ``team_schedule`` for the
          Team picked by ``_resolve_current_team_for_sidebar``; falls
          back to disabled when no Team is resolvable.
        * players.* and stats.* — all 12 placeholder ``coming_soon_*``
          routes (LG-01h).
    """
    if displayed_season is not None:
        standings_url: str | None = reverse(
            "season_standings", args=[displayed_season.id]
        )
        schedule_url: str | None = reverse(
            "season_schedule", args=[displayed_season.id]
        )
    else:
        standings_url = None
        schedule_url = None

    picked = _resolve_current_team_for_sidebar(league, displayed_season)
    if picked is None:
        schedule_team_url: str | None = None
    else:
        schedule_team_url = reverse(
            "team_schedule",
            kwargs={"league_id": league.id, "team_id": picked.id},
        )

    # LG-01h placeholder URLs — every entry needs the League id.
    def _cs(name: str) -> str:
        return reverse(name, kwargs={"league_id": league.id})

    raw_entries: list[tuple[str, str, str, str | None]] = [
        # top (1)
        (
            "top",
            "dashboard",
            "Dashboard",
            reverse("league_dashboard", args=[league.id]),
        ),
        # LEAGUE (6)
        ("league", "standings", "Standings", standings_url),
        ("league", "schedule", "Schedule", schedule_url),
        ("league", "playoffs", "Playoffs", _cs("league_playoffs")),
        ("league", "finances", "Finances", _cs("coming_soon_finances")),
        ("league", "history", "History", reverse("league_history", args=[league.id])),
        (
            "league",
            "power_rankings",
            "Power Rankings",
            _cs("league_power_rankings"),
        ),
        # TEAM (4)
        ("team", "roster", "Roster", _cs("team_roster")),
        ("team", "schedule_team", "Schedule", schedule_team_url),
        ("team", "finances_team", "Finances", _cs("coming_soon_team_finances")),
        ("team", "history_team", "History", _cs("team_history")),
        # PLAYERS (6)
        ("players", "free_agents", "Free Agents", _cs("players_free_agents")),
        ("players", "trade", "Trade", _cs("coming_soon_trade")),
        (
            "players",
            "trading_block",
            "Trading Block",
            _cs("coming_soon_trading_block"),
        ),
        ("players", "prospects", "Prospects", _cs("coming_soon_prospects")),
        ("players", "watch_list", "Watch List", _cs("players_watch_list")),
        (
            "players",
            "hall_of_fame",
            "Hall of Fame",
            _cs("coming_soon_hall_of_fame"),
        ),
        # STATS (6) — LG-01h, entire section NEW
        ("stats", "game_log", "Game Log", _cs("stats_game_log")),
        (
            "stats",
            "league_leaders",
            "League Leaders",
            _cs("stats_league_leaders"),
        ),
        (
            "stats",
            "player_ratings",
            "Player Ratings",
            _cs("stats_player_ratings"),
        ),
        ("stats", "player_stats", "Player Stats", _cs("stats_player_stats")),
        ("stats", "team_stats", "Team Stats", _cs("stats_team_stats")),
        (
            "stats",
            "statistical_feats",
            "Statistical Feats",
            _cs("stats_statistical_feats"),
        ),
    ]

    out: list[dict] = []
    for section, key, label, url in raw_entries:
        out.append(
            {
                "key": key,
                "label": label,
                "section": section,
                "url": url,
                "disabled": url is None,
                "active": key == sidebar_active,
            }
        )
    return out


# ---------------------------------------------------------------------------
# LG-03 — Season-end awards (shared aggregation helpers)
# ---------------------------------------------------------------------------

# The tournament formats whose champion is crowned through a terminal Bracket
# node — only these source a Finals MVP. ``round_robin`` / ``swiss`` have no
# single deciding Match, so they (and no-playoff Seasons) yield ``None``.
_BRACKET_FINALS_FORMATS: frozenset[str] = frozenset(
    {"single_elimination", "double_elimination", "round_robin_double_elim"}
)


def _award_round_dict(prs: PlayerRoundState) -> dict:
    """Build one LG-03 seam dict from a ``PlayerRoundState`` row.

    Mirrors ``league_screens.player_stats._build_round_dicts`` team-resolution:
    the team resolves from the Round's ``team_red`` / ``team_blue`` keyed on
    the player's ``team_color``. ``accuracy`` / ``mvp`` are pre-computed here
    from the ``get_accuracy`` / ``get_mvp`` PROPERTIES (no parens) so the pure
    module never touches the MVP formula or the ORM.
    """
    game_round = prs.game_round
    if prs.team_color == "red":
        team = game_round.team_red
    elif prs.team_color == "blue":
        team = game_round.team_blue
    else:
        team = None
    return {
        "player_id": prs.player_id,
        "player_name": prs.player.name,
        "role": prs.role,
        "team_id": team.id if team is not None else 0,
        "team_name": team.name if team is not None else "",
        "points_scored": prs.points_scored,
        "tags_made": prs.tags_made,
        "times_tagged": prs.times_tagged,
        "accuracy": float(prs.get_accuracy),
        "mvp": float(prs.get_mvp),
        "resupplies_given": prs.resupplies_given,
        "specials_used": prs.specials_used,
        "own_specials_cancelled": prs.own_specials_cancelled,
    }


def _season_regular_round_dicts(season: Season) -> list[dict]:
    """Build the flat regular-season seam dicts for a Season, id-ascending.

    Regular-season corpus (LOCKED ORM): every ``PlayerRoundState`` reachable
    via ``game_round__match__season=season`` — playoff Matches carry
    ``season=NULL`` (Part2c-1 #3) so they are naturally excluded. Rows are
    ordered ``id`` ascending so the pure module's "last row wins" identity
    resolution is deterministic.
    """
    prs_qs = (
        PlayerRoundState.objects.filter(game_round__match__season=season)
        .select_related(
            "player",
            "game_round",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )
    return [_award_round_dict(prs) for prs in prs_qs]


def _finals_deciding_node(tournament) -> Optional["object"]:
    """Resolve the terminal Bracket node won by the Tournament champion.

    - ``single_elimination`` / ``round_robin_double_elim`` ⇒ the node with
      ``advances_to_id is None`` whose ``winner_id == tournament.champion_id``.
    - ``double_elimination`` ⇒ the grand-final node GF2
      (``bracket_type == "grand_final"``, ``advances_to_id is None``), or GF1
      when GF2 is a bye (the Bracket reset was skipped).

    Returns ``None`` when no node matches (defensive).
    """
    fmt = tournament.format
    if fmt == "double_elimination":
        gf_nodes = [
            n
            for n in tournament.nodes.all()
            if n.bracket_type == "grand_final" and n.advances_to_id is None
        ]
        # GF2 is the terminal grand-final node; fall back to GF1 when GF2 is a
        # bye / inert (the reset was skipped).
        for node in gf_nodes:
            if not node.is_bye and node.winner_id == tournament.champion_id:
                return node
        # GF2 inert — pick the grand-final node won by the champion (GF1).
        for node in tournament.nodes.all():
            if (
                node.bracket_type == "grand_final"
                and node.winner_id == tournament.champion_id
            ):
                return node
        return None

    # single_elimination / round_robin_double_elim.
    for node in tournament.nodes.all():
        if node.advances_to_id is None and node.winner_id == tournament.champion_id:
            return node
    return None


def _season_finals_mvp(season: Season) -> Optional[AwardWinner]:
    """Compute a Season's Finals MVP, or ``None``.

    Set only when the Season has a tournament/playoff phase whose Tournament
    has a BRACKET format (:data:`_BRACKET_FINALS_FORMATS`). Navigates
    ``ordered_phases()`` → the ``tournament`` phase with a built Tournament →
    the deciding ``BracketNode`` → ALL ``GameRound``s of ALL its
    ``SeriesMatch`` rows → ``pick_finals_mvp`` over the per-round dicts.
    """
    phase = None
    for p in season.ordered_phases():
        if p.phase_type == "tournament" and p.tournament_id is not None:
            phase = p
            break
    if phase is None:
        return None

    tournament = phase.tournament
    if tournament is None or tournament.format not in _BRACKET_FINALS_FORMATS:
        return None
    if tournament.champion_id is None:
        return None

    node = _finals_deciding_node(tournament)
    if node is None:
        return None

    final_round_dicts: list[dict] = []
    for series_match in node.series_matches.all():
        match = series_match.match
        if match is None:
            continue
        prs_qs = (
            PlayerRoundState.objects.filter(game_round__match=match)
            .select_related(
                "player",
                "game_round",
                "game_round__team_red",
                "game_round__team_blue",
            )
            .order_by("id")
        )
        final_round_dicts.extend(_award_round_dict(prs) for prs in prs_qs)

    return pick_finals_mvp(final_round_dicts)


def _compute_season_award_set(season: Season) -> AwardSet:
    """Build a Season's full :class:`AwardSet` (regular-season + finals MVP).

    The single shared path used by ``season_awards`` (the view),
    ``_build_history_row`` (League History), and the player-page awards badge.
    Builds the regular-season seam dicts, derives ``min_games``, calls
    ``compute_season_awards``, then stamps the separately-computed Finals MVP.
    """
    round_dicts = _season_regular_round_dicts(season)

    games_by_player: dict[int, int] = defaultdict(int)
    for row in round_dicts:
        games_by_player[row["player_id"]] += 1
    max_games = max(games_by_player.values(), default=0)
    min_games = ceil(max_games / 2)

    awards = compute_season_awards(round_dicts, min_games=min_games)
    finals_mvp = _season_finals_mvp(season)
    return replace(awards, finals_mvp=finals_mvp)


# Human labels for the player-page awards badge.
_KD_ROLE_LABELS: dict[str, str] = {
    "commander": "K/D — Commander",
    "heavy": "K/D — Heavy",
    "scout": "K/D — Scout",
    "medic": "K/D — Medic",
    "ammo": "K/D — Ammo",
}


def _player_award_labels(awards: AwardSet, player_id: int) -> list[str]:
    """Human labels of the awards ``player_id`` won in this ``AwardSet``."""
    labels: list[str] = []

    def won(winner: Optional[AwardWinner]) -> bool:
        return winner is not None and winner.player_id == player_id

    if won(awards.most_points):
        labels.append("Most Points")
    if won(awards.best_accuracy):
        labels.append("Best Accuracy")
    for role in ROLE_KEYS:
        if won(awards.kd_by_role.get(role)):
            labels.append(_KD_ROLE_LABELS[role])
    if won(awards.best_medic):
        labels.append("Best Medic")
    if won(awards.most_efficient_nuke):
        labels.append("Most Efficient Nuke")
    if won(awards.season_mvp):
        labels.append("Season MVP")
    if won(awards.finals_mvp):
        labels.append("Finals MVP")
    return labels


def _build_history_row(
    season: Season,
    teams_by_id: dict[int, Team],
    *,
    is_in_progress: bool,
) -> dict:
    """LG-01f / LG-03 — build one row of the League History table.

    Returns a dict with 13 keys (the LG-01f 11 plus LG-03's
    ``season_mvp`` / ``finals_mvp``). ``None`` values render as ``"—"``
    in the template. The standings columns consume the pre-fetched
    ``season.matches.all()`` cache and the ``teams_by_id`` lookup with
    zero queries; the LG-03 award cells issue **one** per-Season
    ``PlayerRoundState`` query via ``_compute_season_award_set`` —
    acceptable on the paginated (10-row) History page.
    """
    matches_list_in: list[dict] = []
    for match in season.matches.all():
        if not match.is_completed:
            continue
        matches_list_in.append(
            {
                "match_id": match.id,
                "team_red_id": match.team_red_id,
                "team_blue_id": match.team_blue_id,
                "winner_team_id": match.winner_id,
                "red_rounds_won": match.red_rounds_won,
                "blue_rounds_won": match.blue_rounds_won,
                "red_total_points": match.red_total_points,
                "blue_total_points": match.blue_total_points,
            }
        )
    matches_played = len(matches_list_in)

    if season.starting_team_ids_json is not None:
        team_ids_for_season = list(season.starting_team_ids_json)
        teams_enrolled = len(team_ids_for_season)
    else:
        team_ids_for_season = sorted(t.id for t in season.teams.all())
        teams_enrolled = len(team_ids_for_season)

    enrolled_teams: list[tuple[int, str]] = []
    for tid in team_ids_for_season:
        team_obj = teams_by_id.get(tid)
        enrolled_teams.append((tid, team_obj.name if team_obj is not None else ""))

    standings = compute_standings(matches_list_in, enrolled_teams)

    top_three: list = [
        teams_by_id.get(standings[i].team_id) if i < len(standings) else None
        for i in range(3)
    ]

    if is_in_progress:
        champion: Team | None = None
    else:
        champion = season.champion_team
        if champion is None and standings:
            champion = teams_by_id.get(standings[0].team_id)

    if len(standings) >= 2:
        runner_up = teams_by_id.get(standings[1].team_id)
    else:
        runner_up = None

    # LG-03 — Season MVP + Finals MVP via the shared award path (one per-season
    # PlayerRoundState query — acceptable on the paginated 10-row History page).
    award_set = _compute_season_award_set(season)

    return {
        "season_id": season.id,
        "season_name": season.name,
        "season_url": reverse("season_dashboard", args=[season.id]),
        "start_date": season.start_date,
        "teams_enrolled": teams_enrolled,
        "matches_played": matches_played,
        "champion": champion,
        "runner_up": runner_up,
        "tournament_champion": None,
        "top_three": top_three,
        "is_in_progress": is_in_progress,
        "season_mvp": award_set.season_mvp,
        "finals_mvp": award_set.finals_mvp,
    }


def league_history(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01f — League History page.

    Read-only paginated table of every Season in ``league_id``. The
    in-progress Season (if any) is pinned at the top of the table with
    an "In progress" badge in the Champion cell and live standings in
    the top-3 cells. Completed Seasons paginate newest-first by id.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)

    seasons_qs = (
        league.seasons.select_related("champion_team")
        .prefetch_related("matches", "teams")
        .filter(state__in=["active", "draft", "completed"])
        .order_by("-id")
    )
    seasons = list(seasons_qs)

    team_ids: set[int] = set()
    for s in seasons:
        team_ids.update(s.starting_team_ids_json or [])
        if s.state in {"active", "draft"}:
            team_ids.update(t.id for t in s.teams.all())
    teams_by_id = Team.objects.in_bulk(team_ids)

    in_progress_season = next(
        (s for s in seasons if s.state in {"active", "draft"}),
        None,
    )
    completed_seasons = [s for s in seasons if s.state == "completed"]

    per_page = _coerce_per_page(request.GET.get("per_page"), default=10)
    paginator = Paginator(completed_seasons, per_page)
    page_obj = paginator.get_page(_coerce_page(request.GET.get("page"), default=1))

    in_progress_row = (
        _build_history_row(in_progress_season, teams_by_id, is_in_progress=True)
        if in_progress_season is not None
        else None
    )
    completed_rows = [
        _build_history_row(s, teams_by_id, is_in_progress=False)
        for s in page_obj.object_list
    ]

    request.session["last_league_id"] = league.id

    # Reuse the already-materialised in-memory lists (no extra queries).
    displayed_season = in_progress_season or (
        completed_seasons[0] if completed_seasons else None
    )
    sidebar_links = _build_league_sidebar_links(league, displayed_season, "history")

    # Carry every querystring param EXCEPT ``page`` across page navigation
    # (LG-00c precedent — survives future filter / sort additions).
    qs = request.GET.copy()
    qs.pop("page", None)
    pagination_querystring = qs.urlencode()

    context = {
        "league": league,
        "in_progress_row": in_progress_row,
        "completed_rows": completed_rows,
        "page_obj": page_obj,
        "paginator": paginator,
        "per_page": per_page,
        "per_page_options": _LG01F_PER_PAGE_OPTIONS,
        "pagination_querystring": pagination_querystring,
        "sidebar_links": sidebar_links,
        "sidebar_active": "history",
    }
    return render(request, "leagues/history.html", context)


# ---------------------------------------------------------------------------
# LG-01c — Dashboards (continued)
# ---------------------------------------------------------------------------


def league_dashboard(request, league_id: int) -> HttpResponse:
    """LG-01c — Dashboard for a single League.

    Picks one Season to display (active > most-recent completed > none),
    renders state badge + placeholder action button + top-3 standings +
    next round + round count + three leaders snippets.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = _pick_displayed_season(league)
    if displayed_season is None:
        season_mode = "none"
    elif displayed_season.state == "draft":
        season_mode = "draft"
    elif displayed_season.state == "completed":
        season_mode = "completed"
    else:
        season_mode = "active"

    body = _build_dashboard_context(displayed_season, season_mode)
    sidebar_links = _build_league_sidebar_links(league, displayed_season, "dashboard")
    context = {
        "league": league,
        **body,
        "sidebar_active": "dashboard",
        "sidebar_links": sidebar_links,
        "play_error": None,
        "play_job_id": None,
    }
    return render(request, "leagues/dashboard.html", context)


def season_dashboard(request, season_id: int) -> HttpResponse:
    """LG-01c — Dashboard for a single Season.

    Same body surface as the League dashboard plus a sidebar with live
    links to standings / schedule and disabled placeholders for Teams /
    History.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id
    displayed_season = season
    season_mode = season.state

    body = _build_dashboard_context(displayed_season, season_mode)
    league = season.league
    sidebar_links = _build_league_sidebar_links(
        league, _pick_displayed_season(league), None
    )
    context = {
        "season": season,
        **body,
        "sidebar_active": None,
        "sidebar_links": sidebar_links,
        "play_error": None,
        "play_job_id": None,
    }
    return render(request, "seasons/dashboard.html", context)


# ---------------------------------------------------------------------------
# LG-01d — Play Season views + helper
# ---------------------------------------------------------------------------


def _render_season_dashboard_error(
    request: HttpRequest, season: Season, play_error: str
) -> HttpResponse:
    """LG-01d — re-render the Season dashboard with ``play_error`` set."""
    season_mode = season.state
    body = _build_dashboard_context(season, season_mode)
    context = {
        "season": season,
        **body,
        "sidebar_active": None,
        "sidebar_links": _build_league_sidebar_links(
            season.league, _pick_displayed_season(season.league), None
        ),
        "play_error": play_error,
        "play_job_id": None,
    }
    return render(request, "seasons/dashboard.html", context, status=400)


def start_season(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the ``draft → active`` transition.

    POST only. Idempotent on the "already active" double-submit race —
    a ``ValidationError`` whose message contains the substring
    ``"non-completed"`` (the LG-01 ``Season.clean()`` wording) is
    swallowed and the user is redirected to the dashboard. Any other
    ``ValidationError`` re-renders the Season dashboard with
    ``play_error`` populated and HTTP 400.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        season.start_season()
    except ValidationError as exc:
        messages_list = getattr(exc, "messages", None) or [str(exc)]
        joined = " ".join(messages_list)
        season.refresh_from_db()
        if "non-completed" in joined or season.state == "active":
            return redirect("season_dashboard", season_id=season.id)
        return _render_season_dashboard_error(request, season, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def play_week(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for Play One Week (one matchday).

    Sync, single ``@transaction.atomic`` wrapping every Round in the
    next unplayed matchday. On a Season already finished (no unplayed
    fixtures) ⇒ idempotent 302 redirect. On a non-``active`` Season ⇒
    400 + ``play_error``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    if season.state != "active":
        return _render_season_dashboard_error(
            request,
            season,
            f"Season must be active to play; got state={season.state!r}",
        )

    # LG-02-Part2c-3f — weekly playoff pacing. When the cursor sits on a
    # built+active tournament phase, "One Week" drains exactly ONE bracket
    # STAGE (the lowest incomplete (bracket_type, bracket_round) group) and
    # then crowns the Season champion if the final node resolved. Otherwise
    # the RR matchday path below runs unchanged. play_next_bracket_round
    # carries its own per-Match atomicity, so the playoff branch needs no
    # transaction.atomic wrapper.
    phase = season.current_phase()
    if (
        phase is not None
        and phase.phase_type == "tournament"
        and phase.tournament_id is not None
    ):
        from matches.tournament_engine import play_next_bracket_round

        play_next_bracket_round(phase.tournament)
        season.complete_if_finished()
        return redirect("season_dashboard", season_id=season.id)

    try:
        with transaction.atomic():
            # LG-01j — deferred import of ArenaMap + the per-fixture
            # map-resolver helper. Mirrors the ``play_season_task``
            # pattern: ``in_bulk`` runs ONCE outside the per-fixture
            # loop, the helper is called per fixture.
            from core.models import ArenaMap
            from matches.tasks import _resolve_fixture_map

            # LG-02-Part2c-2 — by-phase fixtures (global-continuous matchday
            # offset already applied) + phase-aware played_keys.
            # LG-02-Part2c-3c — barrier-aware: the RR loop halts at an
            # incomplete tournament phase so a mid-season bracket drains first.
            by_phase = season.playable_fixtures_by_phase()
            phase_by_id = {phase.id: phase for phase, _ in by_phase}
            fixtures = [
                (phase.id, fixture)
                for phase, phase_fixtures in by_phase
                for fixture in phase_fixtures
            ]
            # LG-02-Part2c-3a — played_keys gain ``leg`` so a double_round_robin
            # phase's two legs are distinct.
            played_keys = {
                (
                    gr.match.season_phase_id,
                    frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
                    gr.round_number,
                    gr.match.leg,
                )
                for gr in GameRound.objects.filter(match__season=season).select_related(
                    "match"
                )
            }
            to_play = select_play_fixtures(fixtures, played_keys, 1)
            if not to_play:
                return redirect("season_dashboard", season_id=season.id)
            team_ids = {f.team_a_id for _pid, f in to_play} | {
                f.team_b_id for _pid, f in to_play
            }
            team_by_id = Team.objects.in_bulk(team_ids)
            # LG-01j — bulk-load the frozen-snapshot map pool once.
            pool_ids = season.starting_map_pool_ids_json or []
            pool_by_id: dict[int, ArenaMap] = ArenaMap.objects.in_bulk(pool_ids)
            for phase_id, fixture in to_play:
                team_a = team_by_id[fixture.team_a_id]
                team_b = team_by_id[fixture.team_b_id]
                arena_map = _resolve_fixture_map(season, fixture, pool_by_id)
                BatchSimulator().simulate_scheduled_round(
                    season,
                    team_a,
                    team_b,
                    fixture.round_number,
                    arena_map=arena_map,
                    season_phase=phase_by_id.get(phase_id),
                    leg=fixture.leg,
                )
    except (ValidationError, ValueError) as exc:
        return _render_season_dashboard_error(request, season, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def play_two_months(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the Play Two Months async run.

    Validates the Season is ``active`` (else 400 + ``play_error``), then
    enqueues ``play_season_task.delay(season_id, max_matchdays=8)`` and
    returns ``JsonResponse({"job_id", "season_id"}, status=202)``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    if season.state != "active":
        return _render_season_dashboard_error(
            request,
            season,
            f"Season must be active to play; got state={season.state!r}",
        )

    result = play_season_task.delay(season.id, max_matchdays=8)
    return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)


def play_until_end(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the Play Until End of Season async run."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    if season.state != "active":
        return _render_season_dashboard_error(
            request,
            season,
            f"Season must be active to play; got state={season.state!r}",
        )

    result = play_season_task.delay(season.id, max_matchdays=None)
    return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)


def play_single_round(request, season_id: int) -> HttpResponse:
    """LG-02-Part2c-1 — POST entry point for Play Single Round (one playoff
    Match).

    Sync. POST only (405 on GET). Requires the cursor be on a built + active
    tournament phase; otherwise re-renders the Season dashboard with a
    ``play_error`` (HTTP 400, the LG-01d ``play_error`` pattern). Plays
    exactly one playoff Match via ``play_next_node``, then
    ``complete_if_finished()`` crowns the Season when the final node
    resolves. 302 redirect to the dashboard on success.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    phase = season.current_phase()
    if phase is None or phase.phase_type != "tournament" or phase.tournament_id is None:
        return _render_season_dashboard_error(
            request, season, "No active playoff bracket to play."
        )

    from matches.tournament_engine import play_next_node

    play_next_node(phase.tournament)
    season.complete_if_finished()
    return redirect("season_dashboard", season_id=season.id)


def play_playoffs(request, season_id: int) -> JsonResponse:
    """LG-02-Part2c-1 — POST entry point for the Play Playoffs async run.

    POST only (405 on GET). Requires the cursor be on a built + active
    tournament phase; otherwise 409 JSON ``{"error": ...}``. Happy path
    enqueues ``play_playoffs_task.delay(season_id)`` and returns
    ``JsonResponse({"job_id", "season_id"}, status=202)``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    phase = season.current_phase()
    if phase is None or phase.phase_type != "tournament" or phase.tournament_id is None:
        return JsonResponse({"error": "No active playoff bracket to play."}, status=409)

    result = play_playoffs_task.delay(season.id)
    return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)


def _build_play_status_response(
    async_result: AsyncResult,
    *,
    season_id: int,
) -> dict:
    """Assemble the locked 5-key polling JSON for a Play Season job.

    Returns ``{"status", "completed", "total", "error", "season_id"}``
    per the LG-01d seam contract §3.
    """
    state = async_result.state
    status = _celery_state_to_job_status(state)

    completed = 0
    total = 0
    error: str | None = None

    if state == "PROGRESS":
        info = async_result.info or {}
        if isinstance(info, dict):
            completed = int(info.get("completed", 0) or 0)
            total = int(info.get("total", 0) or 0)
    elif state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            completed = int(result.get("completed", 0) or 0)
            total = int(result.get("total", 0) or 0)
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {
        "status": status,
        "completed": completed,
        "total": total,
        "error": error,
        "season_id": season_id,
    }


def play_status(request, season_id: int, job_id: str) -> JsonResponse:
    """LG-01d — Shared polling endpoint for both async play tasks.

    GET only. Returns the locked 5-key polling JSON. The URL kwarg
    ``season_id`` is authoritative; any ``?season_id=`` query param is
    the carry pattern but the URL kwarg wins on disagreement.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_object_or_404(Season, pk=season_id)
    # Only write when the value actually changes. play_status is polled every
    # ~0.5s for the whole "Play …" run; an unconditional assignment marks the
    # session modified on every poll, forcing a django_session write that
    # competes with the play loop's per-Round write transactions and triggers
    # "database is locked" on SQLite. The guard keeps last_league_id fresh
    # (first poll writes it) without writing on every subsequent poll.
    if request.session.get("last_league_id") != season.league_id:
        request.session["last_league_id"] = season.league_id

    async_result = AsyncResult(job_id)
    return JsonResponse(_build_play_status_response(async_result, season_id=season_id))


# ---------------------------------------------------------------------------
# LG-01g — Per-Team Schedule view + helpers
# ---------------------------------------------------------------------------


def _render_fixture_sides(fixture, teams_by_id: dict[int, Team]):
    """LG-01g — resolve a fixture's per-Round Side assignment.

    Round 1 ⇒ ``(teams_by_id[team_a_id], teams_by_id[team_b_id])``.
    Round 2 ⇒ ``(teams_by_id[team_b_id], teams_by_id[team_a_id])`` —
    the per-Match colour swap simulated round 2 persists.

    Raises ``KeyError`` if a team id is missing from ``teams_by_id`` —
    a real bug, never swallowed (the lookup is built from the same
    ``starting_team_ids_json`` set that produced the fixtures).
    """
    if fixture.round_number == 1:
        return (teams_by_id[fixture.team_a_id], teams_by_id[fixture.team_b_id])
    return (teams_by_id[fixture.team_b_id], teams_by_id[fixture.team_a_id])


def _build_team_schedule_rows(
    displayed_season: Season,
    team: Team,
    fixtures: list,
    played_game_rounds: Iterable[GameRound],
    teams_by_id: dict[int, Team],
) -> dict[str, list[dict]]:
    """LG-01g — walk fixtures + played GameRounds, partition into
    Upcoming / Completed rows from the picked Team's perspective.

    Algorithm (§4c, pinned):
        1. ``played_keys`` keyed Side-agnostically on
           ``(frozenset({team_red_id, team_blue_id}), round_number)``.
        2. ``fixture_by_key`` over the full ``fixtures`` list for
           Completed-row matchday recovery.
        3. Filter fixtures to ones involving ``team``.
        4. Per filtered fixture: skip if played, else build the
           7-key Upcoming row via ``_render_fixture_sides``.
        5. Per played GameRound: build the 11-key Completed row from
           the persisted ``match.team_red`` / ``team_blue`` (NOT
           recomputed — the GameRound records actual physical Sides).
        6. Sort Upcoming by ``(matchday, round_number)`` asc; Completed
           keeps queryset order (id asc = chronological).
    """
    # LG-02-Part2c-3a — every key gains ``leg`` so a double_round_robin phase's
    # two legs are distinct (``gr.match.leg`` / ``fixture.leg``).
    played_game_rounds = list(played_game_rounds)
    played_keys: set[tuple[frozenset[int], int, int]] = set()
    for gr in played_game_rounds:
        match = gr.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        played_keys.add(
            (
                frozenset({match.team_red_id, match.team_blue_id}),
                gr.round_number,
                match.leg,
            )
        )

    fixture_by_key: dict[tuple[frozenset[int], int, int], object] = {}
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        fixture_by_key[key] = fixture

    upcoming: list[dict] = []
    for fixture in fixtures:
        if team.id not in {fixture.team_a_id, fixture.team_b_id}:
            continue
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        if key in played_keys:
            continue
        red_team, blue_team = _render_fixture_sides(fixture, teams_by_id)
        fixture_date = displayed_season.start_date + timedelta(
            days=(fixture.matchday - 1) * 7
        )
        upcoming.append(
            {
                "matchday": fixture.matchday,
                "round_number": fixture.round_number,
                "date": fixture_date,
                "red_team_id": red_team.id,
                "red_team_name": red_team.name,
                "blue_team_id": blue_team.id,
                "blue_team_name": blue_team.name,
            }
        )

    upcoming.sort(key=lambda r: (r["matchday"], r["round_number"]))

    completed: list[dict] = []
    for gr in played_game_rounds:
        match = gr.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            # Defensive — a GameRound with no persisted Match cannot
            # render the per-Side breakdown; skip silently.
            continue
        key = (
            frozenset({match.team_red_id, match.team_blue_id}),
            gr.round_number,
            match.leg,
        )
        fixture = fixture_by_key.get(key)
        matchday = fixture.matchday if fixture is not None else 0
        fixture_date = displayed_season.start_date + timedelta(days=(matchday - 1) * 7)
        if gr.round_number == 1:
            red_score = match.red_round1_points
            blue_score = match.blue_round1_points
        else:
            red_score = match.red_round2_points
            blue_score = match.blue_round2_points

        picked_is_red = team.id == match.team_red_id
        picked_per_round = red_score if picked_is_red else blue_score
        other_per_round = blue_score if picked_is_red else red_score
        if picked_per_round > other_per_round:
            outcome = "W"
        elif picked_per_round < other_per_round:
            outcome = "L"
        else:
            outcome = "T"

        completed.append(
            {
                "matchday": matchday,
                "round_number": gr.round_number,
                "date": fixture_date,
                "red_team_id": match.team_red_id,
                "red_team_name": match.team_red.name,
                "blue_team_id": match.team_blue_id,
                "blue_team_name": match.team_blue.name,
                "game_round_id": gr.id,
                "red_score": red_score,
                "blue_score": blue_score,
                "outcome": outcome,
            }
        )

    return {"upcoming": upcoming, "completed": completed}


def team_schedule(request: HttpRequest, league_id: int, team_id: int) -> HttpResponse:
    """LG-01g — Per-Team Schedule page.

    Two-column read-only view of a single Team's per-Round schedule
    inside the displayed Season of ``league_id``. The Upcoming column
    enumerates unplayed (fixture, round_number) pairs that involve the
    Team; the Completed column enumerates persisted GameRounds for
    Matches involving the Team. A dropdown above the columns navigates
    to a different Team's view inside the same League.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    team = get_object_or_404(Team, pk=team_id)

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    if displayed_season is None:
        return HttpResponseNotFound("No Season in this League.")

    if displayed_season.starting_team_ids_json:
        team_ids = list(displayed_season.starting_team_ids_json)
    else:
        team_ids = sorted(t.id for t in displayed_season.teams.all())

    # LG-02-Part2a — route fixtures through the Season chokepoint.
    fixtures = displayed_season.scheduled_fixtures()
    teams_by_id = Team.objects.in_bulk(team_ids)

    played_game_rounds = list(
        GameRound.objects.filter(match__season=displayed_season)
        .filter(Q(match__team_red=team) | Q(match__team_blue=team))
        .select_related("match", "match__team_red", "match__team_blue")
        .order_by("id")
    )

    rows = _build_team_schedule_rows(
        displayed_season=displayed_season,
        team=team,
        fixtures=fixtures,
        played_game_rounds=played_game_rounds,
        teams_by_id=teams_by_id,
    )

    request.session["last_league_id"] = league.id

    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, "schedule_team"
    )
    context = {
        "league": league,
        "displayed_season": displayed_season,
        "team": team,
        "upcoming_rows": rows["upcoming"],
        "completed_rows": rows["completed"],
        "team_picker_options": displayed_season.teams.order_by("name"),
        "sidebar_links": sidebar_links,
        "sidebar_active": "schedule_team",
        "current_team": league.current_team,
    }
    return render(request, "leagues/team_schedule.html", context)


@transaction.atomic
def next_season(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01e — POST entry point for the Start Next Season action.

    Creates a fresh ``draft`` Season inside ``league_id`` with copied
    teams from the latest completed Season's snapshot, an auto-generated
    name, and a Jan-1-next-year start date. Redirects to the new
    Season's dashboard on success.

    Guards (in order):
        1. 405 on non-POST.
        2. 404 on missing League.
        3. 302 redirect to ``season_dashboard`` of ``league.active_season``
           when a non-completed Season already exists (active-Season
           guard — idempotent on double-submit; the UI hides the
           button when a Season is in progress, but a stray POST
           lands the user on the in-progress Season's dashboard).
        4. 400 ``HttpResponseBadRequest("No completed Season in this League.")``
           when no completed Season exists (defensive — should never
           fire because the LG-01c button only shows when displayed
           Season is completed).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    if league.active_season is not None:
        return redirect("season_dashboard", season_id=league.active_season.id)

    all_seasons = list(league.seasons.all())
    completed = [s for s in all_seasons if s.state == "completed"]
    if not completed:
        return HttpResponseBadRequest("No completed Season in this League.")
    latest_completed = max(completed, key=lambda s: s.id)

    name = f"Season {len(all_seasons) + 1}"
    start_date = date(latest_completed.start_date.year + 1, 1, 1)
    schedule_format = latest_completed.schedule_format

    new_season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format=schedule_format,
        state="draft",
        # LG-01j — carry map_mode verbatim from the previous Season.
        map_mode=latest_completed.map_mode,
    )

    team_ids = latest_completed.starting_team_ids_json or []
    if team_ids:
        teams_qs = Team.objects.filter(id__in=team_ids)
        new_season.teams.add(*teams_qs)

    # LG-01j — rehydrate the new Season's map_pool from the previous
    # Season's FROZEN SNAPSHOT (NOT its live M2M). The snapshot is the
    # source of truth post-activation: admin-side edits to the live
    # ``map_pool`` of the completed Season don't leak into the next
    # Season's pool. Deleted maps simply drop out of the queryset.
    from core.models import ArenaMap

    map_pool_ids = latest_completed.starting_map_pool_ids_json or []
    if map_pool_ids:
        new_season.map_pool.set(ArenaMap.objects.filter(id__in=map_pool_ids))

    # LG-02-Part2b — carry the previous Season's full phase composition
    # forward (mirrors the team-id / map-pool carry-forward). Copy
    # ordinal / phase_type / schedule_format verbatim; reset tournament to
    # NULL. ``Meta.ordering = ["ordinal"]`` guarantees the source order.
    # LG-02-Part2c-3b — also carry ``tournament_mode`` verbatim so a future
    # non-``standings`` mode (Part2c-3c) reproduces across seasons.
    for src in latest_completed.phases.all():
        SeasonPhase.objects.create(
            season=new_season,
            ordinal=src.ordinal,
            phase_type=src.phase_type,
            schedule_format=src.schedule_format,
            tournament=None,
            tournament_mode=src.tournament_mode,
            # LG-02-Part2c-3d / 3e — carry all tournament columns forward verbatim
            # (the source row has real persisted values for each).
            tournament_cut=src.tournament_cut,
            tournament_format=src.tournament_format,
            final_series_length=src.final_series_length,
            semifinal_series_length=src.semifinal_series_length,
            quarterfinal_series_length=src.quarterfinal_series_length,
            earlier_series_length=src.earlier_series_length,
            wb_advancers=src.wb_advancers,
            lb_advancers=src.lb_advancers,
            swiss_rounds=src.swiss_rounds,
        )

    # LG-04 — age + develop every Player in the rolling League's developing set,
    # tick total_games, and write one PlayerSeasonRating row tagged to the NEW
    # Season. Inside the same atomic block (after carry-forward, before redirect)
    # so a failure rolls back the whole rollover.
    _develop_league_for_new_season(league, new_season, latest_completed)

    return redirect("season_dashboard", season_id=new_season.id)
