"""ANG-01b — Match / Round domain services shared by the HTML views and the API.

Sandbox create validation, batch-team resolution, and the six read-only
``build_*`` builders behind RES-03 (missile log), RES-04 (heatmap), RV-01
(compare rounds), RV-03 (round report), HX-03 (team head-to-head) and HX-04
(player head-to-head). The HTML views delegate here so both surfaces run one
code path; HTML output stays byte-identical.

This module must NOT import ``matches.views`` (``matches.views`` imports it).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING

from django.db.models import Q, QuerySet
from django.http import Http404
from django.urls import reverse
from django.utils.text import slugify

from accounts.permissions import owned_queryset, stamp_manager
from teams.models import Player, Team

from . import (
    h2h_stats,
    missile_log_stats,
    player_h2h_stats,
    round_comparison,
    round_summary,
)
from .models import GameEvent, GameRound, Match, PlayerRoundState
from .sim_helpers.round_series import build_round_series
from .simulation import BatchSimulator

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

    from core.models import ArenaMap


class MatchSetupError(Exception):
    """A matchup the sandbox refuses. ``str(exc)`` is the exact user-facing message."""


def _scope_user(user: "AbstractBaseUser | None") -> "AbstractBaseUser | None":
    """``user`` when authenticated, else ``None`` (Unmanaged rows only)."""
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


# ---------------------------------------------------------------------------
# Sandbox create / batch validation
# ---------------------------------------------------------------------------


def check_matchup(team_red: Team, team_blue: Team) -> None:
    """Raise ``MatchSetupError`` for a matchup the sandbox refuses.

    Checks, in the HTML order: same team, then red roster, then blue roster.
    """
    if team_red == team_blue:
        raise MatchSetupError("A team cannot play against itself!")
    for team in (team_red, team_blue):
        errors = team.roster_errors
        if errors:
            raise MatchSetupError(
                f"{team.name} has an invalid roster: {'; '.join(errors)}"
            )


def resolve_batch_teams(
    team_red_id: int, team_blue_id: int, user: "AbstractBaseUser | None"
) -> tuple[Team, Team]:
    """Resolve a batch matchup's two Teams through the Account's ownership scope.

    A foreign Team is reported exactly like a missing one ("Team {tid} does
    not exist."), so another Account's Team's existence is never revealed.
    Uses ``Team.objects.all()`` (not ``regular()``) so a Free Agents Team
    surfaces as a roster error, matching the ANG-01a batch behaviour.
    """
    if team_red_id == team_blue_id:
        raise MatchSetupError("A team cannot play against itself!")
    scoped = owned_queryset(Team.objects.all(), _scope_user(user))
    teams: list[Team] = []
    for tid in (team_red_id, team_blue_id):
        team = scoped.filter(id=tid).first()
        if team is None:
            raise MatchSetupError(f"Team {tid} does not exist.")
        teams.append(team)
    team_red, team_blue = teams
    check_matchup(team_red, team_blue)
    return team_red, team_blue


def create_sandbox_match(
    team_red: Team,
    team_blue: Team,
    match_type: str,
    *,
    arena_map: "ArenaMap | None",
    user: "AbstractBaseUser | None",
) -> Match:
    """Validate, simulate and persist a 2-round sandbox Match, stamped to ``user``."""
    check_matchup(team_red, team_blue)
    try:
        match = BatchSimulator().simulate_match(
            team_red, team_blue, match_type, arena_map=arena_map
        )
    except ValueError as exc:
        raise MatchSetupError(str(exc)) from exc
    # UX-01 — post-hoc stamp: the simulator persists the Match, so the
    # **Manager** is set on the returned row rather than threaded through
    # BatchSimulator.
    return stamp_manager(match, user)


def create_sandbox_round(
    team_red: Team,
    team_blue: Team,
    *,
    arena_map: "ArenaMap | None",
    user: "AbstractBaseUser | None",
) -> GameRound:
    """Validate, simulate and persist a standalone sandbox Round, stamped to ``user``."""
    check_matchup(team_red, team_blue)
    try:
        game_round = BatchSimulator().simulate_single_round_detailed(
            team_red, team_blue, arena_map=arena_map
        )
    except ValueError as exc:
        raise MatchSetupError(str(exc)) from exc
    return stamp_manager(game_round, user)


# ---------------------------------------------------------------------------
# RV-01 round comparison
# ---------------------------------------------------------------------------


def shared_team_ids(round_a: GameRound, round_b: GameRound) -> list[int]:
    """RV-01: sorted list of Team ids present in both rounds, ignoring Side.

    A team counts as shared whether it played red in one round and blue in the
    other — the comparison is by team identity, not physical Side.
    """
    ids_a = {round_a.team_red_id, round_a.team_blue_id}
    ids_b = {round_b.team_red_id, round_b.team_blue_id}
    return sorted(ids_a & ids_b)


def _comparison_row(ps: PlayerRoundState) -> dict:
    """RV-01: build one per-PlayerRoundState comparison-row dict (16 keys).

    The 12 ``COMPARE_STAT_KEYS`` are flat at the top level so
    ``round_comparison.stat_values`` can pick them up directly.
    """
    return {
        "player_id": ps.player_id,
        "name": ps.player.name,
        "role": ps.role,
        "team_color": ps.team_color,
        "points_scored": ps.points_scored,
        "mvp": ps.get_mvp,
        "tags_made": ps.tags_made,
        "times_tagged": ps.times_tagged,
        "accuracy": ps.get_accuracy,
        "final_lives": ps.final_lives,
        "resupplies_given": ps.resupplies_given,
        "missiles_landed": ps.missiles_landed,
        "specials_used": ps.specials_used,
        "follow_up_shots": ps.follow_up_shots,
        "reaction_shots": ps.reaction_shots,
        "combo_resupply_count": ps.combo_resupply_count,
    }


def _cumulative_team_points(game_round: GameRound, team_id: int) -> list[list]:
    """RV-01: cumulative points-over-time series for one team in one round.

    Thin ORM-fetch adapter over ``round_comparison.cumulative_team_points``.
    Walks the round's events for actors on ``team_id`` ordered by timestamp,
    accumulating ``points_awarded`` (NULL coalesced to 0).
    """
    events = (
        game_round.events.filter(actor__team_id=team_id)
        .order_by("timestamp")
        .values_list("timestamp", "points_awarded")
    )
    return round_comparison.cumulative_team_points(events)


def build_round_comparison(
    round_a: GameRound, round_b: GameRound, user: "AbstractBaseUser | None"
) -> dict:
    """RV-01: the full comparison payload for two distinct rounds sharing a team.

    Raises ``ValueError`` when the rounds are the same or share no team; a
    shared Team the Account does not own raises ``Http404``.
    """
    if round_a.id == round_b.id:
        raise ValueError("Pick two different rounds to compare.")
    team_ids = shared_team_ids(round_a, round_b)
    if not team_ids:
        raise ValueError("These rounds share no team, so there is nothing to compare.")

    owned_teams = owned_queryset(Team.objects.all(), _scope_user(user))
    points_series = []
    for team_id in team_ids:
        team = owned_teams.filter(id=team_id).first()
        if team is None:
            raise Http404("No Team matches the given query.")
        points_series.append(
            {
                "team_id": team_id,
                "team_name": team.name,
                "a": _cumulative_team_points(round_a, team_id),
                "b": _cumulative_team_points(round_b, team_id),
            }
        )

    team_id_set = set(team_ids)
    rows_a = [
        _comparison_row(ps)
        for ps in round_a.player_states.select_related("player").all()
        if ps.player.team_id in team_id_set
    ]
    rows_b = [
        _comparison_row(ps)
        for ps in round_b.player_states.select_related("player").all()
        if ps.player.team_id in team_id_set
    ]

    return {
        "team_ids": team_ids,
        "points_series": points_series,
        "deltas": round_comparison.player_stat_deltas(rows_a, rows_b),
    }


# ---------------------------------------------------------------------------
# HX-03 team head-to-head
# ---------------------------------------------------------------------------


def _h2h_matches_qs(
    team_a: Team,
    team_b: Team,
    provenance: str,
    date_from: date | None,
    date_to: date | None,
) -> QuerySet[Match]:
    """HX-03: filter ``Match`` rows for the H2H corpus.

    Both id-pair orientations are matched (Side-agnostic).
    ``is_completed=True`` is required. Date range filters
    ``Match.date_played``. When ``provenance != "all"``, the Match record
    only counts Matches where **BOTH** ``game_rounds`` match the
    ``is_simulated`` filter (conservative; locked rule).
    """
    qs = (
        Match.objects.filter(
            Q(team_red=team_a, team_blue=team_b) | Q(team_red=team_b, team_blue=team_a),
            is_completed=True,
        )
        .select_related("team_red", "team_blue", "winner")
        .prefetch_related("game_rounds")
    )
    if date_from is not None:
        qs = qs.filter(date_played__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(date_played__date__lte=date_to)

    if provenance == "all":
        return qs

    target_flag = provenance == "sim"
    # Exclude Matches where ANY round disagrees with the provenance filter.
    # (BOTH rounds must match; one mismatch kicks the Match out.)
    qs = qs.exclude(game_rounds__is_simulated=(not target_flag))
    return qs


def _h2h_rounds_qs(
    team_a: Team,
    team_b: Team,
    provenance: str,
    date_from: date | None,
    date_to: date | None,
) -> QuerySet[GameRound]:
    """HX-03: filter ``GameRound`` rows for the unified basket.

    Both id-pair orientations are matched (Side-agnostic). Includes
    Rounds with ``match=None`` and Rounds whose Match is an H2H Match.
    Date range filters ``GameRound.date_played``; provenance filters
    ``is_simulated``.
    """
    qs = GameRound.objects.filter(
        Q(team_red=team_a, team_blue=team_b) | Q(team_red=team_b, team_blue=team_a),
    ).select_related("team_red", "team_blue", "winner", "match", "arena_map")
    if date_from is not None:
        qs = qs.filter(date_played__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(date_played__date__lte=date_to)
    if provenance == "sim":
        qs = qs.filter(is_simulated=True)
    elif provenance == "real":
        qs = qs.filter(is_simulated=False)
    return qs


def _team_a_or_b(game_round: GameRound, team_color: str, team_a_id: int) -> int:
    """HX-03: resolve ``"red"``/``"blue"`` + which Side team_a is on for this
    Round into the corresponding Team id (either ``team_a_id`` or the
    opposing team's id, whichever sat on that colour for the Round).
    """
    if team_color == "red":
        return game_round.team_red_id
    return game_round.team_blue_id


def _normalize_round(game_round: GameRound, team_a_id: int) -> dict:
    """HX-03: build the per-Round ``rounds_list`` dict from team_a's perspective.

    Flips ``red_points``/``blue_points`` and the per-team survivor counts
    when ``game_round.team_red_id != team_a_id`` so ``team_a_score`` /
    ``team_b_score`` / ``team_a_survivors`` / ``team_b_survivors`` are
    always from team_a's perspective.
    """
    # Pre-pulled player states (the caller does the bulk fetch); fall back
    # to a per-round query if the round wasn't tagged with a cache.
    states = getattr(game_round, "_h2h_states_cache", None)
    if states is None:
        states = list(game_round.player_states.all())
    red_survivors = sum(
        1 for s in states if s.team_color == "red" and s.final_lives > 0
    )
    blue_survivors = sum(
        1 for s in states if s.team_color == "blue" and s.final_lives > 0
    )

    if game_round.team_red_id == team_a_id:
        team_a_score = game_round.red_points
        team_b_score = game_round.blue_points
        team_a_survivors = red_survivors
        team_b_survivors = blue_survivors
    else:
        team_a_score = game_round.blue_points
        team_b_score = game_round.red_points
        team_a_survivors = blue_survivors
        team_b_survivors = red_survivors

    return {
        "round_id": game_round.id,
        "date_played": game_round.date_played,
        "team_a_score": team_a_score,
        "team_b_score": team_b_score,
        "team_a_survivors": team_a_survivors,
        "team_b_survivors": team_b_survivors,
        "match_id": game_round.match_id,
        "arena_map_id": game_round.arena_map_id,
        "arena_map_name": (
            game_round.arena_map.name if game_round.arena_map_id else None
        ),
        "is_simulated": game_round.is_simulated,
    }


def _build_player_rounds(rounds_qs, team_a_id: int, team_b_id: int) -> list[dict]:
    """HX-03: one ``player_rounds_list`` entry per ``PlayerRoundState`` row.

    Single ORM query that joins ``player`` and ``game_round``; resolves
    each row's team via ``team_color`` against the Round's red/blue
    Team ids, mapping onto ``team_a_id`` / ``team_b_id``. Computes
    ``mvp = state.get_mvp`` (property — **no parentheses**).
    """
    states = PlayerRoundState.objects.filter(game_round__in=rounds_qs).select_related(
        "player", "game_round"
    )
    out: list[dict] = []
    for state in states:
        gr = state.game_round
        attributed = _team_a_or_b(gr, state.team_color, team_a_id)
        # Defensive: only emit rows that landed on one of the two teams.
        if attributed not in (team_a_id, team_b_id):
            continue
        out.append(
            {
                "player_id": state.player_id,
                "player_name": state.player.name,
                "team_id": attributed,
                "mvp": state.get_mvp,
                "round_id": gr.id,
            }
        )
    return out


def _build_detail_list(
    matches_list: list[dict],
    rounds_list: list[dict],
    match_lookup: dict,
    team_a: Team,
    team_b: Team,
) -> list[dict]:
    """HX-03: unified reverse-chronological detail list.

    One row per Match (with 2-round totals + winner) AND one row per
    standalone Round (with that Round's score). Each row carries
    ``kind`` ∈ ``{"match", "round"}`` plus display fields used by the
    template.
    """
    rows: list[dict] = []
    rounds_by_match: dict[int, list[dict]] = defaultdict(list)
    standalone_rounds: list[dict] = []
    for r in rounds_list:
        if r["match_id"] is None:
            standalone_rounds.append(r)
        else:
            rounds_by_match[r["match_id"]].append(r)

    for m in matches_list:
        match_id = m["match_id"]
        match_rounds = rounds_by_match.get(match_id, [])
        team_a_total = sum(r["team_a_score"] for r in match_rounds)
        team_b_total = sum(r["team_b_score"] for r in match_rounds)
        winner_team_id = m["winner_team_id"]
        match_obj = match_lookup.get(match_id)
        winner_name = "—"
        if winner_team_id is not None and match_obj is not None:
            if winner_team_id == match_obj.team_red_id:
                winner_name = match_obj.team_red.name
            elif winner_team_id == match_obj.team_blue_id:
                winner_name = match_obj.team_blue.name
        rows.append(
            {
                "kind": "match",
                "date_played": m["date_played"],
                "label": "Match",
                "team_a_score": team_a_total,
                "team_b_score": team_b_total,
                "score_str": f"{team_a_total} - {team_b_total}",
                "winner_name": winner_name,
                "detail_url": reverse("match_detail", args=[match_id]),
                "is_simulated": m["is_simulated"],
            }
        )

    for r in standalone_rounds:
        winner_name = "—"
        a = r["team_a_score"]
        b = r["team_b_score"]
        if a > b:
            winner_name = team_a.name
        elif b > a:
            winner_name = team_b.name
        rows.append(
            {
                "kind": "round",
                "date_played": r["date_played"],
                "label": "Single Round",
                "team_a_score": a,
                "team_b_score": b,
                "score_str": f"{a} - {b}",
                "winner_name": winner_name,
                "detail_url": reverse("game_round_detail", args=[r["round_id"]]),
                "is_simulated": r["is_simulated"],
            }
        )

    rows.sort(key=lambda row: row["date_played"], reverse=True)
    return rows


def build_team_h2h(
    team_a: Team,
    team_b: Team,
    *,
    provenance: str,
    date_from: date | None,
    date_to: date | None,
) -> dict:
    """HX-03: the head-to-head results payload for two distinct Teams.

    Keys mirror the HTML results-mode context (minus ``mode``); no model
    instances appear in the output.
    """
    if team_a.id == team_b.id:
        raise ValueError("Pick two different teams to compare.")

    matches_qs = _h2h_matches_qs(team_a, team_b, provenance, date_from, date_to)
    rounds_qs = _h2h_rounds_qs(team_a, team_b, provenance, date_from, date_to)

    matches_list: list[dict] = []
    match_lookup: dict[int, Match] = {}
    for m in matches_qs:
        match_lookup[m.id] = m
        matches_list.append(
            {
                "match_id": m.id,
                "winner_team_id": m.winner_id,
                "date_played": m.date_played,
                "is_simulated": (
                    all(gr.is_simulated for gr in m.game_rounds.all())
                    if m.game_rounds.all()
                    else True
                ),
            }
        )

    # Materialise rounds so we can attach the prefetched player states cache.
    rounds = list(rounds_qs)
    # Bulk-fetch PlayerRoundStates for all rounds in one query, then bucket.
    states_by_round: dict[int, list[PlayerRoundState]] = {}
    if rounds:
        for state in PlayerRoundState.objects.filter(
            game_round__in=rounds
        ).select_related("player"):
            states_by_round.setdefault(state.game_round_id, []).append(state)
    for gr in rounds:
        gr._h2h_states_cache = states_by_round.get(gr.id, [])

    rounds_list: list[dict] = [_normalize_round(gr, team_a.id) for gr in rounds]
    player_rounds_list: list[dict] = _build_player_rounds(rounds, team_a.id, team_b.id)

    return {
        "match_record": h2h_stats.compute_match_record(
            matches_list, team_a.id, team_b.id
        ),
        "round_record": h2h_stats.compute_round_record(rounds_list),
        "score_margin": h2h_stats.compute_score_margin(rounds_list),
        "avg_survivors": h2h_stats.compute_avg_survivors(rounds_list),
        "top_impactful": h2h_stats.top_impactful_per_team(
            player_rounds_list, team_a.id, team_b.id
        ),
        "per_map_breakdown": h2h_stats.compute_per_map_breakdown(rounds_list),
        "detail_list": _build_detail_list(
            matches_list, rounds_list, match_lookup, team_a, team_b
        ),
        "margin_series": h2h_stats.margin_series(rounds_list),
        "cumulative_wl_series": h2h_stats.cumulative_wl_series(rounds_list),
    }


# ---------------------------------------------------------------------------
# HX-04 player head-to-head
# ---------------------------------------------------------------------------

_VALID_PLAYER_H2H_ROLES: frozenset[str] = frozenset(
    ("commander", "heavy", "scout", "medic", "ammo")
)


def _player_h2h_rounds_qs(
    player_a: Player,
    player_b: Player,
    provenance: str,
    date_from: date | None,
    date_to: date | None,
) -> QuerySet[GameRound]:
    """HX-04: filter ``GameRound`` rows where BOTH players appeared.

    Returns the queryset of ``GameRound`` rows where both ``player_a`` and
    ``player_b`` have a ``PlayerRoundState`` row, plus the date and
    provenance filters. NO opposite-teams gate here (that's
    ``_normalize_player_round``'s job); NO role gate (that's
    ``_filter_by_role_both``'s job).
    """
    qs = (
        GameRound.objects.filter(player_states__player=player_a)
        .filter(player_states__player=player_b)
        .distinct()
        .select_related("match", "arena_map")
    )
    if date_from is not None:
        qs = qs.filter(date_played__date__gte=date_from)
    if date_to is not None:
        qs = qs.filter(date_played__date__lte=date_to)
    if provenance == "sim":
        qs = qs.filter(is_simulated=True)
    elif provenance == "real":
        qs = qs.filter(is_simulated=False)
    return qs


def _build_player_h2h_tag_counts(
    rounds_qs: QuerySet[GameRound], player_a_id: int, player_b_id: int
) -> dict:
    """HX-04: single-query tag-count lookup keyed by ``round_id``.

    Returns ``{round_id: (a_to_b_count, b_to_a_count)}``. Iterates one
    flat ``GameEvent.values_list`` query (NOT two ``.annotate(Count())``
    calls) and groups in Python. Self-tags (defensive — actor == target)
    are ignored.
    """
    rows = GameEvent.objects.filter(
        game_round__in=rounds_qs,
        event_type="tag",
        actor_id__in=(player_a_id, player_b_id),
        target_id__in=(player_a_id, player_b_id),
    ).values_list("game_round_id", "actor_id", "target_id")

    counts: dict = {}
    for round_id, actor_id, target_id in rows:
        if actor_id == target_id:
            # Defensive: self-tags are ignored.
            continue
        pair = counts.get(round_id)
        if pair is None:
            pair = [0, 0]
            counts[round_id] = pair
        if actor_id == player_a_id and target_id == player_b_id:
            pair[0] += 1
        elif actor_id == player_b_id and target_id == player_a_id:
            pair[1] += 1
    return {rid: (pair[0], pair[1]) for rid, pair in counts.items()}


def _normalize_player_round(
    game_round: GameRound,
    prs_a: PlayerRoundState,
    prs_b: PlayerRoundState,
    tag_counts: dict,
) -> dict | None:
    """HX-04: build the 12-key per-Round dict from player_a's perspective.

    Returns ``None`` when the two players sat on the same team for this
    Round (opposite-teams gate — the caller drops ``None`` rows). Scores
    derive from the Round's ``red_points``/``blue_points`` and each
    player's per-Round ``team_color``. Tag counts come from the
    ``tag_counts`` lookup built by ``_build_player_h2h_tag_counts``.
    """
    if prs_a.team_color == prs_b.team_color:
        return None

    if prs_a.team_color == "red":
        player_a_team_score = game_round.red_points
    else:
        player_a_team_score = game_round.blue_points
    if prs_b.team_color == "red":
        player_b_team_score = game_round.red_points
    else:
        player_b_team_score = game_round.blue_points

    a_to_b, b_to_a = tag_counts.get(game_round.id, (0, 0))

    return {
        "round_id": game_round.id,
        "date_played": game_round.date_played,
        "player_a_team_score": player_a_team_score,
        "player_b_team_score": player_b_team_score,
        "tags_a_to_b": a_to_b,
        "tags_b_to_a": b_to_a,
        "role_a": prs_a.role,
        "role_b": prs_b.role,
        "match_id": game_round.match_id,
        "arena_map_id": game_round.arena_map_id,
        "arena_map_name": (
            game_round.arena_map.name if game_round.arena_map_id else None
        ),
        "is_simulated": game_round.is_simulated,
    }


def _filter_by_role_both(rounds_list: list[dict], role: str | None) -> list[dict]:
    """HX-04: keep rows where BOTH players played the given role.

    Passthrough (returns the list unchanged) when ``role`` is ``None``,
    empty, or not one of the five SM5 roles (forgiving-fallback per the
    HX-03 precedent). A valid role with zero matching rounds renders an
    empty results page — *not* passthrough — so the user sees that this
    specific matchup did not occur at that role.
    """
    if not role or role not in _VALID_PLAYER_H2H_ROLES:
        return rounds_list
    return [r for r in rounds_list if r["role_a"] == role and r["role_b"] == role]


def _build_player_h2h_detail_list(rounds_list: list[dict]) -> list[dict]:
    """HX-04: reverse-chronological detail list with display fields.

    Sorted by ``date_played`` desc, then ``round_id`` desc. Each row is
    ``{round_id, date_played, role_a, role_b, player_a_team_score,
    player_b_team_score, score_str, tags_a_to_b, tags_b_to_a,
    winner_label, detail_url, is_simulated}``.
    """
    ordered = sorted(
        rounds_list,
        key=lambda r: (r["date_played"], r["round_id"]),
        reverse=True,
    )
    out: list[dict] = []
    for r in ordered:
        a = r["player_a_team_score"]
        b = r["player_b_team_score"]
        if a > b:
            winner_label = "Player A"
        elif b > a:
            winner_label = "Player B"
        else:
            winner_label = "Tie"
        out.append(
            {
                "round_id": r["round_id"],
                "date_played": r["date_played"],
                "role_a": r["role_a"],
                "role_b": r["role_b"],
                "player_a_team_score": a,
                "player_b_team_score": b,
                "score_str": f"{a} - {b}",
                "tags_a_to_b": r["tags_a_to_b"],
                "tags_b_to_a": r["tags_b_to_a"],
                "winner_label": winner_label,
                "detail_url": reverse("game_round_detail", args=[r["round_id"]]),
                "is_simulated": r["is_simulated"],
            }
        )
    return out


def build_player_h2h(
    player_a: Player,
    player_b: Player,
    *,
    role: str | None,
    provenance: str,
    date_from: date | None,
    date_to: date | None,
) -> dict:
    """HX-04: the head-to-head results payload for two distinct Players.

    ``role`` keeps the HTML semantics (``None`` / unknown ⇒ passthrough).
    """
    if player_a.id == player_b.id:
        raise ValueError("Pick two different players to compare.")

    rounds_qs = _player_h2h_rounds_qs(
        player_a, player_b, provenance, date_from, date_to
    )
    rounds = list(rounds_qs)

    # Bulk-fetch PlayerRoundState rows for the two players across the basket,
    # then bucket by (round_id, player_id) so the per-Round normalizer can
    # look up each player's per-Round state cheaply.
    states_by_round: dict = {}
    if rounds:
        for state in PlayerRoundState.objects.filter(
            game_round__in=rounds,
            player_id__in=(player_a.id, player_b.id),
        ):
            states_by_round.setdefault(state.game_round_id, {})[state.player_id] = state

    tag_counts = (
        _build_player_h2h_tag_counts(rounds, player_a.id, player_b.id) if rounds else {}
    )

    rounds_list: list[dict] = []
    for gr in rounds:
        slot = states_by_round.get(gr.id) or {}
        prs_a = slot.get(player_a.id)
        prs_b = slot.get(player_b.id)
        if prs_a is None or prs_b is None:
            continue
        row = _normalize_player_round(gr, prs_a, prs_b, tag_counts)
        if row is None:
            # Same-team — drop.
            continue
        rounds_list.append(row)

    # Role filter (both-semantics; passthrough on None/unknown).
    rounds_list = _filter_by_role_both(rounds_list, role)

    return {
        "round_record": player_h2h_stats.compute_round_record(rounds_list),
        "score_margin": player_h2h_stats.compute_score_margin(rounds_list),
        "tag_stats": player_h2h_stats.compute_tag_stats(rounds_list),
        "per_role_breakdown": player_h2h_stats.compute_per_role_breakdown(rounds_list),
        "per_map_breakdown": player_h2h_stats.compute_per_map_breakdown(rounds_list),
        "detail_list": _build_player_h2h_detail_list(rounds_list),
        "margin_series": player_h2h_stats.margin_series(rounds_list),
        "cumulative_wl_series": player_h2h_stats.cumulative_wl_series(rounds_list),
    }


# ---------------------------------------------------------------------------
# RES-03 missile log / RES-04 heatmap
# ---------------------------------------------------------------------------


def build_missile_log(game_round: GameRound) -> dict:
    """RES-03: ``{"fired", "hit", "efficiency", "rows"}`` for one round.

    Lazily upgrades the round to ``combat`` fidelity first (GEN-01).
    """
    BatchSimulator().ensure_fidelity(game_round, "combat")

    events_qs = GameEvent.objects.filter(
        game_round=game_round,
        event_type="missiled",
    ).order_by("timestamp")
    events = [
        {
            "timestamp": e.timestamp,
            "metadata": e.metadata or {},
            "description": e.description,
            "points_awarded": e.points_awarded or 0,
        }
        for e in events_qs
    ]
    return missile_log_stats.summarize_missile_log(events)


def build_heatmap(game_round: GameRound) -> dict:
    """RES-04: the per-round heatmap payload (``arena_map`` is a model instance).

    Lazily upgrades the round to ``full`` fidelity first (GEN-01).
    """
    BatchSimulator().ensure_fidelity(game_round, "full")

    has_map = game_round.arena_map_id is not None
    arena_map = game_round.arena_map if has_map else None
    processed_image_url = (
        reverse("processed_image", args=[arena_map.pk]) if arena_map else None
    )

    # Roster: red first then blue, each ordered by (role, name).
    player_states = list(game_round.player_states.select_related("player").all())

    def _sort_key(state):
        team_rank = 0 if state.team_color == "red" else 1
        return (team_rank, state.role, state.player.name)

    player_roster = [
        {
            "id": state.player_id,
            "name": state.player.name,
            "role": state.role,
            "team_color": state.team_color,
        }
        for state in sorted(player_states, key=_sort_key)
    ]

    return {
        "cell_occupancy_json": game_round.cell_occupancy_json or {},
        "player_roster": player_roster,
        "has_map": has_map,
        "arena_map": arena_map,
        "zone_size": game_round.zone_size,
        "processed_image_url": processed_image_url,
    }


# ---------------------------------------------------------------------------
# RV-03 round report
# ---------------------------------------------------------------------------


def _player_row(state: PlayerRoundState) -> dict:
    """Build one **Round scoreboard** row dict (GLOSSARY.md, frozen 28-key shape
    pinned by ``round_summary.PLAYER_ROW_KEYS``).

    Both ``game_round_detail`` (HTML) and ``export_round_report`` (PDF) consume
    this dict, so the two surfaces cannot drift. The PDF deliberately renders
    a 14-key subset; the HTML renders 27 of 28 (``team_color`` is implicit
    from the table heading).
    """
    ts = state.was_eliminated_at
    # Match the pre-flip `is_eliminated` filter semantic exactly: `ts == 0` is
    # the legacy "never set" sentinel (not red), `ts >= 1801` is the TIME-01
    # SURVIVED_SENTINEL (not red), `ts is None` falls back to final_lives.
    if ts is None:
        is_elim = state.final_lives <= 0
        ts_display = ""
    elif ts == 0 or ts >= 1801:
        is_elim = False
        ts_display = ""
    else:
        is_elim = True
        ts_display = state.eliminated_timestamp()
    return {
        "name": state.player.name,
        "role": state.role,
        "team_color": state.team_color,
        "was_eliminated_at": ts,
        "eliminated_timestamp": ts_display,
        "is_eliminated": is_elim,
        "final_lives": state.final_lives,
        "points_scored": state.points_scored,
        "mvp": state.get_mvp,
        "tags_made": state.tags_made,
        "times_tagged": state.times_tagged,
        "accuracy": state.get_accuracy,
        "final_shots": state.final_shots,
        "final_special": state.final_special,
        "shots_used": state.shots_used,
        "missiles_used": state.missiles_used,
        "starting_missiles": state.starting_missiles,
        "missiles_landed": state.missiles_landed,
        "times_missiled": state.times_missiled,
        "final_medic_hits": state.final_medic_hits,
        "medic_lives_removed_from_nuke": state.medic_lives_removed_from_nuke,
        "follow_up_shots": state.follow_up_shots,
        "reaction_shots": state.reaction_shots,
        "resupplies_given": state.resupplies_given,
        "specials_used": state.specials_used,
        "combo_resupply_count": state.combo_resupply_count,
        "specific_tags_count": len(state.specific_tags or {}),
        "special_cost": state.special_cost,
    }


def _round_players_data(game_round: GameRound) -> list[dict]:
    """The 6-key SIM-05 player rows (the ``players_data`` half of
    ``round_playback_payload``), built without serializing any events."""
    return [
        {
            "id": ps.player_id,
            "name": ps.player.name,
            "team": ps.team_color,
            "role": ps.role,
            "sl": ps.starting_lives,
            "ss": ps.starting_shots,
        }
        for ps in game_round.player_states.select_related("player").all()
    ]


def round_playback_payload(
    game_round: GameRound, *, include_movement: bool = True
) -> tuple[list[dict], list[dict]]:
    """Build the SIM-05 ``(events_data, players_data)`` JSON payload from a
    persisted ``GameRound`` — the 13-key event shape + 6-key player shape the
    client playback engine in ``game_round_events.html`` / ``play_week_live.html``
    consumes. Extracted from ``game_round_events`` so the LG-01i live-watch view
    (which replays a just-committed round) reuses the exact same serialization;
    RV-05 moved it here so the round report can chart the same rows.

    ``include_movement=False`` drops ``event_type="movement"`` rows (the live
    watch surfaces only gameplay events on its timeline; the scoreboard
    reconstruction ignores movement either way).
    """
    # UXP-06: ``team_id`` lives on the Player row, so no Team join is needed.
    # UXP-06: GameEvent.Meta orders by timestamp only, so same-tick events came
    # back in arbitrary order on Postgres; ``id`` is the insertion order.
    events_qs = game_round.events.order_by("timestamp", "id").select_related(
        "actor", "target"
    )
    if not include_movement:
        events_qs = events_qs.exclude(event_type="movement")
    events_data = [
        {
            "type": e.event_type,
            "ts": e.timestamp,  # canonical ticks (1 tick = 0.5 s)
            "tf": e.formatted_timestamp,  # mm:ss display string
            "icon": e.get_event_icon(),
            "desc": e.description,
            "pts": e.points_awarded,
            "aid": e.actor_id,
            "an": e.actor.name,
            "at": e.actor.team_id,
            "tid": e.target_id if e.target_id else -1,
            "tn": e.target.name if e.target_id else "",
            "tt": e.target.team_id if e.target_id else "",
            "meta": e.metadata or {},
        }
        for e in events_qs
    ]
    return events_data, _round_players_data(game_round)


def build_round_report_data(game_round: GameRound) -> dict:
    """RV-03: the ``report_data`` dict ``build_round_report`` consumes (the 15
    RV-03 keys plus RV-05's ``chart_series``).

    Scoreboard ordering mirrors ``game_round_detail`` exactly. No fidelity
    upgrade here: the callers upgrade the round to ``full`` first (RV-05), so
    ``chart_series`` is built over the same player and playback rows the
    events page sends; a round with no events yields start points only.
    """
    red_states = (
        # LG-07: group by the physical Side (team_color), NOT player__team — a
        # member-night drawn-team game BORROWS Players (their Player.team is their
        # real team, not the drawn team), so a player__team filter matches 0. For
        # a regular game player.team == team_red ⟺ team_color == "red", so this is
        # behaviour-neutral there and fixes the empty drawn-team scoreboard.
        game_round.player_states.filter(team_color="red")
        .select_related("player")
        .order_by("-points_scored", "role", "player__name")
    )
    blue_states = (
        game_round.player_states.filter(team_color="blue")
        .select_related("player")
        .order_by("-points_scored", "role", "player__name")
    )

    red_players = [_player_row(s) for s in red_states]
    blue_players = [_player_row(s) for s in blue_states]

    round_label = (
        f"Round {game_round.round_number} of 2" if game_round.match else "Single Round"
    )
    map_name = game_round.arena_map.name if game_round.arena_map_id else None
    winner_name = game_round.winner.name if game_round.winner_id else None
    events_data, players_data = round_playback_payload(game_round)

    return {
        "round_id": game_round.pk,
        "round_label": round_label,
        "date_played": game_round.date_played.strftime("%b %d, %Y %H:%M"),
        "map_name": map_name,
        "red_team_name": game_round.team_red.name,
        "blue_team_name": game_round.team_blue.name,
        "red_points": game_round.red_points,
        "blue_points": game_round.blue_points,
        "red_eliminated": game_round.red_team_eliminated,
        "blue_eliminated": game_round.blue_team_eliminated,
        "winner_name": winner_name,
        "red_players": red_players,
        "blue_players": blue_players,
        "red_totals": round_summary.team_totals(red_players, game_round.red_points),
        "blue_totals": round_summary.team_totals(blue_players, game_round.blue_points),
        "chart_series": build_round_series(players_data, events_data),
    }


def round_report_filename(game_round: GameRound) -> str:
    """RV-03: the ``Content-Disposition`` filename for a round's PDF."""
    red_slug = slugify(game_round.team_red.name) or "red"
    blue_slug = slugify(game_round.team_blue.name) or "blue"
    return f"round-{game_round.pk}-{red_slug}-vs-{blue_slug}.pdf"
