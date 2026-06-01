"""LG-01z-q — pure feat-detection module for the Statistical Feats screen.

PURE module — frozen import allowlist: ``dataclasses``, ``typing``,
``collections`` ONLY. NO Django, NO ORM, NO RNG, NO I/O. The view in
``matches/league_screens/statistical_feats.py`` materialises a list of
per-Round dicts (one entry per ``PlayerRoundState`` row, plus the parent
Round's relevant event counts and Match context) and hands that list to the
``scan_feats`` entry point here; this module never touches a Django object.

Per-Round row dict shape (every key required; the view builds it):

    {
        # --- identity / context ---
        "round_id": int,                # GameRound.id (deep-link target)
        "match_id": int | None,         # Match.id, None for standalone rounds
        "player_id": int,
        "player_name": str,
        "team_id": int | None,
        "team_name": str,               # "" when unresolved
        "role": str,                    # PlayerRoundState.role
        # --- PlayerRoundState performance ---
        "tags_made": int,
        "times_tagged": int,
        "shots_missed": int,
        "points_scored": int,
        "resupplies_given": int,
        "missiles_landed": int,
        "mvp": float,                   # PlayerRoundState.get_mvp
        # --- per-Round event-derived counts (this player as actor) ---
        "nuke_detonations": int,        # `special` nuke-detonation events
    }

Plus a parallel list of per-Match dicts for the comeback-win feat (one entry
per completed Match in the Season):

    {
        "match_id": int,
        "round_id": int | None,         # round 2's GameRound.id (deep-link)
        "winner_team_id": int | None,
        "winner_team_name": str,        # "" when unresolved / tie
        "red_team_id": int | None,
        "blue_team_id": int | None,
        "red_round1_points": int,
        "blue_round1_points": int,
    }

Each detected feat is returned as a ``FeatRecord`` (see below): ``kind`` (the
stable feat key driving the per-feat DOM id ``stat-feat-{kind}``), ``label``
(human-readable summary), ``name`` (player or team name), ``value`` (the
feat's headline figure as a string), and ``round_id`` (the deep-link target,
may be ``None`` when no single Round is the natural anchor).

Feat definitions (as implemented — see also the screen's docstring):

* ``triple_nuke``      — a player detonating >= 3 nukes in a single Round.
                         Derived from the per-Round ``nuke_detonations`` count
                         (one `special` nuke-detonation GameEvent per actor).
                         Attributed to the player/actor (the contract allows
                         "team/player"; the persisted actor on the event is a
                         player, so we attribute per-player — the cleanest
                         derivation). One record per qualifying (player, round).
* ``medic_shutout``    — role == "medic" with times_tagged == 0 in a Round the
                         player actually played (the row's mere presence proves
                         the player was in the Round). Best single such feat by
                         most tags_made (ties → highest mvp), one record.
* ``perfect_heavy``    — role == "heavy", shots_missed == 0, tags_made > 0
                         (a perfect-accuracy Heavy round). Best by tags_made,
                         one record.
* ``top_mvp``          — the single highest get_mvp across all player-rounds.
* ``top_score``        — the single highest points_scored across all rows.
* ``tag_streak``       — BEST-EFFORT. A true consecutive-tag streak (N tags
                         with no intervening death/miss) is NOT cleanly
                         derivable: the persisted data exposes per-Round
                         aggregate ``tags_made`` only, not the ordered tag/miss
                         timeline at the granularity needed to reconstruct an
                         uninterrupted run. We therefore APPROXIMATE the streak
                         as the most tags_made by a single player in a single
                         Round (the per-Round tag count). Documented as an
                         approximation in the label.
* ``most_resupplies``  — the single highest resupplies_given in a Round.
* ``most_missiles``    — the single highest missiles_landed in a Round.
* ``comeback_win``     — a team that WON the Match after LOSING round 1 (its
                         round-1 score was strictly lower than the opponent's).
                         Derived from the per-Match dicts. One record (the
                         most recent / first qualifying Match by input order;
                         input is id-ascending so "last" == most recent).

``scan_feats`` returns the records in a STABLE display order (the order the
predicates are listed above) so the template renders deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Minimum nuke detonations by one actor in one Round to count as a triple-nuke.
TRIPLE_NUKE_THRESHOLD = 3


@dataclass(frozen=True)
class FeatRecord:
    """One detected statistical feat.

    ``kind`` is the stable feat key (drives the ``stat-feat-{kind}`` DOM id).
    ``round_id`` is the deep-link target (``None`` when no single Round is the
    natural anchor for the feat).
    """

    kind: str
    label: str
    name: str
    value: str
    round_id: Optional[int]


# ---------------------------------------------------------------------------
# Per-feat predicate / scan functions (one per feat).
# Each consumes the player-round dict list (and, for comeback, the match list)
# and returns a single ``FeatRecord`` or ``None`` (no qualifying data).
# ``triple_nuke`` returns a list (zero-or-more qualifying player-rounds).
# ---------------------------------------------------------------------------


def find_triple_nukes(player_rounds: list[dict]) -> list[FeatRecord]:
    """All player-rounds with >= TRIPLE_NUKE_THRESHOLD nuke detonations.

    One record per qualifying (player, round). Sorted by detonation count
    desc, then mvp desc, then player_name asc for a stable order.
    """
    qualifying = [
        row
        for row in player_rounds
        if int(row.get("nuke_detonations", 0)) >= TRIPLE_NUKE_THRESHOLD
    ]
    qualifying.sort(
        key=lambda r: (
            -int(r["nuke_detonations"]),
            -float(r["mvp"]),
            r["player_name"],
        )
    )
    out: list[FeatRecord] = []
    for row in qualifying:
        count = int(row["nuke_detonations"])
        out.append(
            FeatRecord(
                kind="triple_nuke",
                label=f"{count} nukes detonated in a single round",
                name=row["player_name"],
                value=str(count),
                round_id=row["round_id"],
            )
        )
    return out


def find_medic_shutout(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Best Medic round with zero times_tagged (a 0-tagged shutout).

    Only rows the player actually played count — the row's presence in the
    list is proof of participation. Best by tags_made desc, mvp desc.
    """
    candidates = [
        row
        for row in player_rounds
        if row["role"] == "medic" and int(row["times_tagged"]) == 0
    ]
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda r: (int(r["tags_made"]), float(r["mvp"])),
    )
    return FeatRecord(
        kind="medic_shutout",
        label="Medic survived a round untagged (0 times tagged)",
        name=best["player_name"],
        value=f"{int(best['tags_made'])} tags, 0 tagged",
        round_id=best["round_id"],
    )


