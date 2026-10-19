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

from django.core.paginator import Paginator
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render

from accounts.permissions import get_owned_or_404
from matches.league_screen_services import (
    FEATS_SORT_KEYS as _FEATS_SORT_KEYS,
    build_statistical_feats,
    feat_row_sort_value as _feat_row_sort_value,  # noqa: F401 -- re-export
    feats_enrolled_teams,
    is_nuke_detonation as _is_nuke_detonation,  # noqa: F401 -- re-export
)
from matches.league_views import (
    _build_league_sidebar_links,
    _coerce_page,
    _coerce_per_page,
    _coerce_sort_key,
    _coerce_team_id,
    _lenient_season_scope,
    _resolve_season_scope,
    _season_param,
    _LG01F_PER_PAGE_OPTIONS,
)
from matches.models import League
from teams.views import _coerce_dir

# LG-06e — the sortable Statistical Feats columns (``_FEATS_SORT_KEYS``), the
# nuke-detonation predicate and the sort extractor moved to
# ``matches.league_screen_services`` (ANG-01e T6); re-exported above under
# their old names.

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

    league = get_owned_or_404(League, request, pk=league_id)
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

    # LG-06b — team filter.
    enrolled_teams = feats_enrolled_teams(displayed_season)
    enrolled_ids = {t.id for t in enrolled_teams}
    selected_team_id = _coerce_team_id(request.GET.get("team_id"), enrolled_ids)
    base_context["enrolled_teams"] = enrolled_teams
    base_context["selected_team_id"] = selected_team_id

    # ANG-01e T6 — the feat scan + sort is the shared builder (the API calls
    # it too), fed the same forgiving Season scope ``_resolve_season_scope``
    # flattened above.
    scope = _lenient_season_scope(request, league, displayed_season)
    feat_rows, team_feats = build_statistical_feats(
        scope, team_id=selected_team_id, sort=sort, direction=direction
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
