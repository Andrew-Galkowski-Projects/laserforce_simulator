"""League / Season views — the LG-01..LG-01f stack.

Extracted from ``matches/views.py`` to keep the league lifecycle (model
list, create flow, dashboards, Play Season POSTs, season standings /
schedule pages, league history) in one file. URL configs in
``matches/league_urls.py`` and ``matches/season_urls.py`` point at the
callables here; URL names are unchanged.
"""

from collections.abc import Callable
from typing import Optional, TypeVar

from celery.result import AsyncResult
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseNotFound,
    JsonResponse,
)
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.permissions import get_owned_or_404, manager_or_none, owned_queryset
from teams.models import Player, Team
from teams.views import _coerce_dir

from . import league_play_services as play_services
from .forms import (
    ConferencePartitionForm,
    CreateLeagueForm,
    MemberNightSetupForm,
    _maps_with_confirmed_config,
)
from .league_play_services import (
    BrokerUnavailableError,
    NoLiveCursorError,
    NoMemberNightGamesError,
    PlayInProgressError,
    SeasonNotActiveError,
    SeasonNotDraftError,
    WrongPhaseError,
)

# ANG-01d-2 T2/T3 — the Start Season / sync play logic, the live cursor +
# play-controls helpers and the FIN-04 per-fixture injury resolver live in
# ``matches.league_play_services`` (shared with the API); re-exported here
# under their old names so existing imports (``tasks.play_season_task``,
# ``core.context_processors``, tests) keep resolving. Patch the service module.
from .league_play_services import (  # noqa: F401 - re-exports
    _INJURY_SLOT_FIELDS,
    _apply_play_hurt,
    _next_matchday_to_play,
    _pick_substitute,
    _resolve_injuries_for_team,
    resolve_injuries_for_fixture,
    restore_after_fixture,
)
from .league_play_services import (  # noqa: F401 - re-exports
    alive_playoff_node as _alive_playoff_node,
)
from .league_play_services import (  # noqa: F401 - re-exports
    resolve_live_cursor as _resolve_live_cursor,
)
from .league_screen_services import (
    SeasonScope,
    resolve_season_scope,
    watched_player_ids,
)
from .league_templates import (
    LEAGUE_TEMPLATES,
    LEAGUE_TEMPLATES_BY_KEY,
)
from .models import (
    GameRound,
    League,
    Match,
    Season,
)

# ANG-01d-1 — the League / Season lifecycle + read helpers now live in
# ``matches.league_services`` (shared with the REST API). Every moved name is
# re-exported here so existing ``from matches.league_views import _x`` callers
# (``league_screens/*``, ``core.context_processors``, tests) keep working.
from .league_services import (  # noqa: F401 - re-exports
    SEED_BUDGET_MAX,
    SEED_BUDGET_MIN,
    SEED_BUDGET_SINGLE,
    STANDINGS_SORT_KEYS,
    WORST_N_ELIGIBLE,
    ManagerFiredError,
    NoCompletedSeasonError,
    SeasonInProgressError,
    _BRACKET_FINALS_FORMATS,
    _KD_ROLE_LABELS,
    _award_round_dict,
    _build_map_config_label,
    _build_team_schedule_rows,
    _classify_playoffs_for_team,
    _coaching_effect_by_team,
    _compute_team_overall,
    _delete_team_if_orphaned,
    _develop_league_for_new_season,
    _developing_players,
    _ensure_owner_evaluations,
    _ensure_team_finances,
    _finals_deciding_node,
    _league_match_q,
    _league_teardown_targets,
    _member_night_appearances_for_team,
    _member_night_game_dict,
    _member_night_games_for_season,
    _pick_manager_team,
    _player_award_labels,
    _playoff_cursor_keys,
    _rank_teams_by_strength,
    _render_fixture_sides,
    _scouting_budget_by_team,
    _season_finals_mvp,
    _season_regular_round_dicts,
    _seed_team_budgets_by_strength,
    _standings_row_attr,
    _standings_sort_value,
    _streak_sort_value,
    _team_winp_for_season,
    _team_wins_games_for_season,
    _validate_conference_partition,
    _write_baseline_ratings,
    build_conferences_state,
    build_dashboard_context,
    build_history_row,
    build_season_awards,
    build_season_schedule,
    build_season_standings,
    build_team_schedule,
    compute_season_award_set,
    create_league_and_season,
    eligible_new_teams,
    ensure_owner_evaluation,
    history_seasons,
    is_career_league,
    latest_completed_season,
    pick_displayed_season,
    manager_awaiting_reassignment,
    reassign_and_roll,
    replace_conferences,
    run_season_rollover,
    season_mode_for,
    start_next_season,
    teardown_league,
    template_to_form_data,
)

# Back-compat aliases for the helpers renamed by the move (contract §2.3).
_template_to_form_data = template_to_form_data
_create_league_and_season = create_league_and_season
_is_career_league = is_career_league
_run_season_rollover = run_season_rollover
_eligible_new_teams = eligible_new_teams
_teardown_league = teardown_league
_build_dashboard_context = build_dashboard_context
_pick_displayed_season = pick_displayed_season
_compute_season_award_set = compute_season_award_set
_build_history_row = build_history_row
_STANDINGS_SORT_KEYS = STANDINGS_SORT_KEYS

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


