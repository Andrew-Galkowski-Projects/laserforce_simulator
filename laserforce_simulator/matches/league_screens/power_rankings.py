"""LG-01z-b — Power Rankings league screen view.

Read-only, GET-only screen rendering a ranked table of every Team enrolled
in the League's ``displayed_season``, scored by a composite **power score**
(see ``matches/power_rankings_logic.py``). Follows the LG-01z shared view
contract (§2): GET-guard → ``get_object_or_404`` → session write →
``displayed_season`` pick → sidebar links → screen aggregation → render.
"""

from __future__ import annotations

from collections import defaultdict

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import (
    _build_league_sidebar_links,
    _resolve_season_scope,
    _season_param,
)
from matches.models import GameRound, League, Match
from matches.power_rankings_logic import (
    SORT_KEYS_DISPLAY,
    PowerRankingInput,
    coerce_dir,
    coerce_sort,
    compute_power_rankings,
    sort_power_rankings,
)
from matches.standings import compute_standings
from teams.models import Team


def _enrolled_team_ids(displayed_season) -> list[int]:
    """Team ids enrolled in the displayed Season.

    Prefers the frozen ``starting_team_ids_json`` snapshot (active /
    completed Seasons); falls back to the live M2M for draft Seasons where
    the snapshot is still ``None`` (mirrors the LG-01 standings precedent).
    """
    if displayed_season.starting_team_ids_json is not None:
        return list(displayed_season.starting_team_ids_json)
    return sorted(t.id for t in displayed_season.teams.all())


def _team_mean_rating(team: Team) -> float:
    """Mean ``overall_rating`` over a team's active-roster players.

    Returns ``0.0`` when the team has no active slots filled, so the
    power-score normalization never trips on an empty iterable.
    """
    actives = team.active_players
    if not actives:
        return 0.0
    return sum(p.overall_rating for p in actives) / len(actives)


def power_rankings(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-b — Power Rankings page for a League's displayed Season.

    Ranks enrolled Teams by a composite power score = sum of three
    min-max-normalized (per-League, to [0, 1]) components: (1) team mean
    ``overall_rating`` over ``active_players``; (2) win% from completed
    Matches via ``compute_standings`` = wins / (wins + losses + ties)
    (0 when none); (3) avg score diff per Round (mean of
    ``red_points - blue_points`` from the team's perspective). Highest sum
    ranks #1; ties break on team name ascending. Only Teams enrolled in
    ``displayed_season`` are ranked.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active="power_rankings"
    )

    sort = coerce_sort(request.GET.get("sort"))
    direction = coerce_dir(request.GET.get("dir"))

    # LG-06d — season selector. Picker options + the forgiving ``?season=``
    # coercion (defaults to displayed_season — fully backward-compatible).
    seasons, selected_season, season_options, season_filter = _resolve_season_scope(
        request, league, displayed_season
    )

    # COERCE-BEFORE-QUERYSTRING: the sort-header href carries every param
    # except sort/dir, with the coerced ``season`` re-set so the chosen scope
    # survives a re-sort (LG-06d).
    qs_no_sort_dir = request.GET.copy()
    qs_no_sort_dir.pop("sort", None)
    qs_no_sort_dir.pop("dir", None)
    qs_no_sort_dir["season"] = _season_param(selected_season)
    querystring_without_sort_dir = qs_no_sort_dir.urlencode()

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "power_rankings",
        "sort": sort,
        "dir": direction,
        "sort_keys_display": SORT_KEYS_DISPLAY,
        "season_options": season_options,
        "selected_season": selected_season,
        "querystring_without_sort_dir": querystring_without_sort_dir,
    }

    if season_filter is None:
        # Empty-state per §2 — no Season scope, render the notice instead of
        # the body. The sidebar still renders.
        context["rows"] = []
        return render(request, "leagues/power_rankings.html", context)

    # Enrolled teams come from the displayed Season (the League's current
    # roster) even under a Career / past-Season scope.
    team_ids = (
        _enrolled_team_ids(displayed_season) if displayed_season is not None else []
    )
    teams_by_id: dict[int, Team] = Team.objects.in_bulk(team_ids)

    # Re-point the LG-06d scope onto the two join shapes: Match filters
    # directly (strip the leading ``match__``); GameRound joins via
    # ``match__season…`` (the season_filter shape verbatim).
    match_filter = {k[len("match__") :]: v for k, v in season_filter.items()}

    # --- Component 2: win% via compute_standings over completed Matches ---
    # LG-07a — member nights are social, not ranked: they must not move the
    # power rankings either.
    completed_qs = Match.objects.filter(is_completed=True, **match_filter).exclude(
        season_phase__phase_type="member_night"
    )
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
    enrolled_for_standings = [
        (tid, teams_by_id[tid].name if tid in teams_by_id else "") for tid in team_ids
    ]
    standings = compute_standings(completed_matches, enrolled_for_standings)
    win_pct_by_id: dict[int, float] = {}
    for row in standings:
        played = row.wins + row.losses + row.ties
        win_pct_by_id[row.team_id] = (row.wins / played) if played else 0.0

    # --- Component 3: avg score diff per Round from each team's view -------
    rounds_qs = GameRound.objects.filter(**season_filter).select_related("match")
    diff_sums: dict[int, float] = defaultdict(float)
    diff_counts: dict[int, int] = defaultdict(int)
    for game_round in rounds_qs:
        red_id = game_round.team_red_id
        blue_id = game_round.team_blue_id
        if red_id is not None:
            diff_sums[red_id] += game_round.red_points - game_round.blue_points
            diff_counts[red_id] += 1
        if blue_id is not None:
            diff_sums[blue_id] += game_round.blue_points - game_round.red_points
            diff_counts[blue_id] += 1

    # --- Assemble per-team inputs (enrolled teams only) -------------------
    inputs: list[PowerRankingInput] = []
    for tid in team_ids:
        team = teams_by_id.get(tid)
        if team is None:
            # Defensive — a stale snapshot id whose Team was deleted. Skip
            # it rather than crash the page.
            continue
        count = diff_counts.get(tid, 0)
        avg_diff = (diff_sums[tid] / count) if count else 0.0
        inputs.append(
            PowerRankingInput(
                team_id=tid,
                team_name=team.name,
                mean_rating=_team_mean_rating(team),
                win_pct=win_pct_by_id.get(tid, 0.0),
                avg_score_diff=avg_diff,
            )
        )

    ranked = compute_power_rankings(inputs)
    ranked = sort_power_rankings(ranked, sort, direction)
    rows = [(row, teams_by_id.get(row.team_id)) for row in ranked]

    context["rows"] = rows
    return render(request, "leagues/power_rankings.html", context)
