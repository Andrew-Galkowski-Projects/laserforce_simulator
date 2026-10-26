"""ANG-06-9 -- Team match history, shared by the HTML view and the API.

``build_team_match_history`` gathers one Team's Matches and standalone Rounds,
scopes them (free-form / one per Tournament), and builds the record tiles and
the role-breakdown roster. Both surfaces see only the Account's own games:
Matches through ``owned_match_q`` and Rounds through ``owned_game_round_q``.
"""

from __future__ import annotations

from collections.abc import Callable

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Q

from accounts.permissions import owned_game_round_q, owned_match_q
from matches.api_refs import team_ref
from matches.models import (
    GameRound,
    Match,
    PlayerRoundState,
    SeriesMatch,
    TournamentPlayerEntry,
)
from teams.models import Player, Team

FREEFORM_SCOPE = "freeform"

ROLE_ORDER: list[tuple[str, str]] = [
    ("commander", "Commander"),
    ("heavy", "Heavy Weapons"),
    ("scout", "Scout"),
    ("ammo", "Ammo Carrier"),
    ("medic", "Medic"),
]


def _empty_roles() -> dict[str, dict]:
    return {key: {"games": 0, "mvp_sum": 0.0, "score_sum": 0} for key, _ in ROLE_ORDER}


def _member_players(team: Team) -> tuple[list[Player], set[int]]:
    """The Team's official roster: drives the Merc flag and the zero-fill.

    A draw team owns its roster via ``TournamentPlayerEntry`` (its borrowed
    Players keep ``Player.team`` = Free Agents); a regular Team via
    ``Player.team``.
    """
    if team.is_draw_team:
        member_ids = set(
            TournamentPlayerEntry.objects.filter(drawn_team=team).values_list(
                "player_id", flat=True
            )
        )
        return list(Player.objects.filter(id__in=member_ids)), member_ids
    members = list(team.players.all())
    return members, {p.id for p in members}


def _roster_rows(
    team: Team, user: AbstractBaseUser | None, in_scope: Callable[[int | None], bool]
) -> list[dict]:
    """Role-breakdown roster over the in-scope Rounds.

    Derived from ``PlayerRoundState`` rather than ``team.players`` so a draw
    team still shows a full roster. A state belongs to the Team in a Round iff
    its ``team_color`` is the colour the Team wore that Round.
    """
    color_by_round: dict[int, str] = {}
    for gr in (
        GameRound.objects.filter(Q(team_red=team) | Q(team_blue=team))
        .filter(owned_game_round_q(user))
        .only("id", "team_red_id", "team_blue_id", "match_id")
    ):
        if not in_scope(gr.match_id):
            continue
        color_by_round[gr.id] = "red" if gr.team_red_id == team.id else "blue"

    roster: dict[int, dict] = {}
    states = PlayerRoundState.objects.filter(
        game_round_id__in=color_by_round.keys()
    ).select_related("player")
    for st in states:
        if st.team_color != color_by_round.get(st.game_round_id):
            continue
        entry = roster.get(st.player_id)
        if entry is None:
            entry = {"player": st.player, "games_played": 0, "roles": _empty_roles()}
            roster[st.player_id] = entry
        entry["games_played"] += 1
        bucket = entry["roles"].get(str(st.role).lower())
        if bucket is not None:
            bucket["games"] += 1
            bucket["mvp_sum"] += st.get_mvp
            bucket["score_sum"] += st.points_scored

    member_players, member_ids = _member_players(team)
    for player in member_players:
        if player.id not in roster:
            roster[player.id] = {
                "player": player,
                "games_played": 0,
                "roles": _empty_roles(),
            }

    rows = []
    for player_id, entry in roster.items():
        cells = []
        for key, _label in ROLE_ORDER:
            bucket = entry["roles"][key]
            games = bucket["games"]
            cells.append(
                {
                    "role": key,
                    "games": games,
                    "avg_mvp": (bucket["mvp_sum"] / games) if games else None,
                    "avg_score": (bucket["score_sum"] / games) if games else None,
                }
            )
        rows.append(
            {
                "player": entry["player"],
                "games_played": entry["games_played"],
                "is_merc": player_id not in member_ids,
                "roles": cells,
            }
        )
    # Members first, then mercs; within each, most-played first, then by name.
    rows.sort(
        key=lambda row: (row["is_merc"], -row["games_played"], row["player"].name)
    )
    return rows


def _record(games: list, team: Team) -> tuple[int, int, int]:
    """(wins, losses, ties): a null winner is a tie (an unfinished Match too)."""
    wins = sum(1 for g in games if g.winner_id == team.id)
    losses = sum(1 for g in games if g.winner_id is not None and g.winner_id != team.id)
    ties = sum(1 for g in games if g.winner_id is None)
    return wins, losses, ties


