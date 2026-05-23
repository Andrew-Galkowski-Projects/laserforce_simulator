from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from matches.models import PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp
from .career_stats import points_trend, summarize, summarize_by_role
from .models import Team, Player, ROLE_CHOICES, _random_player_profile
from .forms import TeamForm, PlayerForm, TeamSlotForm
from .role_benchmarks import (
    ROLES,
    STAT_KEYS,
    compute_career_stat_for_role,
    compute_role_benchmarks,
    player_position,
)
from .role_benchmarks_cache import get_all_benchmark_data


def team_list(request):
    """Display all teams with their roster status"""
    teams = Team.objects.all().prefetch_related("players")
    return render(request, "teams/team_list.html", {"teams": teams})


def team_detail(request, team_id):
    """Display team details and roster"""
    team = get_object_or_404(Team, id=team_id)
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
            team = form.save()
            messages.success(request, f'Team "{team.name}" created successfully!')
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamForm()

    return render(
        request, "teams/team_form.html", {"form": form, "title": "Create New Team"}
    )


def team_edit(request, team_id):
    """Edit an existing team"""
    team = get_object_or_404(Team, id=team_id)

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
    team = get_object_or_404(Team, id=team_id)

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
    team = get_object_or_404(Team, id=team_id)
    player = get_object_or_404(Player, id=player_id, team=team)
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
    team = get_object_or_404(Team, id=team_id)

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
        form = PlayerForm(initial=_random_player_profile())

    return render(
        request,
        "teams/player_form.html",
        {"form": form, "team": team, "title": f"Add Player to {team.name}"},
    )


def player_edit(request, team_id, player_id):
    """Edit a player"""
    team = get_object_or_404(Team, id=team_id)
    player = get_object_or_404(Player, id=player_id, team=team)

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
    team = get_object_or_404(Team, id=team_id)
    player = get_object_or_404(Player, id=player_id, team=team)

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

        enriched = dict(row)
        enriched["benchmarks_by_stat"] = benchmarks_by_stat
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
    player = get_object_or_404(Player, pk=player_id)

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
