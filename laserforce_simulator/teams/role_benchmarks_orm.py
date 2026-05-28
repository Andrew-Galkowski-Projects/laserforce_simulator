"""Role benchmarks ORM materialisation.

Owns the single full-table ``PlayerRoundState`` scan, the ``_MvpAdapter``
duck-typing layer, and the assembly of the seam-dict input the pure
``teams.role_benchmarks`` reducer consumes.

Sits below ``teams.role_benchmarks_cache`` — the cache module calls
``compute_benchmarks_uncached()`` on a miss and writes the result to the
Django cache. Splitting the materialisation out of the cache module gives
a future caller (an admin diagnostic, a CLI script, a fixture, a test
helper) a clean entry point that doesn't depend on the cache backend.

This module is the **only place the ORM scan and the MVP adapter live**;
its single public surface is ``compute_benchmarks_uncached``. The pure
``teams.role_benchmarks`` module never sees a Django model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from matches.models import PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp

from .role_benchmarks import ROLES, build_role_populations

# ---------------------------------------------------------------------------
# MVP adapter — keeps ``calculate_mvp`` happy without exposing ORM internals
# to the pure role-benchmarks reducer.
# ---------------------------------------------------------------------------


@dataclass
class _MvpGameRound:
    """The subset of ``GameRound`` attributes ``calculate_mvp`` reads on ``gr``."""

    blue_team_eliminated: bool
    red_team_eliminated: bool
    eliminated_at: int


@dataclass
class _MvpAdapter:
    """Duck-typed adapter exposing exactly the attributes ``calculate_mvp``
    reads on a ``PlayerRoundState``.

    ``get_accuracy`` is a ``@property`` because the function reads it as an
    attribute (no call); we mirror the ORM model's identical formula.
    """

    role: str
    team_color: str
    final_lives: int
    final_medic_hits: int
    enemy_nuke_cancels: int
    ally_nuke_cancels: int
    times_missiled: int
    missiles_landed: int
    specials_used: int
    own_specials_cancelled: int
    points_scored: int
    tags_made: int
    shots_missed: int
    specific_tags: dict
    game_round: _MvpGameRound = field(repr=False)

    @property
    def get_accuracy(self) -> int:
        total = self.tags_made + self.shots_missed
        if total == 0:
            return 0
        return round(self.tags_made / total * 100)


# Field names pulled from a single ``.values(...)`` scan. Mirrors every
# attribute ``_MvpAdapter`` (and downstream the benchmarks reducer) needs.
_PLAYER_STATE_FIELDS: tuple[str, ...] = (
    "id",
    "player_id",
    "role",
    "team_color",
    "final_lives",
    "final_medic_hits",
    "enemy_nuke_cancels",
    "ally_nuke_cancels",
    "times_missiled",
    "missiles_landed",
    "specials_used",
    "own_specials_cancelled",
    "points_scored",
    "tags_made",
    "shots_missed",
    "times_tagged",
    "resupplies_given",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
    "final_special",
    "specific_tags",
    "game_round__id",
    "game_round__blue_team_eliminated",
    "game_round__red_team_eliminated",
    "game_round__eliminated_at",
)


def compute_benchmarks_uncached() -> tuple[
    dict[tuple[str, str], list[tuple[int, float]]],
    dict[str, dict[int, int]],
]:
    """Run the full-table scan + adapter pipeline and return the benchmark
    populations plus the per-role rounds-played map.

    Returns ``(samples_by_key, rounds_in_role)``:

    - ``samples_by_key[(role, stat)]`` → list of ``(player_id, value)``
      tuples covering every ``(role, stat)`` in the cartesian product
      ``ROLES × STAT_KEYS``.
    - ``rounds_in_role[role]`` → ``{player_id: rounds_played_as_role}``,
      used by the view layer to threshold a population by minimum
      rounds.

    No caching, no signal interaction, no transaction wrapping. The
    cache layer wraps this for the hot path; direct callers (admin
    diagnostics, fixtures, CLI scripts) use it as-is.
    """
    rows = list(PlayerRoundState.objects.values(*_PLAYER_STATE_FIELDS))

    round_dicts: list[dict[str, Any]] = []
    for row in rows:
        adapter = _MvpAdapter(
            role=row["role"],
            team_color=row["team_color"],
            final_lives=row["final_lives"],
            final_medic_hits=row["final_medic_hits"],
            enemy_nuke_cancels=row["enemy_nuke_cancels"],
            ally_nuke_cancels=row["ally_nuke_cancels"],
            times_missiled=row["times_missiled"],
            missiles_landed=row["missiles_landed"],
            specials_used=row["specials_used"],
            own_specials_cancelled=row["own_specials_cancelled"],
            points_scored=row["points_scored"],
            tags_made=row["tags_made"],
            shots_missed=row["shots_missed"],
            specific_tags=row["specific_tags"] or {},
            game_round=_MvpGameRound(
                blue_team_eliminated=bool(row["game_round__blue_team_eliminated"]),
                red_team_eliminated=bool(row["game_round__red_team_eliminated"]),
                eliminated_at=int(row["game_round__eliminated_at"] or 0),
            ),
        )
        mvp = float(calculate_mvp(adapter))
        accuracy_pct = float(adapter.get_accuracy)

        round_dicts.append(
            {
                "player_id": row["player_id"],
                "role": row["role"],
                "points_scored": row["points_scored"],
                "tags_made": row["tags_made"],
                "times_tagged": row["times_tagged"],
                "shots_missed": row["shots_missed"],
                "final_lives": row["final_lives"],
                "resupplies_given": row["resupplies_given"],
                "missiles_landed": row["missiles_landed"],
                "specials_used": row["specials_used"],
                "follow_up_shots": row["follow_up_shots"],
                "reaction_shots": row["reaction_shots"],
                "combo_resupply_count": row["combo_resupply_count"],
                "mvp": mvp,
                "accuracy_pct": accuracy_pct,
            }
        )

    samples_by_key = build_role_populations(round_dicts)

    # Per-role rounds-played map: how many rounds player ``pid`` played in
    # each role. Used to threshold a population by min-rounds.
    rounds_in_role: dict[str, dict[int, int]] = {role: {} for role in ROLES}
    for rd in round_dicts:
        role = rd["role"]
        if role not in rounds_in_role:
            continue
        pid = rd["player_id"]
        if pid is None:
            continue
        rounds_in_role[role][pid] = rounds_in_role[role].get(pid, 0) + 1

    return samples_by_key, rounds_in_role
