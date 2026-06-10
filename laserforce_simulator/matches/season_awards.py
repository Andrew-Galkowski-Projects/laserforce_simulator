"""LG-03 — Season-end awards (pure Python aggregation).

Computes a Season's award set from a flat list of per-``PlayerRoundState``
dicts (one entry per Round a player appeared in). The view
(``matches.league_views.season_awards``) builds these dicts — reading
``get_accuracy`` / ``get_mvp`` (properties) and resolving the team via
``team_color`` — and hands a flat list to :func:`compute_season_awards`; this
module never sees a Django object, the ORM, RNG, ``datetime``, or I/O.

Award metric definitions (pinned — see the seam contract §1.3):

============================  =================================================  ===========  =====
Award                          Metric                                            Scope        Gated
============================  =================================================  ===========  =====
``most_points``                ``SUM(points_scored)``                            any role     no
``best_accuracy``              ``MEAN(accuracy)``                                any role     YES
``kd_by_role[role]``           ``SUM(tags_made) / max(SUM(times_tagged), 1)``    per role     no
``best_medic``                 ``SUM(resupplies_given)``                         medic only   no
``most_efficient_nuke``        ``(SUM(specials_used) − SUM(own_specials_         commander    YES
                               cancelled)) / max(SUM(specials_used), 1)``        only
``season_mvp``                 ``MEAN(mvp)``                                     any role     YES
============================  =================================================  ===========  =====

The **rate / mean** awards (``season_mvp``, ``best_accuracy``,
``most_efficient_nuke``) gate on ``games(player) >= min_games``; the
**total / count** awards (``most_points``, ``best_medic``, ``kd_by_role``) are
NOT gated.

Tiebreak ladder (deterministic, LOCKED): primary metric value desc →
``games_played`` desc → ``player_id`` asc.

``finals_mvp`` is computed separately by the view via :func:`pick_finals_mvp`
and stamped onto the :class:`AwardSet` (the pure fn returns ``finals_mvp=None``).

See ``.claude/worktrees/lg-03-season-awards-seam-contract.md`` for the locked
seam.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

# The 5 role strings as stored on ``PlayerRoundState.role`` — one ``kd_by_role``
# entry per role, in this fixed order.
ROLE_KEYS: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")


@dataclass(frozen=True)
class AwardWinner:
    """One award winner (player identity + the metric value that won it)."""

    player_id: int
    player_name: str
    role: str
    team_id: int
    team_name: str
    value: float


@dataclass(frozen=True)
class AwardSet:
    """The full set of a Season's awards.

    Every slot is ``Optional[AwardWinner]`` — ``None`` when no qualifying
    player exists. ``kd_by_role`` is a mapping with EXACTLY 5 role keys
    (:data:`ROLE_KEYS`), each value an ``Optional[AwardWinner]`` (``None``
    when no player played that role). ``compute_season_awards`` always
    returns ``finals_mvp=None``; the view stamps the
    :func:`pick_finals_mvp` result via ``dataclasses.replace``.
    """

    most_points: Optional[AwardWinner]
    best_accuracy: Optional[AwardWinner]
    kd_by_role: dict[str, Optional[AwardWinner]]
    best_medic: Optional[AwardWinner]
    most_efficient_nuke: Optional[AwardWinner]
    season_mvp: Optional[AwardWinner]
    finals_mvp: Optional[AwardWinner]


def _winner_from_rows(
    rows: list[dict],
    *,
    value: float,
) -> AwardWinner:
    """Build an :class:`AwardWinner` from a player's grouped rows.

    ``rows`` is one player's group (the view passes rows id-ascending, so
    "last row wins" gives the most-recent name / role / team).
    """
    last = rows[-1]
    return AwardWinner(
        player_id=last["player_id"],
        player_name=last["player_name"],
        role=last["role"],
        team_id=last["team_id"],
        team_name=last["team_name"],
        value=value,
    )


def _best(
    grouped: dict[int, list[dict]],
    *,
    value_fn: Callable[[list[dict]], float],
    eligible_fn: Optional[Callable[[list[dict]], bool]] = None,
    min_games: Optional[int] = None,
) -> Optional[AwardWinner]:
    """Return the winning :class:`AwardWinner`, or ``None``.

    ``value_fn(rows) -> float`` computes the metric for a player's grouped
    rows. ``eligible_fn(rows) -> bool`` (optional) filters which players are
    candidates (e.g. role gate). ``min_games`` (optional) gates rate awards
    on ``games(player) >= min_games``.

    Tiebreak ladder (LOCKED): value desc → games_played desc → player_id asc.
    Mirrors ``league_leaders_logic._rank``'s ``(-value, -games, player_id)``.
    """
    candidates: list[tuple[float, int, int, list[dict]]] = []
    for player_id, rows in grouped.items():
        if eligible_fn is not None and not eligible_fn(rows):
            continue
        games_played = len(rows)
        if min_games is not None and games_played < min_games:
            continue
        value = float(value_fn(rows))
        candidates.append((value, games_played, player_id, rows))

    if not candidates:
        return None

    # value desc → games desc → player_id asc.
    candidates.sort(key=lambda t: (-t[0], -t[1], t[2]))
    best_value, _games, _pid, best_rows = candidates[0]
    return _winner_from_rows(best_rows, value=best_value)


def compute_season_awards(
    player_rounds: list[dict],
    *,
    min_games: int,
) -> AwardSet:
    """Compute a Season's :class:`AwardSet` from a flat list of seam dicts.

    ``player_rounds`` is one dict per ``PlayerRoundState`` round-appearance,
    each carrying the required keys ``player_id``, ``player_name``, ``role``,
    ``team_id``, ``team_name``, ``points_scored``, ``tags_made``,
    ``times_tagged``, ``accuracy``, ``mvp``, ``resupplies_given``,
    ``specials_used``, ``own_specials_cancelled``.

    ``min_games`` is the keyword-only qualifier (the view passes
    ``ceil(max_games_any_player / 2)``); it gates ONLY the rate / mean awards
    (``season_mvp`` / ``best_accuracy`` / ``most_efficient_nuke``).

    Empty input ⇒ every slot ``None``, all 5 ``kd_by_role`` entries ``None``.
    ``finals_mvp`` is always ``None`` (the view stamps it separately).
    """
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in player_rounds:
        grouped[row["player_id"]].append(row)

    # --- metric value functions ---
    def points_total(rows: list[dict]) -> float:
        return sum(r["points_scored"] for r in rows)

    def accuracy_mean(rows: list[dict]) -> float:
        return sum(r["accuracy"] for r in rows) / len(rows)

    def kd_ratio(rows: list[dict]) -> float:
        total_tags = sum(r["tags_made"] for r in rows)
        total_tagged = sum(r["times_tagged"] for r in rows)
        return total_tags / max(total_tagged, 1)

    def resupplies_total(rows: list[dict]) -> float:
        return sum(r["resupplies_given"] for r in rows)

    def nuke_efficiency(rows: list[dict]) -> float:
        total_used = sum(r["specials_used"] for r in rows)
        total_cancelled = sum(r["own_specials_cancelled"] for r in rows)
        return (total_used - total_cancelled) / max(total_used, 1)

    def mvp_mean(rows: list[dict]) -> float:
        return sum(r["mvp"] for r in rows) / len(rows)

    # --- role-eligibility predicates (last-row-wins role) ---
    def is_role(role: str):
        return lambda rows: rows[-1]["role"] == role

    most_points = _best(grouped, value_fn=points_total)
    best_accuracy = _best(grouped, value_fn=accuracy_mean, min_games=min_games)
    best_medic = _best(grouped, value_fn=resupplies_total, eligible_fn=is_role("medic"))
    most_efficient_nuke = _best(
        grouped,
        value_fn=nuke_efficiency,
        eligible_fn=is_role("commander"),
        min_games=min_games,
    )
    season_mvp = _best(grouped, value_fn=mvp_mean, min_games=min_games)

    kd_by_role: dict[str, Optional[AwardWinner]] = {
        role: _best(grouped, value_fn=kd_ratio, eligible_fn=is_role(role))
        for role in ROLE_KEYS
    }

    return AwardSet(
        most_points=most_points,
        best_accuracy=best_accuracy,
        kd_by_role=kd_by_role,
        best_medic=best_medic,
        most_efficient_nuke=most_efficient_nuke,
        season_mvp=season_mvp,
        finals_mvp=None,
    )


def pick_finals_mvp(final_round_dicts: list[dict]) -> Optional[AwardWinner]:
    """Return the Finals MVP — the best ``MEAN(mvp)`` over the final rounds.

    ``final_round_dicts`` is one dict per ``PlayerRoundState`` over the
    championship Match's rounds — the same per-round shape as the
    :func:`compute_season_awards` seam dicts; the ``mvp`` key is required
    (the other keys carry identity). Value is the player's mean ``mvp``,
    tiebroken by the same ladder (value desc → games desc → player_id asc).

    Empty input ⇒ ``None``.
    """
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in final_round_dicts:
        grouped[row["player_id"]].append(row)

    def mvp_mean(rows: list[dict]) -> float:
        return sum(r["mvp"] for r in rows) / len(rows)

    return _best(grouped, value_fn=mvp_mean)
