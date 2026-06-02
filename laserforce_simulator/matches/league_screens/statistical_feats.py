"""LG-01z-q — Statistical Feats league screen.

Read-only, GET-only view listing notable single-game / single-round feats
achieved across the League's displayed Season. All feat detection lives in
the pure module ``matches/stat_feats.py``; this view materialises the seam
dicts (per-player-round + per-match) from the ORM and renders the records.

See ``.claude/worktrees/lg-01z-seam-contract.md`` §2 / §4 entry "q".
"""

from __future__ import annotations

from collections import defaultdict

from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches import stat_feats
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_sort_key,
    _coerce_team_id,
)
from matches.models import GameEvent, GameRound, League, Match, PlayerRoundState
from teams.views import _coerce_dir

# LG-06c — sortable Statistical Feats columns. The Feats surface is a flat
# list of heterogeneous ``FeatRecord``s; sort is over the three record-level
# attributes uniform across all feats. Default ``kind`` asc (groups same-kind
# feats together). Secondary tiebreak: ``feat.label``.
_FEATS_SORT_KEYS: frozenset[str] = frozenset({"kind", "name", "value"})
_FEATS_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("kind", "Feat"),
    ("name", "Who"),
    ("value", "Value"),
)


def _feat_value_sort_key(value: str) -> tuple[int, object]:
    """Numeric-aware sort key for ``FeatRecord.value`` (a string).

    Tries ``float(value)``; on ``ValueError`` falls back to a sentinel that
    sorts non-numeric values together AFTER numerics. The tuple-pair keeps the
    sort total and deterministic so e.g. ``"Comeback"`` never crashes it.
    """
    try:
        return (0, float(value))
    except (TypeError, ValueError):
        return (1, value)


def _feat_sort_value(feat: "stat_feats.FeatRecord", key: str):
    """Sort-value extraction on a ``FeatRecord`` per the LG-06c contract."""
    if key == "kind":
        return feat.kind
    if key == "name":
        return feat.name
    # key == "value" — numeric-aware
    return _feat_value_sort_key(feat.value)


def _is_nuke_detonation(event: GameEvent) -> bool:
    """A `special` GameEvent that is a nuke DETONATION (not activation).

    RV-02 / EventLog: nuke detonation is the ``special`` row carrying
    ``metadata["targets"]`` with ``points_awarded == 500``. The activation
    row (``points_awarded == 0`` + ``metadata["fires_at"]``) is excluded.
    """
    if event.event_type != "special":
        return False
    if event.points_awarded != 500:
        return False
    metadata = event.metadata or {}
    return "targets" in metadata


