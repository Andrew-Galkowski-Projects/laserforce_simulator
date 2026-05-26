import random

from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from matches.models import PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp
from .career_stats import points_trend, summarize, summarize_by_role
from .constants import TEAM_NAMES, PLAYER_NAMES
from .models import (
    Team,
    Player,
    ROLE_CHOICES,
    _random_player_profile,
    get_free_agents_team,
)
from .forms import TeamForm, PlayerForm, TeamSlotForm, GenerateLeagueForm
from .player_generator import (
    assign_slots,
    draw_preferred_roles,
    draw_stats,
)
from .role_benchmarks import (
    ROLES,
    STAT_KEYS,
    compute_career_stat_for_role,
    compute_role_benchmarks,
    player_position,
)
from .role_benchmarks_cache import get_all_benchmark_data


def team_list(request):
    """Display all teams with their roster status.

    LG-00: excludes the reserved Free Agents Team via the new
    ``Team.objects.regular()`` manager method.
    """
    teams = Team.objects.regular().prefetch_related("players")
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


# LG-00 helpers -----------------------------------------------------------


def _pop_unique_name(
    pool: list[str], fallback: str, name_exists: "callable[[str], bool]"
) -> str:
    """Pop the next name from ``pool``, deduping against existing rows.

    ``name_exists`` is a callable taking a candidate string and returning
    True if it collides with a persisted row in the relevant scope (Team
    name globally, or Player name within a specific Team). On pool
    exhaustion, falls back to ``f"{fallback} #{n}"`` with the same dedupe.
    On collision with an in-pool candidate, appends ``" #2"``, ``" #3"``,
    ... until a free name is found.
    """
    if pool:
        candidate = pool.pop()
    else:
        # Pool exhausted — synthesize a name based on the last entry.
        n = 1
        while True:
            candidate = f"{fallback} #{n}"
            if not name_exists(candidate):
                return candidate
            n += 1

    if not name_exists(candidate):
        return candidate
    k = 2
    while True:
        suffixed = f"{candidate} #{k}"
        if not name_exists(suffixed):
            return suffixed
        k += 1


def _team_name_exists(name: str) -> bool:
    return Team.objects.filter(name=name).exists()


def _player_name_exists_on_team(team: Team) -> "callable[[str], bool]":
    """Return a closure asserting Player-name uniqueness within ``team``."""

    def _exists(name: str) -> bool:
        return Player.objects.filter(team=team, name=name).exists()

    return _exists


def _resolve_count_marker(raw: str, marker: str, low: int, high: int) -> int:
    """Resolve a `random_*` marker via stdlib ``random.randint``, else ``int(raw)``."""
    if raw == marker:
        return random.randint(low, high)
    return int(raw)


def _build_player_kwargs(rng: random.Random, mean: int, std_dev: int) -> dict:
    """Assemble the kwargs dict (profile + stats + preferred_roles) for one Player."""
    profile = _random_player_profile()
    profile.pop("name", None)  # caller supplies the name
    stats = draw_stats(rng, mean, std_dev)
    preferred = draw_preferred_roles(rng)
    return {"preferred_roles": preferred, **profile, **stats}


def _assign_team_slots(team: Team, created_players: list[Player]) -> None:
    """Run greedy slot assignment + back-fill leftovers for ``team``.

    Mutates ``team`` in memory (setattr on the slot FKs); caller is
    responsible for ``team.save()`` after this returns.
    """
    preferred_roles_per_player = [p.preferred_roles for p in created_players[:6]]
    slot_assignment = assign_slots(preferred_roles_per_player)

    # Indices NOT assigned to any slot — back-fill `None`-valued slot keys
    # in ascending player-index order.
    assigned_indices = {idx for idx in slot_assignment.values() if idx is not None}
    leftover_iter = iter(
        i for i in range(min(6, len(created_players))) if i not in assigned_indices
    )

    for slot_key, player_idx in slot_assignment.items():
        if player_idx is None:
            try:
                player_idx = next(leftover_iter)
            except StopIteration:
                # Fewer than 6 players available — leave the slot unfilled
                # (pure function returns None for the unreachable slots).
                continue
        setattr(team, f"slot_{slot_key}", created_players[player_idx])


