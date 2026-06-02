"""LG-06e — pure feat-detection module for the Statistical Feats screen.

PURE module — frozen import allowlist: ``dataclasses``, ``typing``,
``collections`` ONLY. NO Django, NO ORM, NO RNG, NO I/O. The view in
``matches/league_screens/statistical_feats.py`` materialises a list of
per-(player, round) dicts (one entry per ``PlayerRoundState`` row, plus the
parent Round's relevant event counts and view-computed Opp / Result / Season
descriptors) and hands that list to the ``scan_feats`` entry point here; this
module never touches a Django object.

LG-06e reshapes the output from the pre-existing ~9 "category-best" entries
(one row = the single best of each feat kind) into ZenGM's model: **one
sortable row per (Player, Round) that achieved a feat**, carrying that round's
box-score line + Opp / Result / Season, with the comeback-win feat as a
SEPARATE Team-feats section. See ``.claude/worktrees/lg-06e-seam-contract.md``.

Per-(player, round) input seam-dict shape (every key required; the view builds
it — Opp / Result / Season are computed VIEW-SIDE so this module stays pure):

    {
        # --- identity / deep-link ---
        "round_id": int,
        "match_id": int | None,
        "player_id": int,
        "player_name": str,
        "role": str,                 # PlayerRoundState.role
        "team_id": int | None,       # the row's own team that Round
        "team_name": str,            # "" when unresolved
        # --- descriptor columns (view-computed) ---
        "opp_team_name": str,        # the other team that Round; "" when unresolved
        "result": str,               # "W"/"L"/"T" per-ROUND (own vs opp points)
        "season_id": int | None,
        "season_name": str,
        # --- box-score line (13 BOX_SCORE_KEYS, per-round values) ---
        "points_scored": int,
        "mvp": float,
        "tags_made": int,
        "times_tagged": int,
        "accuracy": float,
        "final_lives": int,
        "resupplies_given": int,
        "missiles_landed": int,
        "specials_used": int,
        "follow_up_shots": int,
        "reaction_shots": int,
        "combo_resupply_count": int,
        "nuke_detonations": int,
        # --- predicate-only fields (NOT box-score columns) ---
        "shots_missed": int,         # for the perfect_heavy predicate
    }

Plus a parallel list of per-Match dicts for the comeback-win feat (unchanged
from the pre-LG-06e shape):

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

``scan_feats`` returns ``(feat_rows, team_feats)``: a per-(player, round) feed
in a guaranteed deterministic order (round_id DESC then player_id ASC) plus the
separate Team-feats list (comeback-win record(s)).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

# ---------------------------------------------------------------------------
# Box-score keys (the per-round values carried on each FeatRow.stats).
# The 12 STAT_KEYS from matches/season_player_stats.py (PER-ROUND, not
# aggregated) PLUS ``nuke_detonations``. Pinned tuple, exact order.
# ---------------------------------------------------------------------------
BOX_SCORE_KEYS: tuple[str, ...] = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
    "nuke_detonations",
)

# ---------------------------------------------------------------------------
# Feat-kind vocabulary — the stable (kind, label) pairs. Single source of
# truth for labels. The threshold-cross label is the base; the season-best
# variant is rendered by the template as "<label> (season best)" when the
# badge's ``is_season_best`` flag is True.
# ---------------------------------------------------------------------------
FEAT_KINDS: tuple[tuple[str, str], ...] = (
    ("triple_nuke", "Triple Nuke"),
    ("medic_shutout", "Medic Shutout"),
    ("perfect_heavy", "Perfect Heavy"),
    ("high_tags", "Tags"),
    ("high_points", "Points"),
    ("high_mvp", "MVP"),
    ("high_resupplies", "Resupplies"),
    ("high_missiles", "Missiles"),
)

# kind -> human label lookup (derived from FEAT_KINDS).
_LABEL_BY_KIND: dict[str, str] = {kind: label for kind, label in FEAT_KINDS}

# ---------------------------------------------------------------------------
# Season-best stats and their feat-kind mapping (pinned).
# ---------------------------------------------------------------------------
SEASON_BEST_STATS: tuple[str, ...] = (
    "mvp",
    "points_scored",
    "tags_made",
    "resupplies_given",
    "missiles_landed",
)

# season-best stat -> feat kind.
_SEASON_BEST_KIND: dict[str, str] = {
    "mvp": "high_mvp",
    "points_scored": "high_points",
    "tags_made": "high_tags",
    "resupplies_given": "high_resupplies",
    "missiles_landed": "high_missiles",
}

# ---------------------------------------------------------------------------
# Threshold constants (conservative starting values; tunable; calibration
# deferred). Named exactly per the seam contract.
# ---------------------------------------------------------------------------
TRIPLE_NUKE_THRESHOLD = 3  # nuke_detonations >= 3 (retained from prior module)
HIGH_TAGS_THRESHOLD = 20  # tags_made >= 20
HIGH_POINTS_THRESHOLD = 12000  # points_scored >= 12000
HIGH_MVP_THRESHOLD = 15  # mvp >= 15
HIGH_RESUPPLIES_THRESHOLD = 20  # resupplies_given >= 20
HIGH_MISSILES_THRESHOLD = 8  # missiles_landed >= 8


@dataclass(frozen=True)
class FeatBadge:
    """One reason a (player, round) row qualified.

    ``kind`` is a stable key from :data:`FEAT_KINDS` (drives the per-badge DOM
    id ``stat-feat-badge-{kind}``); ``label`` is the human-readable badge text;
    ``is_season_best`` is True for a season-best badge, False for a
    threshold-crossing badge.
    """

    kind: str
    label: str
    is_season_best: bool


@dataclass(frozen=True)
class FeatRow:
    """One (Player, Round) performance that achieved at least one feat.

    Field order is pinned. ``stats`` carries every :data:`BOX_SCORE_KEYS` key.
    ``feats`` is non-empty for every emitted row (a row with zero badges is
    never emitted).
    """

    # --- identity / deep-link ---
    player_id: int
    player_name: str
    role: str
    team_id: Optional[int]
    team_name: str
    round_id: int
    # --- descriptor columns ---
    opp_team_name: str
    result: str
    season_id: Optional[int]
    season_name: str
    # --- box-score line (per-round values) ---
    stats: Mapping[str, float]
    # --- badges (stacked) ---
    feats: tuple[FeatBadge, ...]


@dataclass(frozen=True)
class TeamFeatRecord:
    """One Team-feats record (today: comeback-win only).

    Lives in the separate Team-feats section below the per-player feed — it is
    NOT a per-player row and carries no box-score line.
    """

    kind: str
    label: str
    team_name: str
    round_id: Optional[int]


def _threshold_badges(row: dict) -> dict[str, FeatBadge]:
    """Collect the threshold-crossing badges a (player, round) qualifies for.

    Returns a ``{kind: FeatBadge}`` map (one badge per kind, all
    ``is_season_best=False``). A row may cross several thresholds → several
    distinct kinds.
    """
    badges: dict[str, FeatBadge] = {}

    def add(kind: str) -> None:
        badges[kind] = FeatBadge(
            kind=kind, label=_LABEL_BY_KIND[kind], is_season_best=False
        )

    if int(row.get("nuke_detonations", 0)) >= TRIPLE_NUKE_THRESHOLD:
        add("triple_nuke")
    if row.get("role") == "medic" and int(row.get("times_tagged", 0)) == 0:
        add("medic_shutout")
    if (
        row.get("role") == "heavy"
        and int(row.get("shots_missed", 0)) == 0
        and int(row.get("tags_made", 0)) > 0
    ):
        add("perfect_heavy")
    if int(row.get("tags_made", 0)) >= HIGH_TAGS_THRESHOLD:
        add("high_tags")
    if int(row.get("points_scored", 0)) >= HIGH_POINTS_THRESHOLD:
        add("high_points")
    if float(row.get("mvp", 0)) >= HIGH_MVP_THRESHOLD:
        add("high_mvp")
    if int(row.get("resupplies_given", 0)) >= HIGH_RESUPPLIES_THRESHOLD:
        add("high_resupplies")
    if int(row.get("missiles_landed", 0)) >= HIGH_MISSILES_THRESHOLD:
        add("high_missiles")

    return badges


def _season_best_keys(player_rounds: list[dict]) -> dict[tuple[int, int], set[str]]:
    """For each of the 5 SEASON_BEST_STATS, find the single best (player, round).

    Returns a ``{(round_id, player_id): {kind, …}}`` map of season-best feat
    kinds. "Best" = the single highest value of that stat in the whole pool.
    Tiebreak: highest value → highest round_id → lowest player_id. A stat whose
    pool maximum is ``0`` produces NO season-best badge (locked skip — an
    all-zero stat has no meaningful leader).
    """
    out: dict[tuple[int, int], set[str]] = {}
    if not player_rounds:
        return out

    for stat in SEASON_BEST_STATS:
        best_row: Optional[dict] = None
        best_value: Optional[float] = None
        for row in player_rounds:
            value = float(row.get(stat, 0))
            if best_row is None:
                best_row, best_value = row, value
                continue
            # Tiebreak: value desc, then round_id desc, then player_id asc.
            candidate = (
                value,
                int(row.get("round_id", 0)),
                -int(row.get("player_id", 0)),
            )
            incumbent = (
                best_value,
                int(best_row.get("round_id", 0)),
                -int(best_row.get("player_id", 0)),
            )
            if candidate > incumbent:
                best_row, best_value = row, value

        # Skip the season-best badge when the pool maximum is 0 (locked).
        if best_row is None or best_value is None or best_value <= 0:
            continue
        key = (int(best_row.get("round_id", 0)), int(best_row.get("player_id", 0)))
        out.setdefault(key, set()).add(_SEASON_BEST_KIND[stat])

    return out


def find_comeback_win(matches: list[dict]) -> list[TeamFeatRecord]:
    """A team that won the Match after losing round 1.

    DEFINITION (unchanged): the Match has a winner whose round-1 score was
    strictly LOWER than the opponent's round-1 score. Input is id-ascending, so
    the LAST qualifying Match is the most recent — that one is returned. Returns
    a list (0 or 1 record today) so the Team-feats section render is uniform.
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
        return []
    return [
        TeamFeatRecord(
            kind="comeback_win",
            label="Comeback win (won the match after losing round 1)",
            team_name=chosen.get("winner_team_name") or "",
            round_id=chosen.get("round_id"),
        )
    ]


