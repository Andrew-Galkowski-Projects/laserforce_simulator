"""ANG-06-9 -- Team match history, shared by the HTML view and the API.

``build_team_match_history`` gathers one Team's Matches and standalone Rounds,
scopes them (free-form / one per Tournament), and builds the record tiles and
the role-breakdown roster. Both surfaces see only the Account's own games:
Matches through ``owned_match_q`` and Rounds through ``owned_game_round_q``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass

from django.contrib.auth.base_user import AbstractBaseUser
from django.db.models import Q

from accounts.permissions import owned_game_round_q, owned_match_q
from matches.api_refs import team_ref
from matches.models import (
    GameRound,
    Match,
    PlayerRoundState,
    SeriesMatch,
    Tournament,
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


@dataclass(frozen=True)
class HistoryStats:
    """The record tiles over the in-scope games (a null winner is a tie)."""

    total_matches: int
    match_wins: int
    match_losses: int
    match_ties: int
    total_rounds: int
    round_wins: int
    round_losses: int
    round_ties: int


@dataclass(frozen=True)
class TeamMatchHistory:
    """One Team's match history, scoped to the Account's own games.

    ``scope_options``, ``roster_rows`` and each row's ``roles`` stay dicts:
    the HTML view's tests read them by key from the template context.
    """

    team: Team
    matches: list[Match]
    detailed_rounds: list[GameRound]
    unique_opponents: list[Team]
    scope_options: list[dict]
    show_scope_filter: bool
    overall_checked: bool
    has_any_games: bool
    roster_rows: list[dict]
    role_columns: list[str]
    stats: HistoryStats


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


def _color_by_round(
    team: Team, user: AbstractBaseUser | None, in_scope: Callable[[int | None], bool]
) -> dict[int, str]:
    """The colour ``team`` wore in each of its in-scope Rounds, by Round id."""
    color_by_round: dict[int, str] = {}
    for gr in (
        GameRound.objects.filter(Q(team_red=team) | Q(team_blue=team))
        .filter(owned_game_round_q(user))
        .only("id", "team_red_id", "team_blue_id", "match_id")
    ):
        if in_scope(gr.match_id):
            color_by_round[gr.id] = "red" if gr.team_red_id == team.id else "blue"
    return color_by_round


def _tally_roster(color_by_round: dict[int, str]) -> dict[int, dict]:
    """Per-Player games and role buckets from the Team's side of each Round."""
    roster: dict[int, dict] = {}
    # ``get_mvp`` reads each state's Round: fetch it in the same query.
    states = PlayerRoundState.objects.filter(
        game_round_id__in=color_by_round.keys()
    ).select_related("player", "game_round")
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
    return roster


def _role_cells(roles: dict[str, dict]) -> list[dict]:
    """One cell per role in ``ROLE_ORDER``; averages are None for no games."""
    cells = []
    for key, _label in ROLE_ORDER:
        bucket = roles[key]
        games = bucket["games"]
        cells.append(
            {
                "role": key,
                "games": games,
                "avg_mvp": (bucket["mvp_sum"] / games) if games else None,
                "avg_score": (bucket["score_sum"] / games) if games else None,
            }
        )
    return cells


def _roster_rows(
    team: Team, user: AbstractBaseUser | None, in_scope: Callable[[int | None], bool]
) -> list[dict]:
    """Role-breakdown roster over the in-scope Rounds.

    Derived from ``PlayerRoundState`` rather than ``team.players`` so a draw
    team still shows a full roster. A state belongs to the Team in a Round iff
    its ``team_color`` is the colour the Team wore that Round.
    """
    roster = _tally_roster(_color_by_round(team, user, in_scope))

    member_players, member_ids = _member_players(team)
    for player in member_players:
        if player.id not in roster:
            roster[player.id] = {
                "player": player,
                "games_played": 0,
                "roles": _empty_roles(),
            }

    rows = [
        {
            "player": entry["player"],
            "games_played": entry["games_played"],
            "is_merc": player_id not in member_ids,
            "roles": _role_cells(entry["roles"]),
        }
        for player_id, entry in roster.items()
    ]
    # Members first, then mercs; within each, most-played first, then by name.
    rows.sort(
        key=lambda row: (row["is_merc"], -row["games_played"], row["player"].name)
    )
    return rows


def _record(games: Sequence[Match | GameRound], team: Team) -> tuple[int, int, int]:
    """(wins, losses, ties): a null winner is a tie (an unfinished Match too)."""
    wins = sum(1 for g in games if g.winner_id == team.id)
    losses = sum(1 for g in games if g.winner_id is not None and g.winner_id != team.id)
    ties = sum(1 for g in games if g.winner_id is None)
    return wins, losses, ties


def _fetch_games(
    team: Team, user: AbstractBaseUser | None
) -> tuple[list[Match], list[GameRound]]:
    """The Account's own Matches and standalone Rounds for ``team``, newest first."""
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
    return all_matches, standalone_rounds


