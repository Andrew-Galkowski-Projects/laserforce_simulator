"""ANG-01a — Team/Player write services shared by the HTML views and the REST API.

The post-validation halves of LG-00 generation and LG-00b roster import live
here so ``teams.views`` (HTML) and ``teams.api_views`` (REST) run the exact
same code path. The generation / roster helpers moved here from
``teams.views`` (which re-exports them for existing importers) to avoid a
views <-> services import cycle.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import random
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from django.db import transaction
from django.db.models import F, QuerySet

from accounts.permissions import owned_queryset
from matches.models import PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp

from .career_stats import points_trend, summarize, summarize_by_role
from .constants import PLAYER_NAMES, TEAM_NAMES
from .forms import PlayerForm
from .models import Player, Team, _random_player_profile, get_free_agents_team
from .player_generator import assign_slots, draw_preferred_roles, draw_stats
from .roster_importer import (
    ALL_COLUMNS,
    ParsedRoster,
    RosterImportError,
    RowError,
    parse_roster_csv,
)
from .role_benchmarks import (
    ROLES,
    STAT_KEYS,
    compute_career_stat_for_role,
    compute_role_benchmarks,
    player_position,
)
from .role_benchmarks_cache import get_all_benchmark_data

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser


# Small helpers ------------------------------------------------------------


def player_form_initial() -> dict[str, object]:
    """Initial data for a new-player ``PlayerForm``.

    The form's own field initials (the 19 stat defaults of 50, from the model)
    plus one ``_random_player_profile()`` draw (name, age, started_playing_age,
    total_games, home_site, height). The HTML ``player_add`` GET and the API
    add-player defaults both use this, so they agree on every omitted field.
    """
    return {
        **{
            name: field.initial
            for name, field in PlayerForm.base_fields.items()
            if field.initial is not None
        },
        **_random_player_profile(),
    }


def annotate_overall_rating(qs: QuerySet[Player]) -> QuerySet[Player]:
    """Annotate ``overall_rating_db`` — the unweighted mean of the 19 stats."""
    return qs.annotate(
        overall_rating_db=(
            F("player_awareness")
            + F("game_awareness")
            + F("resource_awareness")
            + F("decision_making")
            + F("positioning")
            + F("stamina")
            + F("speed")
            + F("flexibility")
            + F("adaptability")
            + F("communication")
            + F("teamwork")
            + F("Offensive_synergy")
            + F("defensive_synergy")
            + F("midfield_synergy")
            + F("resupply_synergy")
            + F("resupply_efficiency")
            + F("accuracy")
            + F("survival")
            + F("special_usage")
        )
        / 19.0
    )


# Two example rows demonstrating: (a) team grouping (both rows on
# "Red Phoenix"), (b) comma-split preferred_roles cell ("scout,medic"),
# and (c) a quoted CSV cell containing a comma. ``csv.writer`` auto-
# quotes the cell — never hand-format CSV here.
_ROSTER_TEMPLATE_ROWS: tuple[tuple[str, ...], ...] = (
    (
        "Red Phoenix",
        "Alice",
        "commander",
        "28",
        "16",
        "120",
        "Ultrazone Chicago",
        "5'7\"",
        "commander",
        "75",
        "70",
        "65",
        "80",
        "72",
        "68",
        "74",
        "66",
        "71",
        "78",
        "80",
        "82",
        "55",
        "60",
        "50",
        "60",
        "65",
        "72",
        "55",
    ),
    (
        "Red Phoenix",
        "Bob",
        "scout",
        "24",
        "18",
        "85",
        "Ultrazone Chicago",
        "5'10\"",
        "scout,medic",
        "60",
        "62",
        "58",
        "70",
        "65",
        "80",
        "85",
        "78",
        "72",
        "65",
        "68",
        "55",
        "62",
        "60",
        "58",
        "55",
        "82",
        "75",
        "60",
    ),
)


def build_roster_template_csv() -> str:
    """The canonical roster CSV template body.

    Header row in :data:`ALL_COLUMNS` order plus two example data rows,
    serialised via :class:`csv.writer` so a comma inside a quoted cell
    (``"scout,medic"``) is encoded correctly.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(ALL_COLUMNS)
    for row in _ROSTER_TEMPLATE_ROWS:
        writer.writerow(row)
    return buf.getvalue()