def season_awards(request, season_id: int) -> HttpResponse:
    """LG-03 — Season-end awards page.

    Read-only, GET-only. Recomputes the Season's award set on render (no
    persisted award rows) from the frozen regular-season ``PlayerRoundState``
    corpus plus, for a bracket-format playoff phase, the Finals MVP. Renders
    an empty notice when the Season has no completed regular-season rounds.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    league = season.league
    displayed_season = season
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active=None
    )

    # ANG-01d-1 — the award computation is shared with the API.
    body = build_season_awards(season)

    context = {
        "season": season,
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
        "awards": body["awards"],
        "kd_rows": body["kd_rows"],
        "has_rounds": body["has_rounds"],
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
    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    # LG-06g — view-side sort params (shared across every Conference group).
    # ``rank`` is left frozen (we never renumber it); a no-``?sort`` request
    # resolves to ``("rank", "asc")`` so the page renders in standings order.
    sort = _coerce_sort_key(request.GET.get("sort"), _STANDINGS_SORT_KEYS, "rank")
    direction = _coerce_dir(request.GET.get("dir"))

    qs_no_sort_dir = request.GET.copy()
    qs_no_sort_dir.pop("sort", None)
    qs_no_sort_dir.pop("dir", None)
    querystring_without_sort_dir = qs_no_sort_dir.urlencode()

    # ANG-01d-1 — the per-Conference standings computation is shared with the
    # API (``matches.league_services.build_season_standings``).
    body = build_season_standings(season, sort=sort, direction=direction)
    is_draft_preview = body["is_draft_preview"]
    standings_groups = body["standings_groups"]
    rows = body["rows"]
    rows_with_teams = body["rows_with_teams"]

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
        # CONF-01 — per-Conference standings groups (one group with
        # conference=None for a zero-Conference Season).
        "standings_groups": standings_groups,
        "is_draft_preview": is_draft_preview,
        "sidebar_active": "standings",
        "sidebar_links": sidebar_links,
        "sort": sort,
        "dir": direction,
        "sort_keys": _STANDINGS_SORT_KEYS_DISPLAY,
        "querystring_without_sort_dir": querystring_without_sort_dir,
        # UXP-08 — highlight the Manager's row.
        "managed_team_id": _managed_team_id(league),
    }
    return render(request, "seasons/standings.html", context)


def _managed_team_id(league: League) -> int | None:
    """UXP-08 — the Manager's Team id in a career (``mode="league"``) League.

    ``None`` for sandbox / multiplayer Leagues or when no Team is managed.
    """
    if league.mode != "league":
        return None
    return league.current_team_id


# ----------------------------------------------------------------------
# CONF-05 — draft-Season Conference composer (Manage Conferences page).
# Builds on the CONF-01 Conference model (ADR-0034); no model/migration.
# ----------------------------------------------------------------------


def _manage_conferences_context(
    season: Season,
    teams: "list",
    is_editable: bool,
    *,
    submitted_names: "list[str] | None" = None,
    submitted_assignments: "dict[int, int | None] | None" = None,
    submitted_rotations: "list[str] | None" = None,
    errors: "list[str] | None" = None,
) -> dict:
    """Build the Manage-Conferences template context (shared GET + error
    re-render). Pre-fills the composer from the submitted values on a failed
    POST, else from the Season's existing Conference partition.

    CONF-06 — also carries the per-Conference arena-map rotation: ``conf_rows``
    (index-aligned with ``conf_names``, each ``rotation`` a comma-joined id
    string for the hidden input) and ``confirmed_maps`` (the picker's allowed
    maps). The read-only branch resolves the frozen snapshot to author-ordered
    map names.
    """
    confs = season.ordered_conferences()
    if submitted_names is not None:
        conf_names = submitted_names
        assignments = submitted_assignments or {}
    else:
        conf_names = [c.name for c in confs]
        idx_by_team: dict[int, int] = {}
        for i, conf in enumerate(confs):
            for team in conf.teams.all():
                idx_by_team[team.id] = i
        assignments = {t.id: idx_by_team.get(t.id) for t in teams}

    # CONF-06 — the rotation string for row ``i``: the raw resubmitted value on
    # a failed POST (so the user does not lose their picks), else the stored
    # live list. A missing index (a row added client-side without a rotation)
    # falls back to "".
    confirmed_maps = list(_maps_with_confirmed_config())
    if submitted_rotations is not None:
        rotation_strings = list(submitted_rotations)
    else:
        rotation_strings = [
            ",".join(str(i) for i in (c.map_rotation_ids_json or [])) for c in confs
        ]
    conf_rows = [
        {
            "name": name,
            "rotation": (rotation_strings[i] if i < len(rotation_strings) else ""),
        }
        for i, name in enumerate(conf_names)
    ]

    team_rows = [{"team": t, "selected": assignments.get(t.id)} for t in teams]
    # CONF-06 — resolve rotation ids to names for the read-only branch. Read
    # through ArenaMap (NOT ``confirmed_maps``) so a snapshotted map still
    # displays after its zone config is un-confirmed; only a genuinely deleted
    # row drops out. One query, on the union of every Conference's snapshot.
    from core.models import ArenaMap

    snapshot_by_conf = {
        c.id: list(
            c.starting_map_rotation_ids_json
            if c.starting_map_rotation_ids_json is not None
            else (c.map_rotation_ids_json or [])
        )
        for c in confs
    }
    map_name_by_id = {
        m.id: m.name
        for m in ArenaMap.objects.in_bulk(
            [i for ids in snapshot_by_conf.values() for i in ids]
        ).values()
    }
    readonly_groups = [
        {
            "conference": c,
            "teams": sorted(c.teams.all(), key=lambda t: t.name),
            # CONF-06 — author-ordered map names off the activation snapshot
            # (falling back to the live list when the Season never activated).
            # Ids whose ArenaMap was deleted drop out silently.
            "rotation_names": [
                map_name_by_id[i]
                for i in snapshot_by_conf.get(c.id, [])
                if i in map_name_by_id
            ],
        }
        for c in confs
    ]
    league = season.league
    sidebar_displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(league, sidebar_displayed_season, None)
    return {
        "season": season,
        "teams": teams,
        "team_rows": team_rows,
        "conf_names": conf_names,
        "conf_rows": conf_rows,
        "confirmed_maps": confirmed_maps,
        "map_mode": season.map_mode,
        "readonly_groups": readonly_groups,
        "is_editable": is_editable,
        "errors": errors or [],
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
    }


def manage_conferences(request: HttpRequest, season_id: int) -> HttpResponse:
    """CONF-05 — the draft-Season Conference composer.

    GET renders an editable composer while the Season is ``draft`` (name
    Conferences + assign each enrolled Team) or a read-only frozen partition once
    the Season is active/completed (membership is snapshotted at Start Season).
    POST (draft only) validates + **replaces** the Season's Conferences;
    Conferences are OPTIONAL — an empty submission clears them (flat Season). A
    non-draft POST is rejected (the partition is frozen).
    """
    if request.method not in ("GET", "POST"):
        return HttpResponseNotAllowed(["GET", "POST"])
    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    is_editable = season.state == "draft"
    teams = sorted(season.teams.all(), key=lambda t: t.name)

    if request.method == "POST":
        if not is_editable:
            return HttpResponseBadRequest(
                "Conferences can only be edited while the Season is in draft."
            )
        names = request.POST.getlist("conference_name")
        # CONF-06 — one hidden ``conference_rotation`` input per
        # ``.conference-row``, so index i here aligns with index i of ``names``
        # (browsers submit both in document order). Missing index ⇒ "".
        rotations = request.POST.getlist("conference_rotation")
        team_to_conf_idx: dict[int, int | None] = {}
        for team in teams:
            raw = request.POST.get(f"team_{team.id}_conference", "")
            team_to_conf_idx[team.id] = int(raw) if raw.isdigit() else None
        # ANG-01d-1 — the indexed POST is reshaped into the shared
        # ``ConferencePartitionForm`` payload; the form runs today's partition
        # (incl. the CONF-02 bracket floor), rotation (CONF-06) and
        # rotate-by-conference guard checks in the same order.
        form = ConferencePartitionForm(
            data={
                "conferences": [
                    {
                        "name": names[i],
                        "team_ids": [
                            t.id for t in teams if team_to_conf_idx.get(t.id) == i
                        ],
                        "map_rotation_ids": (
                            rotations[i] if i < len(rotations) else ""
                        ),
                    }
                    for i in range(len(names))
                ]
            },
            season=season,
        )
        if not form.is_valid():
            errors = [
                m for e in form.errors.as_data()["conferences"] for m in e.messages
            ]
            context = _manage_conferences_context(
                season,
                teams,
                is_editable,
                submitted_names=[n.strip() for n in names],
                submitted_assignments=team_to_conf_idx,
                submitted_rotations=[r for r in rotations],
                errors=errors,
            )
            return render(request, "seasons/manage_conferences.html", context)
        replace_conferences(season, form.cleaned_data["conferences"])
        messages.success(request, "Conferences saved.")
        return redirect("manage_conferences", season_id=season.id)

    context = _manage_conferences_context(season, teams, is_editable)
    return render(request, "seasons/manage_conferences.html", context)


def season_schedule(request, season_id: int) -> HttpResponse:
    """LG-01 — Schedule page for a Season.

    Renders the deterministic fixture list from
    ``Season.scheduled_fixtures()`` with persisted ``GameRound``s
    overlaid. Fixtures are grouped by
    matchday; the display date for matchday ``n`` is
    ``season.start_date + (n - 1) * 7 days``.
    """
    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    league = season.league
    sidebar_displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, sidebar_displayed_season, "schedule"
    )

    # ANG-01d-1 — fixtures + played-Round overlay + member nights are built by
    # the shared ``build_season_schedule`` (``< 2`` teams ⇒ no matchdays).
    body = build_season_schedule(season)

    context = {
        "season": season,
        "matchdays": body["matchdays"],
        "member_night_games": body["member_night_games"],
        "sidebar_active": "schedule",
        "sidebar_links": sidebar_links,
    }
    return render(request, "seasons/schedule.html", context)


def league_list(request) -> HttpResponse:
    """LG-01a — flat list of all Leagues (active + archived sections)."""
    active_leagues = list(
        owned_queryset(League.objects.filter(state="active"), request.user).order_by(
            "-id"
        )
    )
    archived_leagues = list(
        owned_queryset(League.objects.filter(state="archived"), request.user).order_by(
            "-id"
        )
    )
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


def _redirect_after_create(form: CreateLeagueForm, season: Season) -> HttpResponse:
    """CRE-01 + CONF-05 — the shared post-create redirect for both create views.

    CONF-05: when the form requested Conferences, ``_create_league_and_season``
    has already pre-created them, so send the user to the Manage Conferences
    composer to rename / reassign before Start Season. Otherwise (UXP-08) land
    on the League dashboard, which carries the draft Start Season call-to-action.
    """
    if form.cleaned_data.get("number_of_conferences") or 0:
        return redirect("manage_conferences", season_id=season.id)
    return redirect("league_dashboard", league_id=season.league_id)


def league_create(request) -> HttpResponse:
    """CRE-01 — League template chooser (the new default at ``create/``).

    GET renders the template chooser. POST resolves the chosen
    ``LeagueTemplate``, builds ``CreateLeagueForm(data=_template_to_form_data(
    ...))``, validates, and on valid calls ``_create_league_and_season(form)``
    → redirect ``league_dashboard``. An unknown template key or an invalid form
    re-renders the chooser.
    """
    if request.method == "POST":
        template = LEAGUE_TEMPLATES_BY_KEY.get(request.POST.get("template", ""))
        if template is None:
            return render(
                request,
                "leagues/create.html",
                {
                    "form": CreateLeagueForm(),
                    "templates": LEAGUE_TEMPLATES,
                    "error": "Please choose a valid league template.",
                },
            )
        form = CreateLeagueForm(
            data=_template_to_form_data(
                template,
                league_name=request.POST.get("league_name", ""),
                difficulty=request.POST.get("difficulty", "medium"),
            )
        )
        if form.is_valid():
            season = _create_league_and_season(form, manager=manager_or_none(request))
            return _redirect_after_create(form, season)
        return render(
            request,
            "leagues/create.html",
            {
                "form": form,
                "templates": LEAGUE_TEMPLATES,
                "error": "Could not create the league. Please check your inputs.",
            },
        )

    return render(
        request,
        "leagues/create.html",
        {"form": CreateLeagueForm(), "templates": LEAGUE_TEMPLATES},
    )


def league_create_advanced(request) -> HttpResponse:
    """CRE-01 — Advanced create-League screen (the relocated full form).

    GET renders the full form; POST validates ``CreateLeagueForm(request.POST)``
    and on valid calls ``_create_league_and_season(form)`` → redirect
    ``league_dashboard``; invalid re-renders the full form.
    """
    if request.method != "POST":
        return render(
            request,
            "leagues/create_advanced.html",
            {"form": CreateLeagueForm()},
        )

    form = CreateLeagueForm(request.POST)
    if not form.is_valid():
        return render(request, "leagues/create_advanced.html", {"form": form})

    season = _create_league_and_season(form, manager=manager_or_none(request))
    return _redirect_after_create(form, season)


# ---------------------------------------------------------------------------
# LG-01f — League history + sidebar helpers
# ---------------------------------------------------------------------------


_LG01F_PER_PAGE_OPTIONS: tuple[int, ...] = (10, 25, 50, 100)


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


def _lenient_season_scope(
    request: HttpRequest, league: League, displayed_season: Season | None
) -> SeasonScope:
    """ANG-01e -- ``resolve_season_scope`` over ``?season=``, HTML-forgiving.

    A value the service rejects (``ValueError``) falls back to the default
    scope (``displayed_season``), as the HTML pages always have.
    """
    try:
        return resolve_season_scope(league, displayed_season, request.GET.get("season"))
    except ValueError:
        return resolve_season_scope(league, displayed_season, None)


def _resolve_season_scope(
    request: HttpRequest, league: League, displayed_season: Season | None
) -> tuple[list[Season], int | str | None, list[dict], dict | None]:
    """LG-06d -- the HTML wrapper over ``league_screen_services.resolve_season_scope``.

    Reads ``?season=`` forgivingly: a value the service rejects falls back to
    ``displayed_season`` (only the API is strict). Returns the legacy 4-tuple
    ``(seasons, selected_season, season_options, season_filter)``:

    - ``seasons`` -- ``list[Season]`` newest-first.
    - ``selected_season`` -- ``"career"`` | ``int`` Season id | ``None`` (the
      empty-state, when the League has no Season).
    - ``season_options`` -- ``list[dict]`` of ``{"id", "name", "year"}``
      newest-first; the template appends the "Career" entry.
    - ``season_filter`` -- the scope's ``round_filter``: ``{"match__season__league":
      league}`` for Career, ``{"match__season": <Season>}`` for a single
      Season, or ``None`` in the empty-state.
    """
    scope = _lenient_season_scope(request, league, displayed_season)
    season_options = [
        {
            "id": s.id,
            "name": s.name,
            "year": s.start_date.year if s.start_date is not None else None,
        }
        for s in scope.options
    ]
    return scope.seasons, scope.selected_id, season_options, scope.round_filter


def _watched_player_ids(request: HttpRequest, league_id: int) -> set[int]:
    """LG-06f / ANG-01e -- the requesting Account's watched Player ids for a League.

    Thin wrapper over ``league_screen_services.watched_player_ids`` (the
    browser session is no longer read); anonymous => ``set()``.
    """
    return watched_player_ids(getattr(request, "user", None), league_id)


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
        ("league", "finances", "Finances", _cs("league_finances")),
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
        ("team", "finances_team", "Finances", _cs("team_finances")),
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


def league_history(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01f — League History page.

    Read-only paginated table of every Season in ``league_id``. The
    in-progress Season (if any) is pinned at the top of the table with
    an "In progress" badge in the Champion cell and live standings in
    the top-3 cells. Completed Seasons paginate newest-first by id.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)

    # ANG-01d-1 — the Season / Team query block is shared with the API.
    in_progress_season, completed_seasons, teams_by_id = history_seasons(league)

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

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    displayed_season = _pick_displayed_season(league)
    season_mode = season_mode_for(displayed_season)

    body = _build_dashboard_context(displayed_season, season_mode)
    sidebar_links = _build_league_sidebar_links(league, displayed_season, "dashboard")
    managed_team_id = _managed_team_id(league)
    context = {
        "league": league,
        **body,
        "sidebar_active": "dashboard",
        "sidebar_links": sidebar_links,
        # UXP-08 — onboarding: "You manage <Team>" + row highlight, and the
        # past-evaluations link only once an owner evaluation exists.
        "managed_team_id": managed_team_id,
        "managed_team": league.current_team if managed_team_id else None,
        "has_owner_evaluations": league.owner_evaluations.exists(),
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

    season = get_owned_or_404(Season, request, pk=season_id)
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
        # ANG-01d-2 — a sync play view refused by a live run flashes here.
        "play_error": request.session.pop("play_error", None),
        "play_job_id": None,
    }
    return render(request, "seasons/dashboard.html", context)


# ---------------------------------------------------------------------------
# LG-01d — Play Season views + helper
# ---------------------------------------------------------------------------


def _render_season_dashboard_error(
    request: HttpRequest, season: Season, play_error: str, *, status: int = 400
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
    return render(request, "seasons/dashboard.html", context, status=status)


def start_season(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the ``draft → active`` transition.

    POST only. ANG-01d-2 — delegates to ``league_play_services.start_season``:
    a non-draft Season (active or completed) keeps the idempotent double-submit
    302 to the dashboard but is never re-snapshotted or reopened. A
    ``ValidationError`` whose message contains ``"non-completed"`` (the LG-01
    ``Season.clean()`` wording) also redirects; any other ``ValidationError``
    re-renders the Season dashboard with ``play_error`` populated and HTTP 400.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        play_services.start_season(season)
    except SeasonNotDraftError:
        return redirect("season_dashboard", season_id=season.id)
    except ValidationError as exc:
        messages_list = getattr(exc, "messages", None) or [str(exc)]
        joined = " ".join(messages_list)
        season.refresh_from_db()
        if "non-completed" in joined or season.state == "active":
            return redirect("season_dashboard", season_id=season.id)
        return _render_season_dashboard_error(request, season, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def _redirect_with_play_error(season: Season, request, message: str) -> HttpResponse:
    """ANG-01d-2 — redirect a sync play view back to the dashboard, flashing
    ``message`` into the dashboard's ``play_error`` banner (popped on render)."""
    request.session["play_error"] = message
    return redirect("season_dashboard", season_id=season.id)


