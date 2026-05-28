"""League / Season views — the LG-01..LG-01f stack.

Extracted from ``matches/views.py`` to keep the league lifecycle (model
list, create flow, dashboards, Play Season POSTs, season standings /
schedule pages, league history) in one file. URL configs in
``matches/league_urls.py`` and ``matches/season_urls.py`` point at the
callables here; URL names are unchanged.
"""

import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from celery.result import AsyncResult
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Team
from teams.views import _generate_teams

from .forms import CreateLeagueForm
from .models import GameRound, League, Match, PlayerRoundState, Season
from .schedule_generator import generate_schedule
from .season_dashboard import (
    LeaderRow,
    compute_leaders,
    find_next_fixture,
    round_progress,
    select_play_fixtures,
)
from .simulation import BatchSimulator
from .standings import compute_standings
from .tasks import play_season_task
from .views import _celery_state_to_job_status

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
                }
            )

        if season.starting_team_ids_json is not None:
            team_ids = list(season.starting_team_ids_json)
        else:
            team_ids = sorted(t.id for t in season.teams.all())

        enrolled_teams = list(
            Team.objects.filter(id__in=team_ids).values_list("id", "name")
        )
        rows = compute_standings(completed_matches, enrolled_teams)
        teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}

    def _row_team_id(row) -> int:
        if hasattr(row, "team_id"):
            return row.team_id
        return row["team_id"]

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
    }
    return render(request, "seasons/standings.html", context)


def season_schedule(request, season_id: int) -> HttpResponse:
    """LG-01 — Schedule page for a Season.

    Renders the deterministic fixture list from ``generate_schedule``
    with persisted ``GameRound``s overlaid. Fixtures are grouped by
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

    fixtures = generate_schedule(team_ids, season.schedule_format)

    teams_by_id: dict[int, Team] = {
        t.id: t for t in Team.objects.filter(id__in=team_ids)
    }

    # Index played GameRounds by (frozenset of team ids, round_number).
    rounds_qs = GameRound.objects.filter(match__season=season).select_related("match")
    played_by_key: dict[tuple[frozenset[int], int], GameRound] = {}
    for game_round in rounds_qs:
        match = game_round.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        key = (
            frozenset({match.team_red_id, match.team_blue_id}),
            game_round.round_number,
        )
        played_by_key[key] = game_round

    # Build per-fixture dicts.
    per_fixture: list[dict] = []
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
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
    season = Season.objects.create(
        league=league,
        name=cleaned["season_name"],
        start_date=cleaned["start_date"],
        state="draft",
        schedule_format=cleaned["schedule_format"],
    )
    season.teams.add(*created_teams)

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
        if len(team_ids) >= 2:
            fixtures = generate_schedule(team_ids, displayed_season.schedule_format)
        else:
            fixtures = []

        rounds_qs = GameRound.objects.filter(
            match__season=displayed_season
        ).select_related("match")
        played_keys: set = set()
        for game_round in rounds_qs:
            match = game_round.match
            if match is None or match.team_red_id is None or match.team_blue_id is None:
                continue
            played_keys.add(
                (
                    frozenset({match.team_red_id, match.team_blue_id}),
                    game_round.round_number,
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
    }


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


def _build_league_sidebar_links(
    league: League,
    displayed_season: Season | None,
    sidebar_active: str | None,
) -> list[dict]:
    """LG-01f — build the 14-entry League sidebar list (top / LEAGUE / TEAM / PLAYERS).

    Live entries:
        * top.dashboard ⇒ ``league_dashboard``.
        * league.standings / league.schedule ⇒ the displayed Season's
          standings / schedule when ``displayed_season is not None``,
          else disabled.
        * league.history ⇒ ``league_history``.

    All other entries are disabled placeholders for LG-02+.
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

    raw_entries: list[tuple[str, str, str, str | None]] = [
        (
            "top",
            "dashboard",
            "Dashboard",
            reverse("league_dashboard", args=[league.id]),
        ),
        ("league", "standings", "Standings", standings_url),
        ("league", "schedule", "Schedule", schedule_url),
        ("league", "playoffs", "Playoffs", None),
        ("league", "finances", "Finances", None),
        ("league", "history", "History", reverse("league_history", args=[league.id])),
        ("league", "power_rankings", "Power Rankings", None),
        ("team", "roster", "Roster", None),
        ("team", "schedule_team", "Schedule", None),
        ("team", "finances_team", "Finances", None),
        ("team", "history_team", "History", None),
        ("players", "free_agents", "Free Agents", None),
        ("players", "trade", "Trade", None),
        ("players", "trading_block", "Trading Block", None),
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


def _build_history_row(
    season: Season,
    teams_by_id: dict[int, Team],
    *,
    is_in_progress: bool,
) -> dict:
    """LG-01f — build one row of the League History table.

    Returns a dict with the 11 frozen keys described by the seam
    contract. ``None`` values render as ``"—"`` in the template.
    Consumes the pre-fetched ``season.matches.all()`` prefetch cache
    and the pre-built ``teams_by_id`` lookup so this helper issues
    zero queries.
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

    try:
        with transaction.atomic():
            fixtures = generate_schedule(
                season.starting_team_ids_json or [], season.schedule_format
            )
            played_keys = {
                (
                    frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
                    gr.round_number,
                )
                for gr in GameRound.objects.filter(match__season=season).select_related(
                    "match"
                )
            }
            to_play = select_play_fixtures(fixtures, played_keys, 1)
            if not to_play:
                return redirect("season_dashboard", season_id=season.id)
            team_ids = {f.team_a_id for f in to_play} | {f.team_b_id for f in to_play}
            team_by_id = Team.objects.in_bulk(team_ids)
            for fixture in to_play:
                team_a = team_by_id[fixture.team_a_id]
                team_b = team_by_id[fixture.team_b_id]
                BatchSimulator().simulate_scheduled_round(
                    season, team_a, team_b, fixture.round_number
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
    request.session["last_league_id"] = season.league_id

    async_result = AsyncResult(job_id)
    return JsonResponse(_build_play_status_response(async_result, season_id=season_id))


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
    )

    team_ids = latest_completed.starting_team_ids_json or []
    if team_ids:
        teams_qs = Team.objects.filter(id__in=team_ids)
        new_season.teams.add(*teams_qs)

    return redirect("season_dashboard", season_id=new_season.id)
