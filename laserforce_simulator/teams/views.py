from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render, redirect
from django.views.decorators.http import require_GET
from django.contrib import messages
from django.core.exceptions import ValidationError
from accounts.permissions import get_owned_or_404, manager_or_none, owned_queryset
from matches.models import PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp
from .career_stats import points_trend, summarize, summarize_by_role
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
from .role_benchmarks import (
    ROLES,
    STAT_KEYS,
    compute_career_stat_for_role,
    compute_role_benchmarks,
    player_position,
)
from .role_benchmarks_cache import get_all_benchmark_data

# ANG-01a — the LG-00 / LG-00b generation and roster-import helpers moved to
# `teams.services` (shared with the REST API). They are re-exported here so
# existing `from teams.views import _generate_teams, _apply_roster, ...`
# importers (league / tournament views, tests) keep working.
from .services import (  # noqa: F401 - re-exports
    _ROSTER_TEMPLATE_ROWS,
    _apply_roster,
    _assign_team_slots,
    _build_player_kwargs,
    _check_db_slot_collisions,
    _generate_free_agents,
    _generate_teams,
    _player_name_exists_on_team,
    _pop_unique_name,
    _resolve_count_marker,
    _team_name_exists,
    annotate_overall_rating,
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


# HX-01 display key → STAT_KEYS member. The only two HX-01 per-role
# columns that sprout HX-02 benchmark overlays in v1 are points and
# accuracy; the other three render a "—" placeholder so the DOM shape
# stays consistent (the seam locks this).
_HX01_DISPLAY_STAT_KEYS: tuple[str, ...] = (
    "avg_points",
    "tag_ratio",
    "avg_survival_ticks",
    "avg_accuracy_pct",
    "avg_sp_earned",
)
_HX01_TO_BENCHMARK_STAT: dict[str, str] = {
    "avg_points": "points_scored",
    "avg_accuracy_pct": "accuracy",
}

# HX-01b — frozen 15-entry stat row spec for the per-role table.
# Each entry: (key, label, benchmark_stat | None).
#
# Rows 0-4 are the 5 HX-01 display stats in their current order. Rows 5-14
# are STAT_KEYS (`teams/role_benchmarks.py:18`) in declaration order, skipping
# `points_scored` and `accuracy` (already covered at rows 0 and 3 via
# `_HX01_TO_BENCHMARK_STAT`).
#
# The `key` field is the row identifier used in DOM ids verbatim - mixed
# namespace (HX-01 display keys for rows 0-4, STAT_KEYS names for rows 5-14)
# is intentional and locked. Tests pin the order and labels; see
# `.claude/worktrees/hx-01b-seam-contract.md`.
_HX01B_STAT_ROW_SPEC: tuple[tuple[str, str, str | None], ...] = (
    ("avg_points", "Avg points", "points_scored"),
    ("tag_ratio", "Tag ratio", None),
    ("avg_survival_ticks", "Avg survival", None),
    ("avg_accuracy_pct", "Avg accuracy", "accuracy"),
    ("avg_sp_earned", "Avg SP earned", None),
    ("mvp", "MVP score", "mvp"),
    ("tags_made", "Tags made", "tags_made"),
    ("times_tagged", "Times tagged", "times_tagged"),
    ("final_lives", "Final lives", "final_lives"),
    ("resupplies_given", "Resupplies given", "resupplies_given"),
    ("missiles_landed", "Missiles landed", "missiles_landed"),
    ("specials_used", "Specials used", "specials_used"),
    ("follow_up_shots", "Follow-up shots", "follow_up_shots"),
    ("reaction_shots", "Reaction shots", "reaction_shots"),
    ("combo_resupply_count", "Combo resupplies", "combo_resupply_count"),
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


def _round_dict_from_state(state: PlayerRoundState) -> dict:
    """Build the 18-key round-dict (the HX-02 view ↔ pure-module seam)
    from a single `PlayerRoundState` ORM row.

    - HX-01 10 keys are preserved verbatim.
    - 6 raw HX-02 counters.
    - `mvp` is pre-computed via `calculate_mvp` (the same code path the
      ORM property uses, just inlined to avoid a double-property hop).
    - `accuracy_pct` is pre-computed via the existing `get_accuracy`
      property (symmetric carry-over; the reducer rebuilds accuracy from
      raw counters).
    """
    return {
        "role": state.role,
        "points_scored": state.points_scored,
        "tags_made": state.tags_made,
        "times_tagged": state.times_tagged,
        "shots_missed": state.shots_missed,
        "final_special": state.final_special,
        "specials_used": state.specials_used,
        "was_eliminated_at": state.was_eliminated_at,
        "date_played": state.game_round.date_played,
        "game_round_id": state.game_round_id,
        # HX-02 6 raw counters
        "final_lives": state.final_lives,
        "resupplies_given": state.resupplies_given,
        "missiles_landed": state.missiles_landed,
        "follow_up_shots": state.follow_up_shots,
        "reaction_shots": state.reaction_shots,
        "combo_resupply_count": state.combo_resupply_count,
        # HX-02 2 pre-computed (view-side)
        "mvp": float(calculate_mvp(state)),
        "accuracy_pct": float(state.get_accuracy),
    }


def _build_per_role_overlay(
    rounds: list[dict],
    per_role: list[dict],
    samples_by_key: dict,
    rounds_in_role_by_role: dict,
    player_id: int,
    min_rounds: int,
) -> list[dict]:
    """Build the HX-02 per-role benchmark overlay for the HX-01 career page.

    Returns the existing `per_role` rows enriched with a
    ``benchmarks_by_stat`` dict — one entry per HX-01 display stat — that
    the template renders into mean / median / delta / percentile / n
    cells. Stats not in ``_HX01_TO_BENCHMARK_STAT`` get an all-``None``
    placeholder so the DOM shape stays consistent.
    """
    # Group the player's round-dicts by role so a per-stat lookup doesn't
    # rescan the whole list.
    rounds_by_role: dict[str, list[dict]] = {}
    for rd in rounds:
        rounds_by_role.setdefault(rd["role"], []).append(rd)

    out: list[dict] = []
    for row in per_role:
        role = row["role"]
        player_role_rounds = rounds_by_role.get(role, [])
        player_role_count = len(player_role_rounds)
        qualified = player_role_count >= min_rounds

        role_thresholds = rounds_in_role_by_role.get(role, {})
        benchmarks_by_stat: dict[str, dict] = {}
        for hx01_key in _HX01_DISPLAY_STAT_KEYS:
            bench_stat = _HX01_TO_BENCHMARK_STAT.get(hx01_key)
            if bench_stat is None:
                benchmarks_by_stat[hx01_key] = {
                    "benchmark_mean": None,
                    "benchmark_median": None,
                    "delta_mean": None,
                    "delta_median": None,
                    "percentile": None,
                    "qualified": False,
                    "n": 0,
                }
                continue
            samples = samples_by_key.get((role, bench_stat), [])
            # Apply the threshold so the subject's percentile is computed
            # against the same filtered population the standalone benchmarks
            # page shows.
            if min_rounds > 0:
                filtered = [
                    (pid, val)
                    for pid, val in samples
                    if role_thresholds.get(pid, 0) >= min_rounds
                ]
            else:
                filtered = list(samples)

            subject_value = compute_career_stat_for_role(player_role_rounds, bench_stat)
            benchmarks_by_stat[hx01_key] = player_position(
                filtered, player_id, subject_value, qualified
            )

        # HX-01b - additively build the 15-entry ordered `stat_rows` list.
        # Rows 0-4 (HX-01 5 keys) pull `player_value` from the existing
        # `row` dict; rows 5-14 (10 net-new STAT_KEYS) call
        # `compute_career_stat_for_role` against the same
        # `player_role_rounds` slice already built above.
        stat_rows: list[dict] = []
        for key, label, bench_stat in _HX01B_STAT_ROW_SPEC:
            if key in row:
                player_value = float(row[key])
            else:
                player_value = float(
                    compute_career_stat_for_role(player_role_rounds, bench_stat)
                )

            if bench_stat is None:
                benchmark: dict | None = None
            else:
                samples = samples_by_key.get((role, bench_stat), [])
                if min_rounds > 0:
                    filtered = [
                        (pid, val)
                        for pid, val in samples
                        if role_thresholds.get(pid, 0) >= min_rounds
                    ]
                else:
                    filtered = list(samples)
                # `player_value` was already computed as the same
                # `compute_career_stat_for_role(player_role_rounds, bench_stat)`
                # call above for net-new rows; for HX-01 rows it matches
                # `row[key]` which is sum/sum-aggregated identically. Reuse it
                # so the helper isn't called twice per row.
                benchmark = player_position(
                    filtered, player_id, player_value, qualified
                )

            stat_rows.append(
                {
                    "key": key,
                    "label": label,
                    "player_value": player_value,
                    "benchmark": benchmark,
                }
            )

        enriched = dict(row)
        enriched["benchmarks_by_stat"] = benchmarks_by_stat
        enriched["stat_rows"] = stat_rows
        out.append(enriched)
    return out


def player_career_stats(request, player_id: int):
    """Render a player's career stats page (HX-01 + HX-02 overlay).

    Aggregates every `PlayerRoundState` for this player into the totals,
    per-role breakdown, and rolling-mean trend defined in
    `teams/career_stats.py`, then overlays HX-02 per-role role
    benchmarks fetched from the cache helper. The view owns the
    round-dict assembly so the pure modules never see a Django object.
    """
    player = get_owned_or_404(Player, request, pk=player_id)

    states = (
        PlayerRoundState.objects.filter(player=player)
        .select_related("game_round")
        .order_by("game_round__date_played", "game_round_id")
    )

    rounds = [_round_dict_from_state(s) for s in states]

    career = summarize(rounds)
    per_role = summarize_by_role(rounds)
    trend = points_trend(rounds)
    total_rounds = career["games"]

    # HX-02 query params (same shape as the standalone benchmarks page).
    min_rounds = _coerce_threshold(request.GET.get("threshold"))
    display = _coerce_display(request.GET.get("display"))

    # Build per-role benchmark overlays only when the player has rounds —
    # otherwise the per-role table doesn't render at all.
    per_role_with_benchmarks: list[dict] = []
    if total_rounds > 0:
        samples_by_key, rounds_in_role_by_role = get_all_benchmark_data()
        per_role_with_benchmarks = _build_per_role_overlay(
            rounds,
            per_role,
            samples_by_key,
            rounds_in_role_by_role,
            player.id,
            min_rounds,
        )

    context = {
        "player": player,
        "total_rounds": total_rounds,
        "career": career,
        "per_role": per_role,
        "trend": trend,
        "has_rounds": total_rounds > 0,
        # HX-02 additive keys
        "min_rounds": min_rounds,
        "display": display,
        "stat_keys": _HX01_DISPLAY_STAT_KEYS,
        "per_role_with_benchmarks": per_role_with_benchmarks,
    }
    return render(request, "teams/player_career_stats.html", context)


def role_benchmarks(request):
    """HX-02 — global role benchmarks page (one table per role).

    Reads `?threshold=` and `?display=mean|median` (both with safe
    fallbacks), pulls the cached samples, computes the 60-cell benchmark
    summary, and shapes it into one ordered list per role.
    """
    min_rounds = _coerce_threshold(request.GET.get("threshold"))
    display = _coerce_display(request.GET.get("display"))

    samples_by_key, rounds_in_role_by_role = get_all_benchmark_data()
    summaries = compute_role_benchmarks(
        samples_by_key, rounds_in_role_by_role, min_rounds
    )

    # Seam locks `benchmarks` as `{role: [{stat,mean,median,...}, …]}` so
    # tests (and any external consumer) can read it as a dict. We also
    # ship `benchmarks_by_role` — the same data flattened into a list of
    # `(role, rows)` 2-tuples — so the template can iterate without
    # variable key-lookup gymnastics.
    benchmarks: dict[str, list[dict]] = {}
    any_data = False
    for role in ROLES:
        rows: list[dict] = []
        for stat in STAT_KEYS:
            summary = summaries[(role, stat)]
            if summary["n"] and summary["n"] > 0:
                any_data = True
            rows.append(
                {
                    "stat": stat,
                    "mean": summary["mean"],
                    "median": summary["median"],
                    "p25": summary["p25"],
                    "p75": summary["p75"],
                    "p90": summary["p90"],
                    "n": summary["n"],
                }
            )
        benchmarks[role] = rows
    benchmarks_by_role = [(role, benchmarks[role]) for role in ROLES]

    context = {
        "min_rounds": min_rounds,
        "display": display,
        "roles": ROLES,
        "benchmarks": benchmarks,
        "stat_keys": STAT_KEYS,
        # Template-only conveniences (not part of the locked context
        # contract; the seam-locked keys above are the public surface).
        "benchmarks_by_role": benchmarks_by_role,
        "any_data": any_data,
    }
    return render(request, "teams/role_benchmarks.html", context)


@transaction.atomic
def generate_players(request):
    """LG-00 player/team generation surface.

    GET → render `templates/teams/generate_players.html` with an empty form.
    POST → validate the form; on success, generate the Teams (or the Free
    Agents pool) via :func:`teams.services.run_player_generation` inside a
    single transaction, and re-render
    `templates/teams/generate_players_done.html` directly (no redirect, no
    session round-trip). On invalid form, re-render the form page with
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

    return render(
        request,
        "teams/generate_players_done.html",
        {
            "created_teams": result.created_teams,
            "free_agent_count": result.free_agent_count,
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