def statistical_feats(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01z-q — Statistical Feats page for a League's displayed Season.

    Lists notable single-game feats (triple-nuke games, Medic shutouts,
    perfect-accuracy Heavy rounds, single-game MVP / score leaders, tag
    streaks, resupply / missile leaders, comeback wins) across the displayed
    Season's Rounds. Each feat deep-links to its Round. Renders an empty-state
    notice when the League has no Season (or when no feats were detected).
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
        league, displayed_season, sidebar_active="statistical_feats"
    )

    # LG-06c — coerce the sort/dir params before anything else.
    sort = _coerce_sort_key(request.GET.get("sort"), _FEATS_SORT_KEYS, "kind")
    direction = _coerce_dir(request.GET.get("dir"))

    base_context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "statistical_feats",
        "feats": [],
        "enrolled_teams": [],
        "selected_team_id": None,
        "sort": sort,
        "dir": direction,
        "sort_keys": _FEATS_SORT_KEYS_DISPLAY,
        "querystring_without_sort": "",
    }

    if displayed_season is None:
        return render(request, "leagues/statistical_feats.html", base_context)

    # LG-06b — team filter. Enrolled teams (the picker options) + the
    # forgiving ``?team_id=`` coercion against the enrolment set.
    enrolled_teams = list(displayed_season.teams.order_by("name"))
    enrolled_ids = {t.id for t in enrolled_teams}
    selected_team_id = _coerce_team_id(request.GET.get("team_id"), enrolled_ids)
    base_context["enrolled_teams"] = enrolled_teams
    base_context["selected_team_id"] = selected_team_id

    # --- Per-Round nuke-detonation counts, keyed (round_id, actor_id) -----
    detonations: dict[tuple[int, int], int] = defaultdict(int)
    events_qs = GameEvent.objects.filter(
        game_round__match__season=displayed_season,
        event_type="special",
        points_awarded=500,
    ).only("game_round_id", "actor_id", "event_type", "points_awarded", "metadata")
    for event in events_qs:
        if _is_nuke_detonation(event):
            detonations[(event.game_round_id, event.actor_id)] += 1

    # --- Per-player-round seam dicts --------------------------------------
    prs_qs = (
        PlayerRoundState.objects.filter(game_round__match__season=displayed_season)
        .select_related(
            "player",
            "game_round",
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
                "round_id": game_round.id,
                "match_id": game_round.match_id,
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "team_id": team.id if team is not None else None,
                "team_name": team.name if team is not None else "",
                "role": prs.role,
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "shots_missed": prs.shots_missed,
                "points_scored": prs.points_scored,
                "resupplies_given": prs.resupplies_given,
                "missiles_landed": prs.missiles_landed,
                "mvp": prs.get_mvp,
                "nuke_detonations": detonations.get((game_round.id, prs.player_id), 0),
            }
        )

    # --- Per-Match seam dicts (comeback win) ------------------------------
    # Index round-2 GameRound ids per Match for the deep-link anchor.
    round2_by_match: dict[int, int] = {}
    for gr in GameRound.objects.filter(
        match__season=displayed_season, round_number=2
    ).only("id", "match_id"):
        if gr.match_id is not None:
            round2_by_match[gr.match_id] = gr.id

    matches: list[dict] = []
    matches_qs = (
        Match.objects.filter(season=displayed_season, is_completed=True)
        .select_related("team_red", "team_blue", "winner")
        .order_by("id")
    )
    for match in matches_qs:
        matches.append(
            {
                "match_id": match.id,
                "round_id": round2_by_match.get(match.id),
                "winner_team_id": match.winner_id,
                "winner_team_name": (
                    match.winner.name if match.winner is not None else ""
                ),
                "red_team_id": match.team_red_id,
                "blue_team_id": match.team_blue_id,
                "red_round1_points": match.red_round1_points,
                "blue_round1_points": match.blue_round1_points,
            }
        )

    # LG-06b — apply the team filter to both seam inputs before scanning.
    if selected_team_id is not None:
        player_rounds = [
            pr for pr in player_rounds if pr["team_id"] == selected_team_id
        ]
        matches = [
            m
            for m in matches
            if selected_team_id in {m["red_team_id"], m["blue_team_id"]}
        ]

    feats = stat_feats.scan_feats(player_rounds, matches)

    # LG-06c — in-memory sort over the materialised FeatRecord list with
    # ``feat.label`` as the always-appended stable secondary tiebreak.
    feats = sorted(
        feats,
        key=lambda feat: (_feat_sort_value(feat, sort), feat.label),
        reverse=(direction == "desc"),
    )

    # COERCE-BEFORE-QUERYSTRING: header href carry keeps the coerced team_id
    # (re-set) with sort/dir popped, so invalid params never survive.
    qs_no_sort = request.GET.copy()
    qs_no_sort.pop("sort", None)
    qs_no_sort.pop("dir", None)
    if selected_team_id is not None:
        qs_no_sort["team_id"] = str(selected_team_id)
    else:
        qs_no_sort.pop("team_id", None)
    querystring_without_sort = qs_no_sort.urlencode()

    context = dict(base_context)
    context["feats"] = feats
    context["querystring_without_sort"] = querystring_without_sort
    return render(request, "leagues/statistical_feats.html", context)