def build_team_match_history(
    team: Team, user: AbstractBaseUser | None, scopes: set[str] | None
) -> dict:
    """One Team's match history, scoped to ``user``'s own games.

    ``scopes`` is the selected scope keys (``"freeform"`` / ``"t<id>"``);
    ``None`` selects every scope, and unknown keys are ignored. Returns model
    instances in ``team``, ``matches``, ``detailed_rounds``,
    ``unique_opponents`` and each roster row's ``player``.
    """
    all_matches = list(
        Match.objects.filter(Q(team_red=team) | Q(team_blue=team))
        .filter(owned_match_q(user))
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )
    standalone_rounds = list(
        GameRound.objects.filter(
            Q(team_red=team) | Q(team_blue=team), match__isnull=True
        )
        .filter(owned_game_round_q(user))
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )

    # Each Match belongs to a Tournament (SeriesMatch -> BracketNode ->
    # Tournament) or, lacking a SeriesMatch, to the free-form bucket.
    match_tournament = {}
    tournaments_by_id = {}
    for sm in SeriesMatch.objects.filter(
        match_id__in=[m.id for m in all_matches],
        node__tournament__isnull=False,
    ).select_related("node__tournament"):
        tourney = sm.node.tournament
        match_tournament[sm.match_id] = tourney
        tournaments_by_id[tourney.id] = tourney

    def _match_scope(match_id: int | None) -> str:
        tourney = match_tournament.get(match_id)
        return f"t{tourney.id}" if tourney is not None else FREEFORM_SCOPE

    has_freeform = bool(standalone_rounds) or any(
        m.id not in match_tournament for m in all_matches
    )
    scope_options = []
    if has_freeform:
        scope_options.append({"key": FREEFORM_SCOPE, "label": "Free-form matches"})
    for tourney in sorted(tournaments_by_id.values(), key=lambda t: t.name.lower()):
        scope_options.append({"key": f"t{tourney.id}", "label": tourney.name})
    valid_scope_keys = {opt["key"] for opt in scope_options}

    selected = set(valid_scope_keys) if scopes is None else scopes & valid_scope_keys
    for opt in scope_options:
        opt["checked"] = opt["key"] in selected

    matches = [m for m in all_matches if _match_scope(m.id) in selected]
    detailed_rounds = standalone_rounds if FREEFORM_SCOPE in selected else []

    def _round_in_scope(match_id: int | None) -> bool:
        scope = FREEFORM_SCOPE if match_id is None else _match_scope(match_id)
        return scope in selected

    match_wins, match_losses, match_ties = _record(matches, team)
    round_wins, round_losses, round_ties = _record(detailed_rounds, team)

    opponents: dict[int, Team] = {}
    for game in [*matches, *detailed_rounds]:
        opp = _opponent(game, team)
        opponents.setdefault(opp.id, opp)

    return {
        "team": team,
        "matches": matches,
        "detailed_rounds": detailed_rounds,
        "unique_opponents": sorted(opponents.values(), key=lambda t: t.name),
        "scope_options": scope_options,
        "show_scope_filter": len(scope_options) >= 2,
        "overall_checked": bool(valid_scope_keys) and selected == valid_scope_keys,
        "has_any_games": bool(all_matches or standalone_rounds),
        "roster_rows": _roster_rows(team, user, _round_in_scope),
        "role_columns": [label for _, label in ROLE_ORDER],
        "stats": {
            "total_matches": len(matches),
            "match_wins": match_wins,
            "match_losses": match_losses,
            "match_ties": match_ties,
            "total_rounds": len(detailed_rounds),
            "round_wins": round_wins,
            "round_losses": round_losses,
            "round_ties": round_ties,
        },
    }


def _opponent(game: Match | GameRound, team: Team) -> Team:
    return game.team_blue if game.team_red_id == team.id else game.team_red


def _match_result(match: Match, team: Team) -> str:
    if match.winner_id is None:
        return "tie"
    return "won" if match.winner_id == team.id else "lost"


def team_match_history_payload(history: dict) -> dict:
    """``build_team_match_history`` output as the API's ``data`` (no ORM rows)."""
    team: Team = history["team"]
    matches = []
    for m in history["matches"]:
        is_red = m.team_red_id == team.id
        matches.append(
            {
                "id": m.id,
                "opponent": team_ref(_opponent(m, team)),
                "result": _match_result(m, team),
                "team_points": m.red_total_points if is_red else m.blue_total_points,
                "opponent_points": (
                    m.blue_total_points if is_red else m.red_total_points
                ),
                "team_rounds_won": m.red_rounds_won if is_red else m.blue_rounds_won,
                "opponent_rounds_won": (
                    m.blue_rounds_won if is_red else m.red_rounds_won
                ),
                "date_played": m.date_played,
                "is_completed": m.is_completed,
            }
        )
    single_rounds = []
    for r in history["detailed_rounds"]:
        is_red = r.team_red_id == team.id
        single_rounds.append(
            {
                "id": r.id,
                "opponent": team_ref(_opponent(r, team)),
                "team_points": r.red_points if is_red else r.blue_points,
                "opponent_points": r.blue_points if is_red else r.red_points,
                "date_played": r.date_played,
            }
        )
    return {
        "team": team_ref(team),
        "scope_options": [
            {"key": o["key"], "label": o["label"], "selected": o["checked"]}
            for o in history["scope_options"]
        ],
        "show_scope_filter": history["show_scope_filter"],
        "has_any_games": history["has_any_games"],
        "stats": history["stats"],
        "role_columns": [{"role": key, "label": label} for key, label in ROLE_ORDER],
        "roster": [
            {
                "player": {"id": row["player"].id, "name": row["player"].name},
                "games_played": row["games_played"],
                "is_merc": row["is_merc"],
                "roles": row["roles"],
            }
            for row in history["roster_rows"]
        ],
        "opponents": [team_ref(t) for t in history["unique_opponents"]],
        "matches": matches,
        "single_rounds": single_rounds,
    }