# LG-00 generation helpers -------------------------------------------------


def _pop_unique_name(
    pool: list[str], fallback: str, name_exists: Callable[[str], bool]
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


def _player_name_exists_on_team(team: Team) -> Callable[[str], bool]:
    """Return a closure asserting Player-name uniqueness within ``team``."""

    def _exists(name: str) -> bool:
        return Player.objects.filter(team=team, name=name).exists()

    return _exists


def _resolve_count_marker(raw: str, marker: str, low: int, high: int) -> int:
    """Resolve a `random_*` marker via stdlib ``random.randint``, else ``int(raw)``."""
    if raw == marker:
        return random.randint(low, high)
    return int(raw)


def _build_player_kwargs(rng: random.Random, mean: float, std_dev: int) -> dict:
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
    tier_means: "list[float] | None" = None,
    manager: "AbstractBaseUser | None" = None,
) -> list[Team]:
    """Create ``num_teams`` new Teams, each with ``players_per_team`` Players.

    The first 6 players in each Team fill the SM5 slot FKs via greedy
    preferred-role matching (with leftover back-fill); players 7+ remain
    on the bench. Each Player is created with ``Player.objects.create``
    so PKs are available for slot FK assignment.

    CRE-02 ``tier_means``: when ``None`` (the default) every Player of every
    Team is drawn from the flat ``mean`` — identical RNG consumption and
    identical output to pre-CRE-02 generation. When a list is supplied it must
    have exactly ``num_teams`` entries (``ValueError`` otherwise, raised before
    any DB write); the Team at 0-based creation index ``i`` then draws ALL its
    players — bench included — from ``tier_means[i]``, and ``mean`` goes unused
    for those draws.
    """
    if tier_means is not None and len(tier_means) != num_teams:
        raise ValueError(
            f"tier_means has {len(tier_means)} entries but num_teams is {num_teams}"
        )

    team_fallback = TEAM_NAMES[-1] if TEAM_NAMES else "Team"
    player_fallback = PLAYER_NAMES[-1] if PLAYER_NAMES else "Player"
    created_teams: list[Team] = []

    for _team_idx in range(num_teams):
        team_name = _pop_unique_name(team_names_pool, team_fallback, _team_name_exists)
        team = Team.objects.create(name=team_name, manager=manager)
        team_name_exists = _player_name_exists_on_team(team)
        # CRE-02 — this Team's tier mean, or the flat league mean when no
        # spread was requested.
        team_mean = mean if tier_means is None else tier_means[_team_idx]

        created_players: list[Player] = []
        for _player_idx in range(players_per_team):
            player_name = _pop_unique_name(
                player_names_pool, player_fallback, team_name_exists
            )
            player = Player.objects.create(
                team=team,
                name=player_name,
                **_build_player_kwargs(rng, team_mean, std_dev),
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
    team: "Team | None" = None,
) -> int:
    """Create ``players_per_team`` Players on a free-agent pool Team.

    ``team`` is the destination pool Team; when ``None`` it defaults to the
    reserved global Free Agents Team (LG-00 sandbox pool). League-create
    passes a dedicated per-League pool Team so each League owns its own
    free agents. Players land via ``bulk_create`` — the Free-Agents branch
    has no per-Player slot-FK step, so PKs after creation are not needed,
    and one INSERT replaces N. Returns the count actually created.
    """
    free_agents = team if team is not None else get_free_agents_team()
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


# LG-00 generation service -------------------------------------------------


@dataclasses.dataclass(frozen=True)
class GenerationResult:
    created_teams: list[Team]
    free_agent_count: int


