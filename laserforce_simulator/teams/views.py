from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_GET
from django.contrib import messages
from django.core.exceptions import ValidationError
from accounts.permissions import get_owned_or_404, manager_or_none, owned_queryset
from .models import (
    Team,
    Player,
    ROLE_CHOICES,
)
from .forms import (
    TeamForm,
    PlayerForm,
    TeamSlotForm,
    GenerateLeagueForm,
    RosterImportForm,
)
from .roster_importer import RosterImportError

# ANG-01a — the LG-00 / LG-00b generation and roster-import helpers moved to
# `teams.services` (shared with the REST API). They are re-exported here so
# existing `from teams.views import _generate_teams, _apply_roster, ...`
# importers (league / tournament views, tests) keep working.
from .services import (  # noqa: F401 - re-exports
    _HX01_DISPLAY_STAT_KEYS,
    _HX01_TO_BENCHMARK_STAT,
    _HX01B_STAT_ROW_SPEC,
    _ROSTER_TEMPLATE_ROWS,
    _apply_roster,
    _assign_team_slots,
    _build_per_role_overlay,
    _build_player_kwargs,
    _check_db_slot_collisions,
    _generate_free_agents,
    _generate_teams,
    _player_name_exists_on_team,
    _pop_unique_name,
    _resolve_count_marker,
    _round_dict_from_state,
    _team_name_exists,
    annotate_overall_rating,
    build_career_stats,
    build_role_benchmarks,
    build_roster_template_csv,
    player_form_initial,
    run_player_generation,
    run_roster_import,
)


def team_list(request):
    """Display all teams with their roster status.

    LG-00: excludes the reserved Free Agents Team via the new
    ``Team.objects.regular()`` manager method.
    """
    teams = owned_queryset(
        Team.objects.regular().prefetch_related("players"), request.user
    )
    return render(request, "teams/team_list.html", {"teams": teams})


def team_detail(request, team_id):
    """Display team details and roster"""
    team = get_owned_or_404(Team, request, id=team_id)
    players = team.players.all().order_by("name")

    context = {
        "team": team,
        "players": players,
        "active_roster": team.active_roster,
        "bench_players": team.bench_players,
        "is_valid": team.is_valid_roster,
        "roster_errors": team.roster_errors,
        "player_count": team.player_count,
        "role_choices": ROLE_CHOICES,
    }
    return render(request, "teams/team_detail.html", context)


def team_create(request):
    """Create a new team"""
    if request.method == "POST":
        form = TeamForm(request.POST)
        if form.is_valid():
            # UX-01 — stamp the **Manager** at creation from request.user.
            team = form.save(commit=False)
            team.manager = manager_or_none(request)
            team.save()
            messages.success(request, f'Team "{team.name}" created successfully!')
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamForm()

    return render(
        request, "teams/team_form.html", {"form": form, "title": "Create New Team"}
    )


def team_edit(request, team_id):
    """Edit an existing team"""
    team = get_owned_or_404(Team, request, id=team_id)

    if request.method == "POST":
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            team = form.save()
            messages.success(request, f'Team "{team.name}" updated successfully!')
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamForm(instance=team)

    return render(
        request,
        "teams/team_form.html",
        {"form": form, "title": f"Edit {team.name}", "team": team},
    )


def team_slots_edit(request, team_id):
    """Assign players to role slots on a team."""
    team = get_owned_or_404(Team, request, id=team_id)

    if request.method == "POST":
        form = TeamSlotForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, "Roster slots updated.")
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamSlotForm(instance=team)

    return render(
        request,
        "teams/team_slots_form.html",
        {"form": form, "team": team, "title": f"Edit Roster – {team.name}"},
    )


# PD-1: groups mirror the documented stat categories in teams/CLAUDE.md
# (Awareness / Decision-making / Physical / Team / Role). Every one of the
# 19 stats appears in exactly one group.
_STAT_GROUPS = [
    (
        "Awareness",
        [
            ("player_awareness", "Player Awareness"),
            ("game_awareness", "Game Awareness"),
            ("resource_awareness", "Resource Awareness"),
        ],
    ),
    (
        "Decision-making",
        [
            ("decision_making", "Decision Making"),
        ],
    ),
    (
        "Physical",
        [
            ("positioning", "Positioning"),
            ("stamina", "Stamina"),
            ("speed", "Speed"),
            ("flexibility", "Flexibility"),
            ("adaptability", "Adaptability"),
        ],
    ),
    (
        "Team",
        [
            ("communication", "Communication"),
            ("teamwork", "Teamwork"),
        ],
    ),
    (
        "Role",
        [
            ("Offensive_synergy", "Offensive Synergy"),
            ("defensive_synergy", "Defensive Synergy"),
            ("midfield_synergy", "Midfield Synergy"),
            ("resupply_synergy", "Resupply Synergy"),
            ("resupply_efficiency", "Resupply Efficiency"),
            ("accuracy", "Accuracy"),
            ("survival", "Survival"),
            ("special_usage", "Special Usage"),
        ],
    ),
]


