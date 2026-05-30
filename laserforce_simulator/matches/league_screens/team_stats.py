"""LG-01z-p — Team Stats league screen view.

Read-only, GET-only screen rendering a sortable per-team statistics table
for the League's ``displayed_season``. Follows the LG-01z shared view
contract (§2): GET-guard → ``get_object_or_404`` → session write →
``displayed_season`` pick → sidebar links → screen aggregation → render.

Heavy aggregation lives in the pure module ``matches/team_stats_logic.py``;
this view materialises plain per-round + per-event dicts from the ORM and
hands them over. See ``.claude/worktrees/lg-01z-seam-contract.md`` §4-p and
the event-type → column mapping documented in ``team_stats_logic`` /
``matches/CLAUDE.md``.
"""

from __future__ import annotations

from collections import defaultdict

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches.league_views import _build_league_sidebar_links
from matches.models import GameEvent, GameRound, League, PlayerRoundState
from matches.team_stats_logic import (
    SORT_KEYS_DISPLAY,
    aggregate_team_stats,
    coerce_dir,
    coerce_sort,
    sort_team_stats,
)
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


def team_stats(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-p — Team Stats page for a League's displayed Season.

    Aggregates per-team stats over the displayed Season's completed Rounds
    and their GameEvents: avg points-for / against / margin, avg survivors
    (count of ``final_lives > 0`` per Round), total tags landed, total times
    tagged, base captures, missiles fired / hit, nukes fired / landed, and
    cancelled-nuke count. Sortable via ``?sort=&dir=``. Only Teams enrolled
    in ``displayed_season`` are listed. Renders an empty-state notice when
    the League has no Season.
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
        league, displayed_season, sidebar_active="team_stats"
    )

    sort = coerce_sort(request.GET.get("sort"))
    direction = coerce_dir(request.GET.get("dir"))

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "team_stats",
        "sort": sort,
        "dir": direction,
        "sort_keys_display": SORT_KEYS_DISPLAY,
    }

    if displayed_season is None:
        # Empty-state per §2 — no Season, render the notice instead of the
        # body. The sidebar still renders.
        context["rows"] = []
        return render(request, "leagues/team_stats.html", context)

    team_ids = _enrolled_team_ids(displayed_season)
    teams_by_id: dict[int, Team] = Team.objects.in_bulk(team_ids)
    enrolled_teams = [
        (tid, teams_by_id[tid].name if tid in teams_by_id else "") for tid in team_ids
    ]

    # --- Per-round dicts (one per Team appearance in a Round) -------------
    rounds_qs = GameRound.objects.filter(match__season=displayed_season)

    # Per-(round, color) survivor + tag/tagged sums from PlayerRoundState.
    prs_qs = PlayerRoundState.objects.filter(
        game_round__match__season=displayed_season
    ).values("game_round_id", "team_color", "final_lives", "tags_made", "times_tagged")
    survivors: dict[tuple[int, str], int] = defaultdict(int)
    tags_landed: dict[tuple[int, str], int] = defaultdict(int)
    times_tagged: dict[tuple[int, str], int] = defaultdict(int)
    for prs in prs_qs:
        key = (prs["game_round_id"], prs["team_color"])
        if prs["final_lives"] > 0:
            survivors[key] += 1
        tags_landed[key] += prs["tags_made"]
        times_tagged[key] += prs["times_tagged"]

    team_rounds: list[dict] = []
    for gr in rounds_qs:
        for color, team_id, points_for, points_against in (
            ("red", gr.team_red_id, gr.red_points, gr.blue_points),
            ("blue", gr.team_blue_id, gr.blue_points, gr.red_points),
        ):
            if team_id is None:
                continue
            key = (gr.id, color)
            team_rounds.append(
                {
                    "team_id": team_id,
                    "points_for": points_for,
                    "points_against": points_against,
                    "survivors": survivors[key],
                    "tags_landed": tags_landed[key],
                    "times_tagged": times_tagged[key],
                }
            )

    # --- Per-event dicts (one per relevant GameEvent) ---------------------
    # Resolve each event's actor team_id via the actor's PlayerRoundState in
    # that Round (team_color → team_red/team_blue). Build the lookup once.
    actor_team_by_key: dict[tuple[int, int], int] = {}
    color_prs = PlayerRoundState.objects.filter(
        game_round__match__season=displayed_season
    ).values("game_round_id", "player_id", "team_color")
    round_sides: dict[int, tuple[int | None, int | None]] = {
        gr.id: (gr.team_red_id, gr.team_blue_id) for gr in rounds_qs
    }
    for row in color_prs:
        red_id, blue_id = round_sides.get(row["game_round_id"], (None, None))
        if row["team_color"] == "red":
            team_id = red_id
        elif row["team_color"] == "blue":
            team_id = blue_id
        else:
            team_id = None
        if team_id is not None:
            actor_team_by_key[(row["game_round_id"], row["player_id"])] = team_id

    events_qs = GameEvent.objects.filter(
        game_round__match__season=displayed_season,
        event_type__in=["base_capture", "missiled", "special", "nuke_cancelled"],
    ).values(
        "game_round_id",
        "actor_id",
        "event_type",
        "points_awarded",
        "metadata",
    )

    team_events: list[dict] = []
    for ev in events_qs:
        team_id = actor_team_by_key.get((ev["game_round_id"], ev["actor_id"]))
        if team_id is None:
            continue
        etype = ev["event_type"]
        meta = ev["metadata"] or {}
        if etype == "base_capture":
            team_events.append({"team_id": team_id, "kind": "base_capture"})
        elif etype == "missiled":
            team_events.append(
                {
                    "team_id": team_id,
                    "kind": "missiled",
                    "hit": meta.get("result") == "hit",
                }
            )
        elif etype == "nuke_cancelled":
            team_events.append({"team_id": team_id, "kind": "nuke_cancelled"})
        elif etype == "special":
            # Only nuke detonations count (activation rows are skipped).
            if ev["points_awarded"] == 500 and "targets" in meta:
                team_events.append({"team_id": team_id, "kind": "nuke_detonation"})

    rows = aggregate_team_stats(team_rounds, team_events, enrolled_teams)
    rows = sort_team_stats(rows, sort, direction)

    context["rows"] = rows
    return render(request, "leagues/team_stats.html", context)