@transaction.atomic
def run_player_generation(
    cleaned_data: dict, *, manager: "AbstractBaseUser | None"
) -> GenerationResult:
    """Resolve the ``random_*`` markers and generate Teams or the free-agent pool.

    ``cleaned_data`` is a valid ``GenerateLeagueForm``'s ``cleaned_data``.
    ``num_teams >= 1`` creates Teams stamped with ``manager``;
    ``num_teams == 0`` fills the global (never-stamped) Free Agents Team.
    """
    num_teams = _resolve_count_marker(cleaned_data["num_teams"], "random_2_10", 2, 10)
    ppt_raw = cleaned_data["players_per_team"]
    if ppt_raw == "random_team":
        players_per_team = random.randint(6, 8)
    elif ppt_raw == "random_pool":
        players_per_team = random.randint(12, 100)
    else:
        players_per_team = int(ppt_raw)
    mean = cleaned_data["mean"]
    std_dev = cleaned_data["std_dev"]

    # Two RNG sources in deliberate split: ``rng`` (a private ``random.Random``)
    # is the only RNG passed into the pure ``player_generator`` module so its
    # stat-draw / role-draw seam stays deterministic when callers seed it.
    # The service's own ``random.shuffle`` / ``random.randint`` calls are purely
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
            manager=manager,
        )
        return GenerationResult(created_teams=created_teams, free_agent_count=0)

    free_agent_count = _generate_free_agents(
        players_per_team,
        rng=rng,
        mean=mean,
        std_dev=std_dev,
        player_names_pool=player_names_pool,
    )
    return GenerationResult(created_teams=[], free_agent_count=free_agent_count)


# LG-00b roster import -----------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RosterImportResult:
    created_teams: list[Team]
    appended_teams: list[Team]
    player_count: int
    row_count: int


def resolve_import_teams(
    names: Iterable[str], *, manager: "AbstractBaseUser | None"
) -> dict[str, Team]:
    """Map each referenced team name to the existing Team an import may touch.

    Scope is the Account's **own + Unmanaged** Teams (``owned_queryset``
    semantics; ``manager=None`` → Unmanaged only). Another Account's Team is
    never returned, so an import can neither append to it nor leak its
    players' names in a collision message. ``Team.name`` is not DB-unique:
    the highest id wins for a duplicated name.
    """
    teams = owned_queryset(Team.objects.filter(name__in=list(names)), manager)
    return {t.name: t for t in teams.order_by("id")}


def _check_db_slot_collisions(
    parsed: ParsedRoster, *, manager: "AbstractBaseUser | None"
) -> None:
    """Raise :class:`RosterImportError` if any imported row would collide
    with an already-filled slot on an existing ``Team``.

    Iterates ``parsed.by_team`` in CSV-encounter order. Teams that do not
    yet exist — or exist only under another Account — cannot collide (a new
    Team will be created by :func:`_apply_roster`). For existing teams, a
    non-Scout role collides when ``team.slot_<role> is not None``; Scouts
    collide when both Scout slots are already filled and the CSV asks for
    more.
    """
    # One query for all referenced teams, ownership-scoped (ANG-01a).
    teams_by_name = resolve_import_teams(parsed.by_team, manager=manager)

    errors: list[RowError] = []
    for team_name, rows in parsed.by_team.items():
        team = teams_by_name.get(team_name)
        if team is None:
            continue

        # Track in-call simulated assignments so multiple Scouts on the
        # same team don't all race for the first free slot.
        scout_free = [team.slot_scout_1 is None, team.slot_scout_2 is None]

        for row in rows:
            if row.role == "scout":
                # Find the next free Scout slot.
                if not any(scout_free):
                    existing_names = [
                        s.name
                        for s in (team.slot_scout_1, team.slot_scout_2)
                        if s is not None
                    ]
                    filled_label = " / ".join(existing_names) or "<unknown>"
                    errors.append(
                        RowError(
                            row_num=row.row_num,
                            field="role",
                            message=(
                                f"Team '{team_name}' Scout slots already "
                                f"filled by player(s) '{filled_label}'"
                            ),
                        )
                    )
                    continue
                # Mark the first free Scout slot as taken in our shadow.
                for i, free in enumerate(scout_free):
                    if free:
                        scout_free[i] = False
                        break
            else:
                slot_key = f"slot_{row.role}"
                current = getattr(team, slot_key, None)
                if current is not None:
                    errors.append(
                        RowError(
                            row_num=row.row_num,
                            field="role",
                            message=(
                                f"Team '{team_name}' slot '{slot_key}' "
                                f"already filled by player '{current.name}'"
                            ),
                        )
                    )

    if errors:
        raise RosterImportError(errors)