def play_week(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for Play One Week (one matchday).

    Delegates to ``league_play_services.play_week`` (whole-matchday atomic, or
    one bracket stage on a playoff cursor). On a Season already finished ⇒
    idempotent 302 redirect. On a non-``active`` Season ⇒ 400 + ``play_error``.
    ANG-01d-2 — while an Active play run is live ⇒ 302 back to the dashboard
    with a ``play_error``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        play_services.play_week(season)
    except SeasonNotActiveError as exc:
        return _render_season_dashboard_error(request, season, str(exc))
    except PlayInProgressError as exc:
        return _redirect_with_play_error(season, request, str(exc))
    except (ValidationError, ValueError) as exc:
        return _render_season_dashboard_error(request, season, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def _play_in_progress_response(exc: PlayInProgressError) -> JsonResponse:
    """ANG-01d-2 — the HTML async endpoints' 409 while a run is live."""
    return JsonResponse(
        {"error": str(exc), "code": "play_in_progress", "job_id": exc.job_id},
        status=409,
    )


def _broker_unavailable_response(exc: BrokerUnavailableError) -> JsonResponse:
    """L-6 — the HTML async endpoints' 503 when the Celery broker is down
    (the ``tournament_play_all`` JSON shape)."""
    return JsonResponse({"error": str(exc)}, status=503)


def _play_season_run_view(
    request: HttpRequest, season_id: int, max_matchdays: int | None
) -> HttpResponse:
    """LG-01d — shared body of the two ``play_season_task`` async endpoints.

    Non-active ⇒ 400 + ``play_error``; a live run ⇒ 409 ``play_in_progress``;
    else enqueue through ``enqueue_play_run`` and return 202
    ``{"job_id", "season_id"}``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        job_id = play_services.play_season_run(season, max_matchdays=max_matchdays)
    except SeasonNotActiveError as exc:
        return _render_season_dashboard_error(request, season, str(exc))
    except PlayInProgressError as exc:
        return _play_in_progress_response(exc)
    except BrokerUnavailableError as exc:
        return _broker_unavailable_response(exc)
    return JsonResponse({"job_id": job_id, "season_id": season.id}, status=202)


def play_two_months(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the Play Two Months async run (8 matchdays)."""
    return _play_season_run_view(request, season_id, 8)


def play_until_end(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the Play Until End of Season async run."""
    return _play_season_run_view(request, season_id, None)


def play_single_round(request, season_id: int) -> HttpResponse:
    """LG-02-Part2c-1 — POST entry point for Play Single Round (one playoff
    Match).

    Sync. POST only (405 on GET). Delegates to
    ``league_play_services.play_single_round``. A cursor that is not on a built
    + active tournament phase re-renders the Season dashboard with a
    ``play_error`` (HTTP 400); ANG-01d-2 — so does a non-active Season, and a
    live Active play run redirects back with a ``play_error``. 302 redirect to
    the dashboard on success.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        play_services.play_single_round(season)
    except (SeasonNotActiveError, WrongPhaseError) as exc:
        return _render_season_dashboard_error(request, season, str(exc))
    except PlayInProgressError as exc:
        return _redirect_with_play_error(season, request, str(exc))
    return redirect("season_dashboard", season_id=season.id)


def play_playoffs(request, season_id: int) -> JsonResponse:
    """LG-02-Part2c-1 — POST entry point for the Play Playoffs async run.

    POST only (405 on GET). Requires the cursor be on a built + active
    tournament phase; otherwise 409 JSON ``{"error": ...}``. A live run ⇒ 409
    ``play_in_progress``. Happy path enqueues ``play_playoffs_task`` through
    ``enqueue_play_run`` and returns ``JsonResponse({"job_id", "season_id"},
    status=202)``. The CONF-04 Worlds build-before-cursor-read and the CONF-02
    ``tournaments_for_phase`` guard live in ``play_playoffs_run``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        job_id = play_services.play_playoffs_run(season)
    except (SeasonNotActiveError, WrongPhaseError):
        return JsonResponse({"error": "No active playoff bracket to play."}, status=409)
    except PlayInProgressError as exc:
        return _play_in_progress_response(exc)
    except BrokerUnavailableError as exc:
        return _broker_unavailable_response(exc)
    return JsonResponse({"job_id": job_id, "season_id": season.id}, status=202)


def play_cancel(request, season_id: int) -> JsonResponse:
    """PLAY-01 — POST-only cooperative cancel for an in-flight async run.

    ANG-01d-2 — sets ``Season.play_cancel_requested`` only while an Active
    play run is live (``request_play_cancel``) and returns
    ``JsonResponse({"cancelled": <bool>, "season_id": ...})`` (HTTP 200).
    ``cancelled`` is ``False`` and nothing is written when no run is live, so
    a late click cannot leave a stale flag that kills the next run. The task
    reads the flag (top + between fixtures), stops cleanly, and returns
    NORMALLY (Celery SUCCESS, mapped to ``"complete"``).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    cancelled = play_services.request_play_cancel(season)
    return JsonResponse({"cancelled": cancelled, "season_id": season.id})


def member_night_setup(request, season_id: int) -> HttpResponse:
    """LG-07a — POST-only sync setup for a member night (ADR-0033).

    Draws the member-night games for the chosen Sites (a multi-valued ``sites``
    POST field — the checked per-Site toggle boxes; ≥1 required), creates the
    two ``is_draw_team`` Teams per game (roles fixed once here onto the drawn
    Teams' ``slot_*`` FKs from ``MemberNightGame.team_a`` / ``team_b``) plus the
    unplayed ``Match`` shell stamped ``season`` + ``season_phase`` (the
    build-then-drain race-free step), then 302-redirects to the dashboard. The
    draw consumes a FRESH ``random.Random()`` (non-deterministic by design,
    outside the SIM-07/08 seed chain). Re-running setup APPENDS more games.

    ANG-01d-2 — binds ``MemberNightSetupForm`` and delegates to
    ``league_play_services.setup_member_night``. A non-active Season, a cursor
    not on a member night, or an invalid ``sites`` selection re-renders the 400
    dashboard; a live Active play run redirects back with a ``play_error``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        # The state guards run before the Form (the API's order too).
        play_services.require_member_night(
            season, play_services.NO_MEMBER_NIGHT_TO_SET_UP
        )
        form = MemberNightSetupForm(request.POST, season=season)
        if not form.is_valid():
            return _render_season_dashboard_error(
                request, season, form.errors["sites"][0]
            )
        play_services.setup_member_night(
            season, form.cleaned_data["sites"], manager=manager_or_none(request)
        )
    except (SeasonNotActiveError, WrongPhaseError) as exc:
        return _render_season_dashboard_error(request, season, str(exc))
    except PlayInProgressError as exc:
        return _redirect_with_play_error(season, request, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def play_member_night(request, season_id: int) -> JsonResponse:
    """LG-07a — POST-only async drain of the member-night game shells.

    Requires the cursor be on a member_night phase with >= 1 unplayed
    member-night Match; otherwise 409 JSON ``{"error": ...}`` (the
    ``play_playoffs`` async-guard precedent). Happy path starts
    ``play_member_night_task`` through ``enqueue_play_run`` (ANG-01d-2) and
    returns ``JsonResponse({"job_id", "season_id"}, status=202)``. A non-active
    Season re-renders the 400 dashboard; a live run is a 409 ``play_in_progress``.
    Polling REUSES the ``play_status`` view verbatim.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        job_id = play_services.play_member_night_run(season)
    except SeasonNotActiveError as exc:
        return _render_season_dashboard_error(request, season, str(exc))
    except PlayInProgressError as exc:
        return _play_in_progress_response(exc)
    except (WrongPhaseError, NoMemberNightGamesError):
        return JsonResponse({"error": "No member night to play."}, status=409)
    except BrokerUnavailableError as exc:
        return _broker_unavailable_response(exc)
    return JsonResponse({"job_id": job_id, "season_id": season.id}, status=202)


_T = TypeVar("_T")


def _member_night_play_guarded(
    request: HttpRequest, season: Season, play: Callable[[Season], _T]
) -> tuple[_T | None, HttpResponse | None]:
    """Run a synchronous member-night play service on ``season``.

    Returns ``(result, None)``, or ``(None, error_response)``: a non-active
    Season / no member night / nothing unplayed re-renders the 400 dashboard; a
    live Active play run redirects back with a ``play_error``.
    """
    try:
        return play(season), None
    except (SeasonNotActiveError, WrongPhaseError, NoMemberNightGamesError) as exc:
        return None, _render_season_dashboard_error(request, season, str(exc))
    except PlayInProgressError as exc:
        return None, _redirect_with_play_error(season, request, str(exc))


def play_member_night_single(request, season_id: int) -> HttpResponse:
    """LG-07a — POST-only SYNC play of ONE member-night game (ADR-0033).

    Drains the first unplayed member-night ``Match`` shell (both Rounds via
    ``simulate_scheduled_round(..., season_phase=<mn phase>)``), then
    ``complete_if_finished`` and 302-redirects to the dashboard. The analogue of
    ``play_single_round`` for the playoff bracket. ``fidelity`` stays the default
    (no replay needed for the non-live single).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    _result, error = _member_night_play_guarded(
        request, season, play_services.play_member_night_single
    )
    if error is not None:
        return error
    return redirect("season_dashboard", season_id=season.id)


def play_member_night_live(request, season_id: int) -> HttpResponse:
    """LG-07a — POST-only SYNC play of ONE member-night game + a live replay.

    Drains the first unplayed member-night ``Match`` shell at ``fidelity="full"``
    (so the persisted event log can drive the SIM-05 replay — GEN-01), stashes
    the two committed Round ids in the ``live_watch`` session handoff (the LG-01i
    pattern, ``kind="member_night"``), and redirects to ``play_week_live_watch``
    — reusing the existing watch view/template verbatim (it renders a 2-Round
    game from ``round_ids``; ``kind`` is cosmetic).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    result, error = _member_night_play_guarded(
        request, season, play_services.play_member_night_live
    )
    if error is not None:
        return error
    request.session["live_watch"] = {
        "season_id": season.id,
        "kind": result.kind,
        "round_ids": result.round_ids,
    }
    request.session.modified = True
    return redirect("play_week_live_watch", season_id=season.id)


def _build_play_status_response(
    async_result: AsyncResult,
    *,
    season_id: int,
    season: Optional[Season] = None,
) -> dict:
    """Assemble the polling JSON for a Play Season job.

    The LG-01d 5 keys (``status`` / ``completed`` / ``total`` / ``error`` /
    ``season_id``) are unchanged. PLAY-01 ADDS:

    * ``standings`` — server-rendered live partial-standings HTML fragment.
    * ``leaders`` — ``{"points": <html>, "tags": <html>, "ratio": <html>}``.
    * ``cancelled`` — ``True`` when the task returned ``cancelled: true`` on
      SUCCESS; else ``False``. Status stays ``"complete"`` either way.

    The live standings / leaders recompute VIEW-SIDE from committed rows each
    poll (NOT from Celery meta), via the same helpers ``_build_dashboard_context``
    uses, then render the shared dashboard snippet partials. Computed on EVERY
    poll (running OR complete). ``season`` is resolved by the caller and passed
    in; when absent (defensive) the live fragments fall back to empty strings.
    """
    # ANG-01d-2 — the Celery-state mapping is shared with the API's structured
    # Job poll (``build_play_job_status``).
    job = play_services.job_status_fields(async_result)

    panels = _render_live_play_panels(season)

    return {
        "status": job["status"],
        "completed": job["completed"],
        "total": job["total"],
        "error": job["error"],
        "season_id": season_id,
        "standings": panels["standings"],
        "leaders": panels["leaders"],
        # PLAY-01 (LG-07 extension) — the Next round + Rounds-played panels also
        # update per poll (they change per Round, unlike Match-keyed standings).
        "next_round": panels["next_round"],
        "round_count": panels["round_count"],
        "cancelled": job["cancelled"],
    }


def _render_live_play_panels(season: Optional[Season]) -> dict:
    """PLAY-01 — recompute + render the live dashboard panel fragments.

    Recomputes from committed rows via ``_build_dashboard_context`` (the same
    ``compute_standings`` / ``compute_leaders`` / ``find_next_fixture`` /
    ``round_progress`` source the dashboards use), then server-renders the shared
    dashboard snippet partials. Returns a dict with ``standings`` (str),
    ``leaders`` (``{"points", "tags", "ratio"}``), ``next_round`` (str), and
    ``round_count`` (str). ``season is None`` ⇒ empty fragments (the JS patch is
    existence-guarded and no-ops off-dashboard).
    """
    from django.template.loader import render_to_string

    empty = {
        "standings": "",
        "leaders": {"points": "", "tags": "", "ratio": ""},
        "next_round": "",
        "round_count": "",
    }
    if season is None:
        return empty

    body = _build_dashboard_context(season, season.state)
    standings_html = render_to_string(
        "_partials/dashboard_standings_snippet.html",
        {"standings_snippet": body["standings_snippet"]},
    )
    leaders = {
        "points": render_to_string(
            "_partials/dashboard_leaders_snippet.html",
            {"leaders": body["leaders_points"], "leaders_title": "Points per game"},
        ),
        "tags": render_to_string(
            "_partials/dashboard_leaders_snippet.html",
            {"leaders": body["leaders_tags"], "leaders_title": "Tags per game"},
        ),
        "ratio": render_to_string(
            "_partials/dashboard_leaders_snippet.html",
            {"leaders": body["leaders_ratio"], "leaders_title": "Tag ratio"},
        ),
    }
    next_round_html = render_to_string(
        "_partials/dashboard_next_round.html",
        {"next_fixture": body["next_fixture"]},
    )
    round_count_html = render_to_string(
        "_partials/dashboard_round_count.html",
        {
            "round_count_completed": body["round_count_completed"],
            "round_count_total": body["round_count_total"],
        },
    )
    return {
        "standings": standings_html,
        "leaders": leaders,
        "next_round": next_round_html,
        "round_count": round_count_html,
    }


def play_status(request, season_id: int, job_id: str) -> JsonResponse:
    """LG-01d — Shared polling endpoint for both async play tasks.

    GET only. Returns the locked 5-key polling JSON. The URL kwarg
    ``season_id`` is authoritative; any ``?season_id=`` query param is
    the carry pattern but the URL kwarg wins on disagreement.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_owned_or_404(Season, request, pk=season_id)
    # Only write when the value actually changes. play_status is polled every
    # ~0.5s for the whole "Play …" run; an unconditional assignment marks the
    # session modified on every poll, forcing a django_session write that
    # competes with the play loop's per-Round write transactions and triggers
    # "database is locked" on SQLite. The guard keeps last_league_id fresh
    # (first poll writes it) without writing on every subsequent poll.
    if request.session.get("last_league_id") != season.league_id:
        request.session["last_league_id"] = season.league_id

    async_result = AsyncResult(job_id)
    return JsonResponse(
        _build_play_status_response(async_result, season_id=season_id, season=season)
    )


# ---------------------------------------------------------------------------
# LG-01i — "One Week (Live)" play-now-and-watch
# ---------------------------------------------------------------------------


def play_week_live(request, season_id: int) -> HttpResponse:
    """POST: play (commit) the manager's game for this week NOW, kick off the
    rest of the matchday / bracket stage in the BACKGROUND, then redirect to the
    live-watch page. Results are final the moment it runs -- there is no preview
    and no discard.

    Delegates to ``league_play_services.play_live``. 405 on non-POST; 400
    dashboard re-render on a non-active Season, no live game, or a simulator
    error; a live play run ⇒ 302 back to the dashboard with a ``play_error``;
    a manager bye just runs the week in the background and returns to the
    dashboard.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    try:
        result = play_services.play_live(season)
    except PlayInProgressError as exc:
        return _redirect_with_play_error(season, request, str(exc))
    except BrokerUnavailableError as exc:
        return _render_season_dashboard_error(request, season, str(exc), status=503)
    except (
        SeasonNotActiveError,
        NoLiveCursorError,
        ValidationError,
        ValueError,
    ) as exc:
        return _render_season_dashboard_error(request, season, str(exc))

    # Manager bye: nothing to watch -- the week runs in the background.
    if not result.round_ids:
        return redirect("season_dashboard", season_id=season.id)

    # Hand the watched round id(s) to the watch page -- just "what to show" (the
    # game is already committed, so this is NOT a retry pin).
    request.session["live_watch"] = {
        "season_id": season.id,
        "kind": result.kind,
        "round_ids": result.round_ids,
    }
    request.session.modified = True
    return redirect("play_week_live_watch", season_id=season.id)


def play_week_live_watch(request, season_id: int) -> HttpResponse:
    """GET: replay the manager's just-played game (read-only — no commit, no
    discard). Reads the watched round id(s) from the session handoff set by
    :func:`play_week_live`, builds the SIM-05 playback payload from the
    PERSISTED events (reusing ``matches.views.round_playback_payload``), and
    renders ``seasons/play_week_live.html``.

    405 on non-GET; 400 dashboard re-render when there is no game to watch
    (e.g. the page is visited directly without playing a week first).
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    watch = request.session.get("live_watch")
    if (
        not isinstance(watch, dict)
        or watch.get("season_id") != season.id
        or not watch.get("round_ids")
    ):
        return _render_season_dashboard_error(
            request, season, "No live game to watch — start one from the Play menu."
        )

    from matches.views import round_playback_payload

    by_id = {
        gr.id: gr
        for gr in GameRound.objects.filter(id__in=watch["round_ids"]).select_related(
            "team_red", "team_blue"
        )
    }
    rounds = []
    for rid in watch["round_ids"]:
        gr = by_id.get(rid)
        if gr is None:
            continue
        events_data, players_data = round_playback_payload(gr, include_movement=False)
        rounds.append(
            {
                "round_number": gr.round_number,
                "red_team_id": gr.team_red_id,
                "red_team_name": gr.team_red.name,
                "blue_team_id": gr.team_blue_id,
                "blue_team_name": gr.team_blue.name,
                "events_data": events_data,
                "players_data": players_data,
            }
        )

    if not rounds:
        return _render_season_dashboard_error(
            request, season, "No live game to watch — start one from the Play menu."
        )

    cursor_kind = watch.get("kind", "rr")
    if cursor_kind == "member_night":
        # A member-night game is one 2-Round Match: replay it like a playoff
        # Match (both Rounds + toggle); one Round falls back to the RR path.
        cursor_kind = "playoff" if len(rounds) > 1 else "rr"
    context = {
        "season": season,
        "cursor_kind": cursor_kind,
        "preview_round": rounds[0],
        "preview_round_2": rounds[1] if len(rounds) > 1 else None,
        "sidebar_active": None,
        "sidebar_links": _build_league_sidebar_links(
            season.league, _pick_displayed_season(season.league), None
        ),
    }
    return render(request, "seasons/play_week_live.html", context)


# ---------------------------------------------------------------------------
# LG-01g — Per-Team Schedule view + helpers
# ---------------------------------------------------------------------------


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

    league = get_owned_or_404(League, request, pk=league_id)
    team = get_owned_or_404(Team, request, pk=team_id)

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    if displayed_season is None:
        return HttpResponseNotFound("No Season in this League.")

    # ANG-01d-1 — Upcoming / Completed rows + member-night appearances are
    # built by the shared ``build_team_schedule``.
    rows = build_team_schedule(displayed_season, team)

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
        "member_night_appearances": rows["member_night_appearances"],
        "team_picker_options": displayed_season.teams.order_by("name"),
        "sidebar_links": sidebar_links,
        "sidebar_active": "schedule_team",
        "current_team": league.current_team,
    }
    return render(request, "leagues/team_schedule.html", context)


# --- CAR-02: owner-mood firing -----------------------------------------------


def league_delete(request: HttpRequest, league_id: int) -> HttpResponse:
    """DEL-01 — guarded Delete League (full teardown).

    GET renders a confirm page with a summary of what will be deleted; POST
    performs the teardown and redirects to the league list. Career-mode only.
    Mirrors the ``teams.views.player_delete`` GET-confirm / POST-act precedent
    (no 405 guard — GET legitimately renders the confirm page)."""
    league = get_owned_or_404(League, request, pk=league_id)

    if not _is_career_league(league):
        return HttpResponseBadRequest(
            "Delete League is only available for career (league-mode) Leagues."
        )

    if request.method == "POST":
        league_name = league.name
        _teardown_league(league)
        request.session.pop("last_league_id", None)
        messages.success(request, f'League "{league_name}" deleted.')
        return redirect("league_list")

    # GET — compute the delete summary via the same target helpers the teardown
    # uses (as counts) and render the confirm page.
    tournament_ids, candidate_team_ids = _league_teardown_targets(league)
    delete_summary = {
        "seasons": league.seasons.count(),
        "matches": Match.objects.filter(_league_match_q(league, tournament_ids))
        .distinct()
        .count(),
        "tournaments": len(tournament_ids),
        "teams": len(candidate_team_ids),
        "players": Player.objects.filter(team_id__in=candidate_team_ids).count(),
    }
    return render(
        request,
        "leagues/league_confirm_delete.html",
        {"league": league, "delete_summary": delete_summary},
    )


@transaction.atomic
def next_season(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01e / CAR-02 — POST entry point for the Start Next Season action.

    Runs the owner-mood verdict gate, then (for a non-fired or
    fired-and-already-reassigned Manager) the shared season rollover. A
    fired-and-unreassigned Manager cannot roll — they are redirected to the
    New-Team picker.

    Guards (in order):
        1. 405 on non-POST.
        2. 404 on missing League.
        3. 302 redirect to ``season_dashboard`` of ``league.active_season``
           when a non-completed Season already exists (active-Season
           guard — idempotent on double-submit).
        4. 400 ``HttpResponseBadRequest("No completed Season in this League.")``
           when no completed Season exists (defensive).
        5. CAR-02 verdict gate: ensure owner-evaluations exist, read the row for
           ``(league, latest_completed)``; if ``verdict == "fired"`` AND the
           Manager has NOT yet reassigned (``league.current_team`` still ==
           ``team_managed``) ⇒ redirect to ``new_team_picker``. Else roll.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_owned_or_404(League, request, pk=league_id)
    request.session["last_league_id"] = league.id

    # ANG-01d-1 — the guards, the FIN-01 / CAR-02 ensure + verdict gate and the
    # rollover live in ``start_next_season`` (shared with the API). The
    # exceptions are caught INSIDE this view's atomic block, so the lazy
    # finance / owner-evaluation writes commit exactly as before.
    try:
        new_season = start_next_season(league)
    except SeasonInProgressError as exc:
        return redirect("season_dashboard", season_id=exc.season.id)
    except NoCompletedSeasonError:
        return HttpResponseBadRequest("No completed Season in this League.")
    except ManagerFiredError:
        # A fired-and-unreassigned Manager (current_team still == the fired team)
        # cannot roll — they must pick a new team first.
        return redirect("new_team_picker", league_id=league.id)
    return redirect("season_dashboard", season_id=new_season.id)


def owner_evaluation(request: HttpRequest, season_id: int) -> HttpResponse:
    """CAR-02 — GET-only owner-evaluation screen for a completed Season.

    Lazily ensures THIS Season's row + all prior in-tenure rows exist, then
    reads the ``(league, season)`` row. 404 when the Season is not completed
    (the eval screen is only meaningful for a completed Season).
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_owned_or_404(Season, request, pk=season_id)
    request.session["last_league_id"] = season.league_id

    league = season.league
    # FIN-01 — finance rows must exist BEFORE the owner-eval ensure so the
    # money axis reads a real profit (the eval writer is idempotent — once a
    # money_delta=0 row is locked in, finance-ensuring later would not fix it).
    # ANG-01d-1 — both ensures + the read live in ``ensure_owner_evaluation``.
    evaluation = ensure_owner_evaluation(season)
    if evaluation is None:
        # The writer skips non-completed Seasons — no row means the eval screen
        # is not meaningful for this Season.
        raise Http404("No owner evaluation for this Season.")

    displayed_season = season
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active=None
    )

    is_fired = evaluation.verdict == "fired"
    # Fired-and-already-reassigned: current_team has moved off the fired team.
    reassigned = is_fired and league.current_team_id != evaluation.team_managed_id

    context = {
        "season": season,
        "league": league,
        "displayed_season": displayed_season,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
        "evaluation": evaluation,
        # Sum the float cumulatives view-side: Django's ``add`` template filter
        # truncates each operand to int (int(-0.8)+int(-0.4)=0), so the overall
        # mood must be computed here, not chained in the template.
        "overall_mood": (
            evaluation.wins_total + evaluation.playoffs_total + evaluation.money_total
        ),
        "is_fired": is_fired,
        "reassigned": reassigned,
    }
    return render(request, "seasons/owner_evaluation.html", context)


def new_team_picker(request: HttpRequest, league_id: int) -> HttpResponse:
    """CAR-02 — GET-only New-Team picker for a fired Manager.

    Lists the worst-``WORST_N_ELIGIBLE`` teams of the just-completed Season's
    final Standings, excluding the just-left team.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_owned_or_404(League, request, pk=league_id)
    if not _is_career_league(league):
        return HttpResponseBadRequest(
            "Manager firing applies only in single-player career mode."
        )
    request.session["last_league_id"] = league.id

    latest_completed = latest_completed_season(league)
    if latest_completed is None:
        return HttpResponseBadRequest("No completed Season in this League.")
    # UXP-03 — only a fired, not-yet-reassigned Manager picks a new team.
    if not manager_awaiting_reassignment(league, latest_completed):
        return redirect("owner_evaluation", season_id=latest_completed.id)

    eligible_teams = _eligible_new_teams(league, latest_completed)

    displayed_season = (
        league.active_season
        or league.seasons.filter(state="completed").order_by("-id").first()
    )
    sidebar_links = _build_league_sidebar_links(
        league, displayed_season, sidebar_active=None
    )

    context = {
        "league": league,
        "latest_completed": latest_completed,
        "eligible_teams": eligible_teams,
        "sidebar_links": sidebar_links,
        "sidebar_active": None,
    }
    return render(request, "leagues/new_team.html", context)


@transaction.atomic
def reassign_team(request: HttpRequest, league_id: int) -> HttpResponse:
    """CAR-02 — POST: reassign a fired Manager to a worst-N team, then roll.

    Validates the picked ``team_id`` against the eligible worst-N set
    (re-derived server-side), sets ``league.current_team`` (starting a new
    tenure), runs the shared rollover, and redirects to the new Season's
    dashboard.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_owned_or_404(League, request, pk=league_id)
    if not _is_career_league(league):
        return HttpResponseBadRequest(
            "Manager firing applies only in single-player career mode."
        )
    request.session["last_league_id"] = league.id

    # ANG-01d-1 — active-Season guard (mirrors ``next_season``): reassigning
    # while a Season is in progress would roll a SECOND non-completed Season
    # (``Season.objects.create`` bypasses ``Season.clean``). Redirect to the
    # in-progress Season's dashboard before any write.
    active = league.active_season
    if active is not None:
        return redirect("season_dashboard", season_id=active.id)

    latest_completed = latest_completed_season(league)
    if latest_completed is None:
        return HttpResponseBadRequest("No completed Season in this League.")
    if not manager_awaiting_reassignment(league, latest_completed):
        return HttpResponseBadRequest("Only a fired Manager can choose a new team.")

    raw_team_id = request.POST.get("team_id")
    try:
        team_id = int(raw_team_id)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("Invalid team_id.")

    eligible = _eligible_new_teams(league, latest_completed)
    eligible_ids = {row.team_id for row, _team in eligible}
    if team_id not in eligible_ids:
        return HttpResponseBadRequest("team_id is not an eligible team.")

    picked = get_owned_or_404(Team, request, pk=team_id)
    new_season = reassign_and_roll(league, picked, latest_completed)
    return redirect("season_dashboard", season_id=new_season.id)