def _tournament_by_match(matches: list[Match]) -> dict[int, Tournament]:
    """Each tournament Match's Tournament (SeriesMatch -> BracketNode -> Tournament).

    A Match lacking a SeriesMatch belongs to the free-form bucket and is absent.
    """
    return {
        sm.match_id: sm.node.tournament
        for sm in SeriesMatch.objects.filter(
            match_id__in=[m.id for m in matches],
            node__tournament__isnull=False,
        ).select_related("node__tournament")
    }


def _scope_options(has_freeform: bool, tournaments: list[Tournament]) -> list[dict]:
    """Free-form first (when it has games), then each Tournament by name."""
    options = []
    if has_freeform:
        options.append({"key": FREEFORM_SCOPE, "label": "Free-form matches"})
    unique = {t.id: t for t in tournaments}
    for tourney in sorted(unique.values(), key=lambda t: t.name.lower()):
        options.append({"key": f"t{tourney.id}", "label": tourney.name})
    return options


def _opponents(games: Sequence[Match | GameRound], team: Team) -> list[Team]:
    """Each distinct opponent of ``team`` in ``games``, by name."""
    opponents: dict[int, Team] = {}
    for game in games:
        opp = _opponent(game, team)
        opponents.setdefault(opp.id, opp)
    return sorted(opponents.values(), key=lambda t: t.name)


def build_team_match_history(
    team: Team, user: AbstractBaseUser | None, scopes: set[str] | None
) -> TeamMatchHistory:
    """One Team's match history, scoped to ``user``'s own games.

    ``scopes`` is the selected scope keys (``"freeform"`` / ``"t<id>"``);
    ``None`` selects every scope, and unknown keys are ignored. Returns model
    instances in ``team``, ``matches``, ``detailed_rounds``,
    ``unique_opponents`` and each roster row's ``player``.
    """
    all_matches, standalone_rounds = _fetch_games(team, user)
    match_tournament = _tournament_by_match(all_matches)

    def _match_scope(match_id: int | None) -> str:
        tourney = match_tournament.get(match_id)
        return f"t{tourney.id}" if tourney is not None else FREEFORM_SCOPE

    has_freeform = bool(standalone_rounds) or any(
        m.id not in match_tournament for m in all_matches
    )
    scope_options = _scope_options(has_freeform, list(match_tournament.values()))
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

    return TeamMatchHistory(
        team=team,
        matches=matches,
        detailed_rounds=detailed_rounds,
        unique_opponents=_opponents([*matches, *detailed_rounds], team),
        scope_options=scope_options,
        show_scope_filter=len(scope_options) >= 2,
        overall_checked=bool(valid_scope_keys) and selected == valid_scope_keys,
        has_any_games=bool(all_matches or standalone_rounds),
        roster_rows=_roster_rows(team, user, _round_in_scope),
        role_columns=[label for _, label in ROLE_ORDER],
        stats=HistoryStats(
            total_matches=len(matches),
            match_wins=match_wins,
            match_losses=match_losses,
            match_ties=match_ties,
            total_rounds=len(detailed_rounds),
            round_wins=round_wins,
            round_losses=round_losses,
            round_ties=round_ties,
        ),
    )


def _opponent(game: Match | GameRound, team: Team) -> Team:
    return game.team_blue if game.team_red_id == team.id else game.team_red


def _result(game: Match | GameRound, team: Team) -> str:
    if game.winner_id is None:
        return "tie"
    return "won" if game.winner_id == team.id else "lost"


def team_match_history_payload(history: TeamMatchHistory) -> dict:
    """``build_team_match_history`` output as the API's ``data`` (no ORM rows)."""
    team = history.team
    matches = []
    for m in history.matches:
        is_red = m.team_red_id == team.id
        matches.append(
            {
                "id": m.id,
                "opponent": team_ref(_opponent(m, team)),
                "result": _result(m, team),
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
    for r in history.detailed_rounds:
        is_red = r.team_red_id == team.id
        single_rounds.append(
            {
                "id": r.id,
                "opponent": team_ref(_opponent(r, team)),
                "result": _result(r, team),
                "team_points": r.red_points if is_red else r.blue_points,
                "opponent_points": r.blue_points if is_red else r.red_points,
                "date_played": r.date_played,
            }
        )
    return {
        "team": team_ref(team),
        "scope_options": [
            {"key": o["key"], "label": o["label"], "selected": o["checked"]}
            for o in history.scope_options
        ],
        "show_scope_filter": history.show_scope_filter,
        "has_any_games": history.has_any_games,
        "stats": asdict(history.stats),
        "role_columns": [{"role": key, "label": label} for key, label in ROLE_ORDER],
        "roster": [
            {
                "player": {"id": row["player"].id, "name": row["player"].name},
                "games_played": row["games_played"],
                "is_merc": row["is_merc"],
                "roles": row["roles"],
            }
            for row in history.roster_rows
        ],
        "opponents": [team_ref(t) for t in history.unique_opponents],
        "matches": matches,
        "single_rounds": single_rounds,
    }