def player_detail(request, team_id: int, player_id: int):
    team = get_owned_or_404(Team, request, id=team_id)
    player = get_owned_or_404(Player, request, id=player_id, team=team)
    stat_groups = [
        (group_name, [(label, getattr(player, field)) for field, label in fields])
        for group_name, fields in _STAT_GROUPS
    ]
    return render(
        request,
        "teams/player_detail.html",
        {"player": player, "team": team, "stat_groups": stat_groups},
    )


def player_add(request, team_id):
    """Add a player to a team"""
    team = get_owned_or_404(Team, request, id=team_id)

    if request.method == "POST":
        form = PlayerForm(request.POST)
        if form.is_valid():
            player = form.save(commit=False)
            player.team = team
            try:
                player.full_clean()
                player.save()
                messages.success(
                    request, f'Player "{player.name}" added to {team.name}!'
                )
                return redirect("team_detail", team_id=team.id)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
    else:
        form = PlayerForm(initial=player_form_initial())

    return render(
        request,
        "teams/player_form.html",
        {"form": form, "team": team, "title": f"Add Player to {team.name}"},
    )


def player_edit(request, team_id, player_id):
    """Edit a player"""
    team = get_owned_or_404(Team, request, id=team_id)
    player = get_owned_or_404(Player, request, id=player_id, team=team)

    if request.method == "POST":
        form = PlayerForm(request.POST, instance=player)
        if form.is_valid():
            player = form.save(commit=False)
            try:
                player.full_clean()
                player.save()
                messages.success(request, f'Player "{player.name}" updated!')
                return redirect("team_detail", team_id=team.id)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
    else:
        form = PlayerForm(instance=player)

    return render(
        request,
        "teams/player_form.html",
        {"form": form, "team": team, "player": player, "title": f"Edit {player.name}"},
    )


def player_delete(request, team_id, player_id):
    """Delete a player"""
    team = get_owned_or_404(Team, request, id=team_id)
    player = get_owned_or_404(Player, request, id=player_id, team=team)

    if request.method == "POST":
        player_name = player.name
        player.delete()
        messages.success(request, f'Player "{player_name}" removed from {team.name}')
        return redirect("team_detail", team_id=team.id)

    return render(
        request, "teams/player_confirm_delete.html", {"player": player, "team": team}
    )


def _coerce_threshold(raw: str | None, default: int = 5) -> int:
    """Parse the ``?threshold=`` query param.

    Non-int / negative / missing → ``default``. Negative ints clamp to 0;
    everything else returns as-is.
    """
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return max(0, val)


def _coerce_display(raw: str | None, default: str = "mean") -> str:
    """Parse the ``?display=mean|median`` query param; invalid → default."""
    if raw in ("mean", "median"):
        return raw
    return default


# LG-00c — sortable Players tab. The frozen 22-entry ORM whitelist
# (URL key → ORM target) plus the `preferred_roles` Python-side
# sentinel (handled in a separate branch of `player_list`) gives 23
# total accepted sort keys. See `.claude/worktrees/lg-00c-seam-contract.md`.
_SORT_KEYS: dict[str, str] = {
    "name": "name",
    "team": "team__name",
    "overall_rating": "overall_rating_db",
    "player_awareness": "player_awareness",
    "game_awareness": "game_awareness",
    "resource_awareness": "resource_awareness",
    "decision_making": "decision_making",
    "positioning": "positioning",
    "stamina": "stamina",
    "speed": "speed",
    "flexibility": "flexibility",
    "adaptability": "adaptability",
    "communication": "communication",
    "teamwork": "teamwork",
    "offensive_synergy": "Offensive_synergy",
    "defensive_synergy": "defensive_synergy",
    "midfield_synergy": "midfield_synergy",
    "resupply_synergy": "resupply_synergy",
    "resupply_efficiency": "resupply_efficiency",
    "accuracy": "accuracy",
    "survival": "survival",
    "special_usage": "special_usage",
    "potential": "potential",
}