def find_perfect_heavy(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Best perfect-accuracy Heavy round (shots_missed == 0, tags_made > 0)."""
    candidates = [
        row
        for row in player_rounds
        if row["role"] == "heavy"
        and int(row["shots_missed"]) == 0
        and int(row["tags_made"]) > 0
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda r: int(r["tags_made"]))
    return FeatRecord(
        kind="perfect_heavy",
        label="Heavy with perfect accuracy (no missed shots)",
        name=best["player_name"],
        value=f"{int(best['tags_made'])} tags, 0 misses",
        round_id=best["round_id"],
    )


def find_top_mvp(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Single highest get_mvp across all player-rounds."""
    if not player_rounds:
        return None
    best = max(player_rounds, key=lambda r: float(r["mvp"]))
    return FeatRecord(
        kind="top_mvp",
        label="Highest single-game MVP score",
        name=best["player_name"],
        value=f"{float(best['mvp']):.1f}",
        round_id=best["round_id"],
    )


def find_top_score(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Single highest points_scored across all player-rounds."""
    if not player_rounds:
        return None
    best = max(player_rounds, key=lambda r: int(r["points_scored"]))
    return FeatRecord(
        kind="top_score",
        label="Highest single-game score",
        name=best["player_name"],
        value=str(int(best["points_scored"])),
        round_id=best["round_id"],
    )


def find_tag_streak(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Longest tag streak — APPROXIMATED as most tags in a single Round.

    A true consecutive-tag streak is not derivable from the persisted
    per-Round aggregate ``tags_made`` (the ordered tag/miss timeline at the
    needed granularity is not exposed across the seam), so this uses the
    per-Round tag count as a best-effort proxy. Documented in the label.
    """
    if not player_rounds:
        return None
    best = max(player_rounds, key=lambda r: int(r["tags_made"]))
    if int(best["tags_made"]) <= 0:
        return None
    return FeatRecord(
        kind="tag_streak",
        label="Longest tag streak (most tags in a single round)",
        name=best["player_name"],
        value=str(int(best["tags_made"])),
        round_id=best["round_id"],
    )


def find_most_resupplies(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Single highest resupplies_given in a Round."""
    if not player_rounds:
        return None
    best = max(player_rounds, key=lambda r: int(r["resupplies_given"]))
    if int(best["resupplies_given"]) <= 0:
        return None
    return FeatRecord(
        kind="most_resupplies",
        label="Most resupplies given in a single round",
        name=best["player_name"],
        value=str(int(best["resupplies_given"])),
        round_id=best["round_id"],
    )


def find_most_missiles(player_rounds: list[dict]) -> Optional[FeatRecord]:
    """Single highest missiles_landed in a Round."""
    if not player_rounds:
        return None
    best = max(player_rounds, key=lambda r: int(r["missiles_landed"]))
    if int(best["missiles_landed"]) <= 0:
        return None
    return FeatRecord(
        kind="most_missiles",
        label="Most missiles landed in a single round",
        name=best["player_name"],
        value=str(int(best["missiles_landed"])),
        round_id=best["round_id"],
    )


def find_comeback_win(matches: list[dict]) -> Optional[FeatRecord]:
    """A team that won the Match after losing round 1.

    DEFINITION: the Match has a winner, and that winner's round-1 score was
    strictly LOWER than the opponent's round-1 score (it was behind after
    round 1 yet won the Match overall). Input is id-ascending, so the LAST
    qualifying Match is the most recent — we return that one.
    """
    chosen: Optional[dict] = None
    for match in matches:
        winner_id = match.get("winner_team_id")
        if winner_id is None:
            continue
        red_id = match.get("red_team_id")
        blue_id = match.get("blue_team_id")
        red_r1 = int(match.get("red_round1_points", 0))
        blue_r1 = int(match.get("blue_round1_points", 0))
        if winner_id == red_id:
            lost_round1 = red_r1 < blue_r1
        elif winner_id == blue_id:
            lost_round1 = blue_r1 < red_r1
        else:
            # Defensive: winner id matches neither side — skip.
            continue
        if lost_round1:
            chosen = match  # keep iterating; last qualifying == most recent
    if chosen is None:
        return None
    return FeatRecord(
        kind="comeback_win",
        label="Comeback win (won the match after losing round 1)",
        name=chosen.get("winner_team_name") or "",
        value="Comeback",
        round_id=chosen.get("round_id"),
    )


def scan_feats(player_rounds: list[dict], matches: list[dict]) -> list[FeatRecord]:
    """Run every feat predicate and return the records in display order.

    The single-record feats appear at most once; ``triple_nuke`` may appear
    zero-or-more times. Ordering is stable (the order the feats are listed
    in the module docstring) so the template renders deterministically.
    """
    records: list[FeatRecord] = []

    records.extend(find_triple_nukes(player_rounds))

    for finder in (
        find_medic_shutout,
        find_perfect_heavy,
        find_top_mvp,
        find_top_score,
        find_tag_streak,
        find_most_resupplies,
        find_most_missiles,
    ):
        rec = finder(player_rounds)
        if rec is not None:
            records.append(rec)

    comeback = find_comeback_win(matches)
    if comeback is not None:
        records.append(comeback)

    return records