def _apply_roster(
    parsed: ParsedRoster, *, manager: "AbstractBaseUser | None"
) -> tuple[list[Team], list[Team], int]:
    """Persist ``parsed`` to the DB and return the audit tuple.

    Returns ``(created_teams, appended_teams, player_count)``:
      - ``created_teams`` — Teams newly created during this call (stamped
        with ``manager``), in CSV-encounter order.
      - ``appended_teams`` — pre-existing own / Unmanaged Teams that received
        new Players, in CSV-encounter order. A Team appears in exactly one
        of the two lists per call.
      - ``player_count`` — total Players created (equals ``len(parsed.rows)``).
    """
    teams_by_name = resolve_import_teams(parsed.by_team, manager=manager)

    created_teams: list[Team] = []
    appended_teams: list[Team] = []
    player_count = 0

    for team_name, rows in parsed.by_team.items():
        team = teams_by_name.get(team_name)
        if team is None:
            team = Team.objects.create(name=team_name, manager=manager)
            created_teams.append(team)
        else:
            appended_teams.append(team)

        for row in rows:
            player = Player.objects.create(
                team=team,
                name=row.name,
                preferred_roles=row.preferred_roles,
                **row.profile,
                **row.stats,
            )
            player_count += 1

            if row.role == "scout":
                if team.slot_scout_1 is None:
                    team.slot_scout_1 = player
                else:
                    team.slot_scout_2 = player
            else:
                setattr(team, f"slot_{row.role}", player)

        team.save()

    return created_teams, appended_teams, player_count


def run_roster_import(
    csv_text: str, *, manager: "AbstractBaseUser | None"
) -> RosterImportResult:
    """Parse, collision-check and apply a roster CSV, all-or-nothing.

    ``RosterImportError`` propagates only after the atomic block has rolled
    back, so a failed import never leaves partial writes.
    """
    with transaction.atomic():
        parsed = parse_roster_csv(csv_text)
        _check_db_slot_collisions(parsed, manager=manager)
        created, appended, player_count = _apply_roster(parsed, manager=manager)
    return RosterImportResult(
        created_teams=created,
        appended_teams=appended,
        player_count=player_count,
        row_count=len(parsed.rows),
    )


# HX-01 / HX-02 career stats + role benchmarks (ANG-01b) -------------------

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


def build_career_stats(
    player: Player,
    *,
    min_rounds: int,
    display: str,
    user: "AbstractBaseUser | None",
) -> dict:
    """HX-01 + HX-02 career payload for one Player (the HTML context minus ``player``).

    Aggregates every ``PlayerRoundState`` for the player into career totals,
    the per-role breakdown and the rolling-mean trend, then overlays the
    HX-02 per-role benchmarks (only when the player has rounds). UXP-14: the
    benchmark population is the Rounds ``user`` may see (own + Unmanaged).
    """
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

    # Build per-role benchmark overlays only when the player has rounds —
    # otherwise the per-role table doesn't render at all.
    per_role_with_benchmarks: list[dict] = []
    if total_rounds > 0:
        samples_by_key, rounds_in_role_by_role = get_all_benchmark_data(user=user)
        per_role_with_benchmarks = _build_per_role_overlay(
            rounds,
            per_role,
            samples_by_key,
            rounds_in_role_by_role,
            player.id,
            min_rounds,
        )

    return {
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


def build_role_benchmarks(
    *, min_rounds: int, display: str, user: "AbstractBaseUser | None"
) -> dict:
    """HX-02 role benchmarks payload (the HTML context, verbatim).

    UXP-14: scoped to the Rounds ``user`` may see -- its own plus Unmanaged
    (ADR-0038), never another Account's. ``benchmarks_by_role`` is a
    template-only convenience the API drops.
    """
    samples_by_key, rounds_in_role_by_role = get_all_benchmark_data(user=user)
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

    return {
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