# LG-00c — 23-entry column display spec (single source of truth for
# both `<th>` headers and per-row `<td>` cells). The 23rd entry is the
# `preferred_roles` Python-side sentinel.
_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("name", "Name"),
    ("team", "Team"),
    ("preferred_roles", "Preferred Roles"),
    ("overall_rating", "Overall"),
    ("player_awareness", "Player Aware"),
    ("game_awareness", "Game Aware"),
    ("resource_awareness", "Resource Aware"),
    ("decision_making", "Decision"),
    ("positioning", "Positioning"),
    ("stamina", "Stamina"),
    ("speed", "Speed"),
    ("flexibility", "Flexibility"),
    ("adaptability", "Adaptability"),
    ("communication", "Communication"),
    ("teamwork", "Teamwork"),
    ("offensive_synergy", "Offensive Syn"),
    ("defensive_synergy", "Defensive Syn"),
    ("midfield_synergy", "Midfield Syn"),
    ("resupply_synergy", "Resupply Syn"),
    ("resupply_efficiency", "Resupply Eff"),
    ("accuracy", "Accuracy"),
    ("survival", "Survival"),
    ("special_usage", "Special Usage"),
)

_VALID_DIRS: tuple[str, str] = ("asc", "desc")
_VALID_PAGE_SIZES: tuple[int, ...] = (10, 25, 50, 100)
_DEFAULT_PAGE_SIZE: int = 10


def _coerce_sort(raw: str | None, default: str = "team") -> str:
    """LG-00c — invalid / missing → default.

    Accepted: every key in ``_SORT_KEYS`` plus the literal
    ``"preferred_roles"`` sentinel (handled in a separate branch of
    the view).
    """
    if raw in _SORT_KEYS or raw == "preferred_roles":
        return raw
    return default


def _coerce_dir(raw: str | None, default: str = "asc") -> str:
    """LG-00c — only ``"asc"`` / ``"desc"`` accepted; everything else
    → default.

    Case-sensitive: ``"ASC"`` falls back to the default (mirrors
    HX-02's ``_coerce_display`` casing discipline).
    """
    if raw in _VALID_DIRS:
        return raw
    return default


def _coerce_per_page(raw: str | None, default: int = _DEFAULT_PAGE_SIZE) -> int:
    """LG-00c — invalid / out-of-whitelist / non-int → default.

    Accepted: only the integer values in ``_VALID_PAGE_SIZES``. Strings
    are parsed via ``int()``; anything that fails or lands outside the
    whitelist falls back to the default (mirrors HX-02's
    ``_coerce_threshold`` discipline).
    """
    try:
        val = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default
    return val if val in _VALID_PAGE_SIZES else default


def player_career_stats(request, player_id: int):
    """Render a player's career stats page (HX-01 + HX-02 overlay).

    Delegates the aggregation to ``teams.services.build_career_stats`` (the
    round-dict assembly, HX-01 summaries and the HX-02 per-role benchmark
    overlay); the view keeps the lenient query-param parsing.
    """
    player = get_owned_or_404(Player, request, pk=player_id)

    # HX-02 query params (same shape as the standalone benchmarks page).
    min_rounds = _coerce_threshold(request.GET.get("threshold"))
    display = _coerce_display(request.GET.get("display"))

    context = {
        "player": player,
        **build_career_stats(player, min_rounds=min_rounds, display=display),
    }
    return render(request, "teams/player_career_stats.html", context)


def role_benchmarks(request):
    """HX-02 — global role benchmarks page (one table per role).

    Reads `?threshold=` and `?display=mean|median` (both with safe
    fallbacks) and renders ``teams.services.build_role_benchmarks``.
    """
    min_rounds = _coerce_threshold(request.GET.get("threshold"))
    display = _coerce_display(request.GET.get("display"))

    context = build_role_benchmarks(min_rounds=min_rounds, display=display)
    return render(request, "teams/role_benchmarks.html", context)


# UXP-16: session key holding the last generation's result for the PRG page.
_GENERATE_RESULT_SESSION_KEY = "generate_players_result"


@transaction.atomic
def generate_players(request: HttpRequest) -> HttpResponse:
    """LG-00 player/team generation surface.

    GET → render `templates/teams/generate_players.html` with an empty form.
    POST → validate the form; on success, generate the Teams (or the Free
    Agents pool) via :func:`teams.services.run_player_generation` inside a
    single transaction, stash the result ids in the session and redirect to
    :func:`generate_players_done` (UXP-16 Post/Redirect/Get, so a refresh
    never generates again). On invalid form, re-render the form page with
    errors (status 200).
    """
    if request.method == "GET":
        return render(
            request,
            "teams/generate_players.html",
            {"form": GenerateLeagueForm()},
        )

    form = GenerateLeagueForm(request.POST)
    if not form.is_valid():
        return render(request, "teams/generate_players.html", {"form": form})

    result = run_player_generation(form.cleaned_data, manager=manager_or_none(request))
    request.session[_GENERATE_RESULT_SESSION_KEY] = {
        "team_ids": [team.pk for team in result.created_teams],
        "free_agent_count": result.free_agent_count,
    }
    return redirect("generate_players_done")


