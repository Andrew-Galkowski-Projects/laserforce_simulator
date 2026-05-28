"""Round scoreboard — pure aggregation over per-player dicts.

The **Round scoreboard** (CONTEXT.md, `### Analytics and review`) is the
per-player table both the HTML round-detail page and the **Round report**
PDF render. The view materialises one 27-key dict per ``PlayerRoundState``
via ``matches.views._player_row`` and hands the lists to the functions
below; both surfaces consume the same dict so they cannot drift.

This module is **pure**: no Django, no ORM, no RNG, no I/O.

See ``.claude/worktrees/round-analytics-seam-contract.md`` for the locked
seam.
"""

from typing import Iterable, Mapping

PLAYER_ROW_KEYS: tuple[str, ...] = (
    # Identity (3)
    "name",
    "role",
    "team_color",
    # Survival (4 — includes derived is_eliminated bool)
    "was_eliminated_at",
    "eliminated_timestamp",
    "is_eliminated",
    "final_lives",
    # Scoring (5)
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    # Resources (5)
    "final_shots",
    "final_special",
    "shots_used",
    "missiles_used",
    "starting_missiles",
    # Combat extras (6)
    "missiles_landed",
    "times_missiled",
    "final_medic_hits",
    "medic_lives_removed_from_nuke",
    "follow_up_shots",
    "reaction_shots",
    # Support (3)
    "resupplies_given",
    "specials_used",
    "combo_resupply_count",
    # Display arithmetic (2)
    "specific_tags_count",
    "special_cost",
)


def team_totals(player_rows: Iterable[Mapping], team_points: int) -> dict:
    """RV-03 carry-over. Per-team resource summary plus derived team values.

    Returns the 6-key dict (key order pinned):
    ``{"resupplies_given", "missiles_landed", "specials_used", "tags_made",
       "survivors", "team_points"}``.
    """
    rows = list(player_rows)
    return {
        "resupplies_given": sum(p["resupplies_given"] for p in rows),
        "missiles_landed": sum(p["missiles_landed"] for p in rows),
        "specials_used": sum(p["specials_used"] for p in rows),
        "tags_made": sum(p["tags_made"] for p in rows),
        "survivors": survivor_count(rows),
        "team_points": team_points,
    }


def survivor_count(player_rows: Iterable[Mapping]) -> int:
    """Number of players whose ``final_lives > 0``.

    Replaces the ``count_survivors`` custom template filter — single source
    of truth so HTML and PDF can't disagree on who survived.
    """
    return sum(1 for p in player_rows if p["final_lives"] > 0)


def team_eliminated(player_rows: Iterable[Mapping]) -> bool:
    """True iff no player on the team has any lives left.

    The 10,000-point team-elim bonus condition. Pinned here so the four
    template sites that currently re-evaluate ``X|count_survivors < 1``
    converge on one bool the view sets once.
    """
    return survivor_count(player_rows) < 1
