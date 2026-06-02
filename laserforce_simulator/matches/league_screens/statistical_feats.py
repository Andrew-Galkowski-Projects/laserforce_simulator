"""LG-06e — Statistical Feats league screen (per-game feed).

Read-only, GET-only view rendering ZenGM's per-game model: one sortable row
per (Player, Round) that achieved a feat, showing that round's box-score line +
Opp / Result / Season and deep-linking to the Round, plus a separate Team-feats
section for the comeback-win feat. All feat detection lives in the pure module
``matches/stat_feats.py``; this view materialises the EXTENDED per-(player,
round) seam dicts from the ORM (computing Opp / Result / Season view-side),
applies the LG-06d season scope + LG-06b team filter, calls ``scan_feats``,
sorts view-side, and paginates (LG-06a).

See ``.claude/worktrees/lg-06e-seam-contract.md``.
"""

from __future__ import annotations

from collections import defaultdict

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, render

from matches import stat_feats
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_sort_key,
    _coerce_team_id,
    _resolve_season_scope,
    _season_param,
    _LG01F_PER_PAGE_OPTIONS,
)
from matches.models import GameEvent, GameRound, League, Match, PlayerRoundState
from teams.views import _coerce_dir

# LG-06e — sortable Statistical Feats columns. EVERY column is sortable: the
# descriptor / identity columns plus the 13 box-score columns. Default
# ``round`` desc (most recent first).
_FEATS_SORT_KEYS: frozenset[str] = frozenset(
    {
        # descriptors / identity
        "name",
        "role",
        "team",
        "opp",
        "result",
        "season",
        "round",
        "feat",
        # box-score columns (13)
        "points_scored",
        "mvp",
        "tags_made",
        "times_tagged",
        "accuracy",
        "final_lives",
        "resupplies_given",
        "missiles_landed",
        "specials_used",
        "follow_up_shots",
        "reaction_shots",
        "combo_resupply_count",
        "nuke_detonations",
    }
)

# Ordered (key, label) pairs for the descriptor / identity sort headers (the
# box-score headers render from ``box_score_columns`` below). The Feats column
# sorts on key ``feat``; the Round column on key ``round``.
_FEATS_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("name", "Player"),
    ("role", "Role"),
    ("team", "Team"),
    ("opp", "Opp"),
    ("result", "Result"),
    ("season", "Season"),
    ("feat", "Feats"),
    ("round", "Round"),
)

# Box-score column display spec — single source for the box-score <th>/<td>.
# (key, label, is_float). mvp / accuracy are floats (rendered to one decimal).
_BOX_SCORE_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("points_scored", "Points", False),
    ("mvp", "MVP", True),
    ("tags_made", "Tags", False),
    ("times_tagged", "Tagged", False),
    ("accuracy", "Acc%", True),
    ("final_lives", "Lives", False),
    ("resupplies_given", "Resup", False),
    ("missiles_landed", "Missiles", False),
    ("specials_used", "Specials", False),
    ("follow_up_shots", "Follow-up", False),
    ("reaction_shots", "Reaction", False),
    ("combo_resupply_count", "Combo Resup", False),
    ("nuke_detonations", "Nukes", False),
)


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


def _feat_row_sort_value(row: "stat_feats.FeatRow", key: str):
    """Extract the sort value for ``row`` under sort ``key`` (LG-06e §3.4).

    String descriptor keys sort case-insensitively; ``result`` sorts lexically
    ("L" < "T" < "W"); the box-score keys sort numerically; ``feat`` sorts on a
    stable join of the row's badge kinds. No key may raise on a ``None`` (the
    dataclass defaults descriptors to ``""``).
    """
    if key == "name":
        return row.player_name.lower()
    if key == "role":
        return row.role
    if key == "team":
        return row.team_name.lower()
    if key == "opp":
        return row.opp_team_name.lower()
    if key == "result":
        return row.result
    if key == "season":
        return row.season_name.lower()
    if key == "round":
        return row.round_id
    if key == "feat":
        return ",".join(sorted(b.kind for b in row.feats))
    # Any box-score key.
    return row.stats.get(key, 0.0)