def generate_players_done(request: HttpRequest) -> HttpResponse:
    """UXP-16 — GET result page for the last generation (read-only).

    Renders `templates/teams/generate_players_done.html` from the ids
    :func:`generate_players` stashed in the session; refreshing it writes
    nothing. With no stashed result, redirect to the form.
    """
    stashed = request.session.get(_GENERATE_RESULT_SESSION_KEY)
    if not stashed:
        return redirect("generate_players")
    team_ids = stashed.get("team_ids", [])
    teams_by_id = owned_queryset(Team.objects.all(), request.user).in_bulk(team_ids)
    created_teams = [teams_by_id[pk] for pk in team_ids if pk in teams_by_id]
    return render(
        request,
        "teams/generate_players_done.html",
        {
            "created_teams": created_teams,
            "free_agent_count": stashed.get("free_agent_count", 0),
        },
    )


# LG-00b roster import ----------------------------------------------------


@transaction.atomic
def import_roster(request):
    """LG-00b roster-import surface.

    ``GET`` → render the form (status 200). ``POST`` → validate the form,
    then :func:`teams.services.run_roster_import` parses the upload, runs
    the ownership-scoped DB slot-collision pre-check, and creates / appends
    Teams and Players inside a single transaction. On ANY error, render the
    form page with the error list (status 200) and roll back writes via
    :func:`transaction.set_rollback`.
    """

    if request.method == "POST":
        form = RosterImportForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                result = run_roster_import(
                    form.cleaned_data["csv_file"], manager=manager_or_none(request)
                )
            except RosterImportError as exc:
                transaction.set_rollback(True)
                return render(
                    request,
                    "teams/roster_import.html",
                    {
                        "form": form,
                        "errors": [str(exc)],
                        "row_errors": exc.errors,
                    },
                )
            return render(
                request,
                "teams/roster_import_done.html",
                {
                    "created_teams": result.created_teams,
                    "appended_teams": result.appended_teams,
                    "player_count": result.player_count,
                    "row_count": result.row_count,
                },
            )
    else:
        form = RosterImportForm()

    return render(
        request,
        "teams/roster_import.html",
        {"form": form, "errors": [], "row_errors": []},
    )


@require_GET
def import_roster_template(request):
    """GET-only download of the canonical roster CSV template.

    The body comes from :func:`teams.services.build_roster_template_csv`
    (header row in ``ALL_COLUMNS`` order plus two example data rows).
    """

    response = HttpResponse(build_roster_template_csv(), content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="roster_template.csv"'
    return response


def player_list(request):
    """LG-00c — sortable, paginated index of every Player.

    Server-side sort via ``?sort=&dir=asc|desc`` and page size via
    ``?per_page=10|25|50|100`` (default 10). Forgiving-fallback
    validation (invalid / missing → defaults). Includes players on the
    Free Agents Team.
    """
    sort = _coerce_sort(request.GET.get("sort"))
    direction = _coerce_dir(request.GET.get("dir"))
    per_page = _coerce_per_page(request.GET.get("per_page"))

    qs = annotate_overall_rating(
        owned_queryset(Player.objects.select_related("team"), request.user, path="team")
    )

    if sort == "preferred_roles":
        rows = list(qs)
        rows.sort(
            key=lambda p: (",".join(p.preferred_roles or []), p.name),
            reverse=(direction == "desc"),
        )
        paginator = Paginator(rows, per_page)
    else:
        prefix = "" if direction == "asc" else "-"
        qs = qs.order_by(prefix + _SORT_KEYS[sort], "name")
        paginator = Paginator(qs, per_page)

    page_raw = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page_raw)
    except (EmptyPage, PageNotAnInteger):
        page_obj = paginator.page(1)

    # Build querystring helpers for the template from COERCED values so
    # invalid `?sort=BOGUS&dir=SIDEWAYS` does not survive in pagination
    # links (LG00c-7 fix; raw ``request.GET.copy()`` would propagate the
    # uncoerced rubbish even though the view itself handles it safely).
    qs_no_page = request.GET.copy()
    qs_no_page.pop("page", None)
    qs_no_page["sort"] = sort
    qs_no_page["dir"] = direction
    qs_no_page["per_page"] = str(per_page)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    qs_no_sort_dir_page["per_page"] = str(per_page)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context = {
        "page_obj": page_obj,
        "paginator": paginator,
        "sort": sort,
        "dir": direction,
        "per_page": per_page,
        "page_size_options": _VALID_PAGE_SIZES,
        "sort_keys": _SORT_KEYS_DISPLAY,
        "querystring_without_page": querystring_without_page,
        "querystring_without_sort_dir_page": querystring_without_sort_dir_page,
    }
    return render(request, "teams/player_list.html", context)