def _generate_teams(
    num_teams: int,
    players_per_team: int,
    *,
    rng: random.Random,
    mean: int,
    std_dev: int,
    team_names_pool: list[str],
    player_names_pool: list[str],
) -> list[Team]:
    """Create ``num_teams`` new Teams, each with ``players_per_team`` Players.

    The first 6 players in each Team fill the SM5 slot FKs via greedy
    preferred-role matching (with leftover back-fill); players 7+ remain
    on the bench. Each Player is created with ``Player.objects.create``
    so PKs are available for slot FK assignment.
    """
    team_fallback = TEAM_NAMES[-1] if TEAM_NAMES else "Team"
    player_fallback = PLAYER_NAMES[-1] if PLAYER_NAMES else "Player"
    created_teams: list[Team] = []

    for _team_idx in range(num_teams):
        team_name = _pop_unique_name(team_names_pool, team_fallback, _team_name_exists)
        team = Team.objects.create(name=team_name)
        team_name_exists = _player_name_exists_on_team(team)

        created_players: list[Player] = []
        for _player_idx in range(players_per_team):
            player_name = _pop_unique_name(
                player_names_pool, player_fallback, team_name_exists
            )
            player = Player.objects.create(
                team=team,
                name=player_name,
                **_build_player_kwargs(rng, mean, std_dev),
            )
            created_players.append(player)

        _assign_team_slots(team, created_players)
        team.save()
        created_teams.append(team)

    return created_teams


def _generate_free_agents(
    players_per_team: int,
    *,
    rng: random.Random,
    mean: int,
    std_dev: int,
    player_names_pool: list[str],
) -> int:
    """Create ``players_per_team`` Players on the reserved Free Agents Team.

    Players land in the pool via ``bulk_create`` — the Free-Agents branch
    has no per-Player slot-FK step, so PKs after creation are not needed,
    and one INSERT replaces N. Returns the count actually created.
    """
    free_agents = get_free_agents_team()
    player_fallback = PLAYER_NAMES[-1] if PLAYER_NAMES else "Player"
    name_exists = _player_name_exists_on_team(free_agents)

    unsaved: list[Player] = []
    for _player_idx in range(players_per_team):
        player_name = _pop_unique_name(player_names_pool, player_fallback, name_exists)
        unsaved.append(
            Player(
                team=free_agents,
                name=player_name,
                **_build_player_kwargs(rng, mean, std_dev),
            )
        )
    Player.objects.bulk_create(unsaved)
    return len(unsaved)


@transaction.atomic
def generate_players(request):
    """LG-00 player/team generation surface.

    GET → render `templates/teams/generate_players.html` with an empty form.
    POST → validate the form; on success, resolve the `random_*` markers,
    generate the Teams (or the Free Agents pool) inside a single
    transaction, and re-render `templates/teams/generate_players_done.html`
    directly (no redirect, no session round-trip). On invalid form,
    re-render the form page with errors (status 200).
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

    num_teams = _resolve_count_marker(
        form.cleaned_data["num_teams"], "random_2_10", 2, 10
    )
    ppt_raw = form.cleaned_data["players_per_team"]
    if ppt_raw == "random_team":
        players_per_team = random.randint(6, 8)
    elif ppt_raw == "random_pool":
        players_per_team = random.randint(12, 100)
    else:
        players_per_team = int(ppt_raw)
    mean = form.cleaned_data["mean"]
    std_dev = form.cleaned_data["std_dev"]

    # Two RNG sources in deliberate split: ``rng`` (a private ``random.Random``)
    # is the only RNG passed into the pure ``player_generator`` module so its
    # stat-draw / role-draw seam stays deterministic when callers seed it.
    # The view's own ``random.shuffle`` / ``random.randint`` calls are purely
    # presentation-layer (name-pool order, "Random (...)" marker resolution)
    # and never cross the pure-module boundary.
    rng = random.Random()
    team_names_pool = list(TEAM_NAMES)
    random.shuffle(team_names_pool)
    player_names_pool = list(PLAYER_NAMES)
    random.shuffle(player_names_pool)

    if num_teams >= 1:
        created_teams = _generate_teams(
            num_teams,
            players_per_team,
            rng=rng,
            mean=mean,
            std_dev=std_dev,
            team_names_pool=team_names_pool,
            player_names_pool=player_names_pool,
        )
        free_agent_count = 0
    else:
        created_teams = []
        free_agent_count = _generate_free_agents(
            players_per_team,
            rng=rng,
            mean=mean,
            std_dev=std_dev,
            player_names_pool=player_names_pool,
        )

    return render(
        request,
        "teams/generate_players_done.html",
        {
            "created_teams": created_teams,
            "free_agent_count": free_agent_count,
        },
    )