def scan_feats(
    player_rounds: list[dict],
    matches: list[dict],
) -> tuple[list[FeatRow], list[TeamFeatRecord]]:
    """Build the per-(player, round) feat feed + the team-feats list.

    Returns ``(feat_rows, team_feats)``:

    * ``feat_rows`` — one :class:`FeatRow` per qualifying (player, round) in the
      module's guaranteed deterministic order (round_id DESC then player_id
      ASC). A row qualifies (hybrid) iff it crosses ANY threshold feat OR is the
      season-best leader for any of the 5 :data:`SEASON_BEST_STATS`. Badges
      stack per row; a row that both crosses a threshold AND leads the season
      for the SAME kind collapses to ONE badge with ``is_season_best=True``.
    * ``team_feats`` — the comeback-win record(s) for the separate Team-feats
      section (today: zero or one record via :func:`find_comeback_win`).
    """
    season_best = _season_best_keys(player_rounds)

    rows: list[FeatRow] = []
    for row in player_rounds:
        key = (int(row.get("round_id", 0)), int(row.get("player_id", 0)))
        badges_by_kind = _threshold_badges(row)

        # Overlay season-best badges; is_season_best=True wins per kind (locked
        # collapse — a row both ≥ threshold and the season leader for the same
        # kind carries ONE badge tagged season-best).
        for kind in season_best.get(key, ()):  # type: ignore[arg-type]
            badges_by_kind[kind] = FeatBadge(
                kind=kind, label=_LABEL_BY_KIND[kind], is_season_best=True
            )

        if not badges_by_kind:
            continue

        # Order badges by the canonical FEAT_KINDS order for stable rendering.
        feats = tuple(
            badges_by_kind[kind] for kind, _ in FEAT_KINDS if kind in badges_by_kind
        )

        stats = {sk: row.get(sk, 0) for sk in BOX_SCORE_KEYS}
        rows.append(
            FeatRow(
                player_id=int(row.get("player_id", 0)),
                player_name=row.get("player_name", ""),
                role=row.get("role", ""),
                team_id=row.get("team_id"),
                team_name=row.get("team_name", ""),
                round_id=int(row.get("round_id", 0)),
                opp_team_name=row.get("opp_team_name", ""),
                result=row.get("result", ""),
                season_id=row.get("season_id"),
                season_name=row.get("season_name", ""),
                stats=stats,
                feats=feats,
            )
        )

    # Deterministic default order: round_id DESC, then player_id ASC.
    rows.sort(key=lambda r: (-r.round_id, r.player_id))

    team_feats = find_comeback_win(matches)
    return rows, team_feats