def statistical_feats(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-06e — Statistical Feats per-game feed for a League's Season.

    One sortable row per (Player, Round) that achieved a feat (threshold-cross
    OR season-best), plus a separate Team-feats section (comeback wins). Sortable
    (``?sort=&dir=``), paginated (``?per_page=&page=``), season-scoped
    (``?season=``, LG-06d) and team-filtered (``?team_id=``, LG-06b). Renders an
    empty-state notice when the League has no Season.
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

    # LG-06c — coerce sort/dir before anything else. Default ``round`` desc.
    sort = _coerce_sort_key(request.GET.get("sort"), _FEATS_SORT_KEYS, "round")
    # LG-06e — default ``round`` desc (most recent first). ``_coerce_dir``
    # defaults to ``"asc"``, so pass the ``"desc"`` default explicitly.
    direction = _coerce_dir(request.GET.get("dir"), "desc")
    per_page = _coerce_per_page(request.GET.get("per_page"))

    # LG-06d — season selector.
    seasons, selected_season, season_options, season_filter = _resolve_season_scope(
        request, league, displayed_season
    )

    base_context = {
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": "statistical_feats",
        "feat_rows": [],
        "team_feats": [],
        "box_score_columns": _BOX_SCORE_COLUMNS,
        "sort": sort,
        "dir": direction,
        "sort_keys": _FEATS_SORT_KEYS_DISPLAY,
        "per_page": per_page,
        "per_page_options": _LG01F_PER_PAGE_OPTIONS,
        "page_obj": None,
        "paginator": None,
        "season_options": season_options,
        "selected_season": selected_season,
        "enrolled_teams": [],
        "selected_team_id": None,
        "querystring_without_sort": "",
        "querystring_without_page": "",
        "querystring_without_sort_dir_page": "",
    }

    if season_filter is None:
        return render(request, "leagues/statistical_feats.html", base_context)

    # Re-point the LG-06d scope onto the two join shapes used below: PRS /
    # GameEvent filter via ``game_round__…``; Match filters directly (strip the
    # leading ``match__``).
    prs_filter = {f"game_round__{k}": v for k, v in season_filter.items()}
    match_filter = {k[len("match__") :]: v for k, v in season_filter.items()}

    # LG-06b — team filter.
    enrolled_teams = (
        list(displayed_season.teams.order_by("name"))
        if displayed_season is not None
        else []
    )
    enrolled_ids = {t.id for t in enrolled_teams}
    selected_team_id = _coerce_team_id(request.GET.get("team_id"), enrolled_ids)
    base_context["enrolled_teams"] = enrolled_teams
    base_context["selected_team_id"] = selected_team_id

    # --- Per-Round nuke-detonation counts, keyed (round_id, actor_id) -----
    detonations: dict[tuple[int, int], int] = defaultdict(int)
    events_qs = GameEvent.objects.filter(
        event_type="special",
        points_awarded=500,
        **prs_filter,
    ).only("game_round_id", "actor_id", "event_type", "points_awarded", "metadata")
    for event in events_qs:
        if _is_nuke_detonation(event):
            detonations[(event.game_round_id, event.actor_id)] += 1

    # --- Extended per-(player, round) seam dicts --------------------------
    prs_qs = (
        PlayerRoundState.objects.filter(**prs_filter)
        .select_related(
            "player",
            "game_round",
            "game_round__match",
            "game_round__match__season",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )
    player_rounds: list[dict] = []
    for prs in prs_qs:
        game_round = prs.game_round
        # Own team + opponent team for this Round, from the player's side.
        if prs.team_color == "red":
            own_team = game_round.team_red
            opp_team = game_round.team_blue
            own_points = game_round.red_points
            opp_points = game_round.blue_points
        elif prs.team_color == "blue":
            own_team = game_round.team_blue
            opp_team = game_round.team_red
            own_points = game_round.blue_points
            opp_points = game_round.red_points
        else:
            own_team = None
            opp_team = None
            own_points = 0
            opp_points = 0

        # Per-ROUND result (own vs opp points in THIS GameRound — NOT the Match
        # winner).
        if own_points > opp_points:
            result = "W"
        elif own_points < opp_points:
            result = "L"
        else:
            result = "T"

        # Season (view-computed) from the Round's Match.
        match = game_round.match
        season = match.season if match is not None else None

        player_rounds.append(
            {
                # identity / deep-link
                "round_id": game_round.id,
                "match_id": game_round.match_id,
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "role": prs.role,
                "team_id": own_team.id if own_team is not None else None,
                "team_name": own_team.name if own_team is not None else "",
                # descriptor columns (view-computed)
                "opp_team_name": opp_team.name if opp_team is not None else "",
                "result": result,
                "season_id": season.id if season is not None else None,
                "season_name": season.name if season is not None else "",
                # box-score line (13 BOX_SCORE_KEYS, per-round values)
                "points_scored": prs.points_scored,
                # ``get_mvp`` is a @property (no parens). ``get_accuracy`` is
                # ALSO a @property on PlayerRoundState (verified against
                # matches/models.py) — read WITHOUT parens. See the deviation
                # note in the PR summary: the seam contract pinned a ``()`` call
                # but the model defines get_accuracy as a property, so calling
                # it would raise TypeError.
                "mvp": float(prs.get_mvp),
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "accuracy": float(prs.get_accuracy),
                "final_lives": prs.final_lives,
                "resupplies_given": prs.resupplies_given,
                "missiles_landed": prs.missiles_landed,
                "specials_used": prs.specials_used,
                "follow_up_shots": prs.follow_up_shots,
                "reaction_shots": prs.reaction_shots,
                "combo_resupply_count": prs.combo_resupply_count,
                "nuke_detonations": detonations.get((game_round.id, prs.player_id), 0),
                # predicate-only field (NOT a box-score column)
                "shots_missed": prs.shots_missed,
            }
        )

    # --- Per-Match seam dicts (comeback win) ------------------------------
    round2_by_match: dict[int, int] = {}
    for gr in GameRound.objects.filter(round_number=2, **season_filter).only(
        "id", "match_id"
    ):
        if gr.match_id is not None:
            round2_by_match[gr.match_id] = gr.id

    matches: list[dict] = []
    matches_qs = (
        Match.objects.filter(is_completed=True, **match_filter)
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

    feat_rows, team_feats = stat_feats.scan_feats(player_rounds, matches)

    # LG-06c — view-side sort with the always-appended deterministic secondary
    # tiebreak (round_id desc, player_id asc). Sort by the secondary key first
    # (stable), then by the primary so equal-primary rows keep the secondary
    # order. ``reverse`` only flips the PRIMARY pass.
    feat_rows.sort(key=lambda r: (-r.round_id, r.player_id))
    feat_rows.sort(
        key=lambda r: _feat_row_sort_value(r, sort), reverse=(direction == "desc")
    )

    # LG-06a — sort FIRST (above), THEN paginate.
    paginator = Paginator(feat_rows, per_page)
    page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))

    # Querystring helpers built from COERCED values (LG-00c precedent).
    qs_no_sort = request.GET.copy()
    qs_no_sort.pop("sort", None)
    qs_no_sort.pop("dir", None)
    qs_no_sort["per_page"] = str(per_page)
    qs_no_sort["season"] = _season_param(selected_season)
    if selected_team_id is not None:
        qs_no_sort["team_id"] = str(selected_team_id)
    else:
        qs_no_sort.pop("team_id", None)
    querystring_without_sort = qs_no_sort.urlencode()

    qs_no_page = request.GET.copy()
    qs_no_page.pop("page", None)
    qs_no_page["sort"] = sort
    qs_no_page["dir"] = direction
    qs_no_page["per_page"] = str(per_page)
    qs_no_page["season"] = _season_param(selected_season)
    if selected_team_id is not None:
        qs_no_page["team_id"] = str(selected_team_id)
    else:
        qs_no_page.pop("team_id", None)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    qs_no_sort_dir_page["per_page"] = str(per_page)
    qs_no_sort_dir_page["season"] = _season_param(selected_season)
    if selected_team_id is not None:
        qs_no_sort_dir_page["team_id"] = str(selected_team_id)
    else:
        qs_no_sort_dir_page.pop("team_id", None)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context = dict(base_context)
    context["feat_rows"] = page_obj.object_list
    context["team_feats"] = team_feats
    context["page_obj"] = page_obj
    context["paginator"] = paginator
    context["querystring_without_sort"] = querystring_without_sort
    context["querystring_without_page"] = querystring_without_page
    context["querystring_without_sort_dir_page"] = querystring_without_sort_dir_page
    return render(request, "leagues/statistical_feats.html", context)
