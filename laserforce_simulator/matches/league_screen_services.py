"""ANG-01e -- the League screen builders shared by the HTML views and the API.

The ``league_screens/*`` HTML views and the ``/api/leagues/<id>/...`` screen
actions call the same functions here, so the two never drift apart. Nothing in
this module reads a request: callers pass the League, the resolved Season
scope and the screen's already-validated params.

Must not import ``matches.league_views`` or ``matches.league_screens`` (they
import this module). ``league_views`` re-exports the moved private names under
their old spellings; tests patch ``matches.league_screen_services.*``.
"""

from __future__ import annotations

from django.contrib.auth.base_user import AbstractBaseUser

from dataclasses import dataclass
from typing import Literal

from matches.league_services import pick_displayed_season
from matches.models import League, PlayerRoundState, Season, WatchedPlayer
from matches.season_player_stats import (
    PlayerStatRow,
    aggregate_player_stats,
    apply_rate,
    sort_player_stats,
    zero_fill_watched,
)
from teams.models import Player

CAREER: Literal["career"] = "career"

# ---------------------------------------------------------------------------
# Season scope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeasonScope:
    """The Season a League screen shows, resolved from a raw ``season`` value.

    - ``seasons``: this League's Seasons, newest first (the picker source).
    - ``selected``: the chosen ``Season``, ``"career"``, or ``None`` when the
      League has no Season (the empty state).
    - ``options``: the Seasons the client may pick (same order as ``seasons``).
    - ``round_filter``: the ORM lookup for a round / match queryset
      (``{"match__season": S}``, ``{"match__season__league": L}`` for Career),
      or ``None`` in the empty state. Prefix it with ``game_round__`` for a
      ``PlayerRoundState`` queryset.
    """

    seasons: list[Season]
    selected: Season | Literal["career"] | None
    options: list[Season]
    round_filter: dict | None

    @property
    def selected_id(self) -> int | str | None:
        """``selected`` as the legacy HTML value: a Season id, ``"career"`` or None."""
        if isinstance(self.selected, Season):
            return self.selected.id
        return self.selected


def resolve_season_scope(
    league: League,
    displayed_season: Season | None,
    raw_season: str | None,
    *,
    allow_career: bool = True,
) -> SeasonScope:
    """Resolve a raw ``season`` value (a Season id, ``"career"``, or blank).

    Blank / ``None`` picks ``displayed_season`` (``None`` when the League has
    no Season). Raises ``ValueError`` for a value that is not one of this
    League's Season ids (or ``"career"`` when ``allow_career``); the HTML
    wrapper falls back to the default, the API rejects it in its param form.
    """
    seasons = list(league.seasons.order_by("-start_date", "-id"))
    selected: Season | Literal["career"] | None
    if raw_season is None or raw_season == "":
        selected = displayed_season
    elif raw_season == CAREER:
        if not allow_career:
            raise ValueError("Career scope is not allowed here.")
        selected = CAREER
    else:
        try:
            season_id = int(raw_season)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid season: {raw_season!r}") from exc
        selected = next((s for s in seasons if s.id == season_id), None)
        if selected is None:
            raise ValueError(f"Season {season_id} is not in this League.")

    if selected == CAREER:
        round_filter: dict | None = {"match__season__league": league}
    elif selected is None:
        round_filter = None
    else:
        round_filter = {"match__season": selected}
    return SeasonScope(
        seasons=seasons,
        selected=selected,
        options=list(seasons),
        round_filter=round_filter,
    )


# ---------------------------------------------------------------------------
# Shared lookups
# ---------------------------------------------------------------------------


def build_round_dicts(prs_filter: dict) -> list[dict]:
    """Build one plain dict per ``PlayerRoundState`` matching ``prs_filter``.

    ``prs_filter`` is a ``PlayerRoundState`` lookup, usually a Season scope's
    ``round_filter`` prefixed with ``game_round__``. ``mvp`` / ``accuracy`` are
    read from the per-Round ``get_mvp`` / ``get_accuracy`` properties here so
    the pure ``season_player_stats`` module never touches the MVP formula or
    the ORM.

    Team identity per row resolves from the Round's ``team_red`` /
    ``team_blue`` keyed on the player's ``team_color``.
    """
    prs_qs = (
        PlayerRoundState.objects.filter(**prs_filter)
        .select_related(
            "player",
            "game_round",
            "game_round__match",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )

    rounds: list[dict] = []
    for prs in prs_qs:
        game_round = prs.game_round
        if prs.team_color == "red":
            team = game_round.team_red
        elif prs.team_color == "blue":
            team = game_round.team_blue
        else:
            team = None
        rounds.append(
            {
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "team_id": team.id if team is not None else 0,
                "team_name": team.name if team is not None else "",
                "role": prs.role,
                # 12 STAT_KEYS -- mvp / accuracy pre-computed per round.
                "points_scored": prs.points_scored,
                "mvp": float(prs.get_mvp),
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "accuracy": float(prs.get_accuracy),
                # Derived survival seconds: elimination tick capped at the
                # round length (1800), /2 to seconds at the display boundary
                # (TIME-01). The pure module averages this across rounds.
                "survival_seconds": min(prs.was_eliminated_at, 1800) / 2,
                "final_lives": prs.final_lives,
                "resupplies_given": prs.resupplies_given,
                "missiles_landed": prs.missiles_landed,
                "specials_used": prs.specials_used,
                "follow_up_shots": prs.follow_up_shots,
                "reaction_shots": prs.reaction_shots,
                "combo_resupply_count": prs.combo_resupply_count,
            }
        )
    return rounds


def enrolled_team_ids(season: Season) -> list[int]:
    """Team ids enrolled in ``season`` -- snapshot first, live M2M fallback.

    Active / completed Seasons read the frozen ``starting_team_ids_json``
    snapshot; draft Seasons (snapshot still ``None``) read the live M2M.
    """
    if season.starting_team_ids_json is not None:
        return list(season.starting_team_ids_json)
    return sorted(t.id for t in season.teams.all())


# ---------------------------------------------------------------------------
# Watch list (per Account, per League)
# ---------------------------------------------------------------------------


class PlayerNotInLeague(Exception):
    """The Player is on no Team of the displayed Season and not a free agent."""


def watched_player_ids(
    account: AbstractBaseUser | None, league: League | int
) -> set[int]:
    """The Player ids on ``account``'s watch list for ``league``.

    ``league`` may be a League or its id (the context processor only has the
    URL kwarg). An anonymous / missing Account has an empty list.
    """
    if account is None or not getattr(account, "is_authenticated", False):
        return set()
    league_id = league.pk if isinstance(league, League) else league
    return set(
        WatchedPlayer.objects.filter(account=account, league_id=league_id).values_list(
            "player_id", flat=True
        )
    )


def _player_in_league(league: League, player: Player) -> bool:
    """True when ``player`` is on a displayed-Season Team or in the FA pool."""
    if player.team_id is None:
        return False
    if league.free_agent_pool_id is not None and (
        player.team_id == league.free_agent_pool_id
    ):
        return True
    displayed_season = pick_displayed_season(league)
    if displayed_season is None:
        return False
    return player.team_id in enrolled_team_ids(displayed_season)


def add_watch(account: AbstractBaseUser, league: League, player: Player) -> None:
    """Put ``player`` on ``account``'s watch list for ``league``; idempotent.

    Raises :class:`PlayerNotInLeague` when the Player is neither on one of the
    displayed Season's enrolled Teams nor in ``league.free_agent_pool``.
    """
    if not _player_in_league(league, player):
        raise PlayerNotInLeague(f"Player {player.pk} is not in this League.")
    WatchedPlayer.objects.get_or_create(account=account, league=league, player=player)


def remove_watch(account: AbstractBaseUser, league: League, player: Player) -> None:
    """Take ``player`` off ``account``'s watch list for ``league``; idempotent."""
    WatchedPlayer.objects.filter(account=account, league=league, player=player).delete()


def clear_watch_list(account: AbstractBaseUser, league: League) -> int:
    """Empty ``account``'s watch list for ``league``; returns how many were removed."""
    removed, _ = WatchedPlayer.objects.filter(account=account, league=league).delete()
    return removed


# ---------------------------------------------------------------------------
# Watch List screen
# ---------------------------------------------------------------------------


def build_watch_list_rows(
    scope: SeasonScope,
    watched_ids: set[int],
    *,
    rate: str,
    sort: str,
    direction: str,
) -> list[PlayerStatRow]:
    """The Watch List screen rows: the Player-Stats pipeline over ``scope``,
    limited to ``watched_ids`` and zero-filled for watched Players with no
    Rounds in scope (so the set is stable across Seasons).

    Empty in the empty state (no Season, ``scope.round_filter is None``).
    """
    if scope.round_filter is None:
        return []
    prs_filter = {f"game_round__{k}": v for k, v in scope.round_filter.items()}
    rows = aggregate_player_stats(build_round_dicts(prs_filter))
    # ``role`` is "" for zero-filled rows: a Player has no single role field.
    identity_by_id = {
        p.id: {
            "player_name": p.name,
            "team_id": p.team_id if p.team_id is not None else 0,
            "team_name": p.team.name if p.team is not None else "",
            "role": "",
        }
        for p in Player.objects.filter(pk__in=watched_ids).select_related("team")
    }
    rows = zero_fill_watched(rows, watched_ids, identity_by_id)
    rows = apply_rate(rows, rate)
    return sort_player_stats(rows, sort, direction)


# --- finances (T5) ---
# Imports kept local to this block so sibling tickets' edits to the module
# header don't conflict.
from django import forms as _forms  # noqa: E402

from matches import finance  # noqa: E402
from matches.league_services import _team_wins_games_for_season  # noqa: E402
from matches.models import TeamSeasonFinance  # noqa: E402
from teams.models import Team  # noqa: E402


class FinanceDisabled(Exception):
    """The League's finance toggle is off; finance edits are refused."""


class TeamNotEditable(Exception):
    """Only ``league.current_team`` may have its finances edited."""


@dataclass(frozen=True)
class TeamFinances:
    """Everything the Team Finances screen shows for one enrolled Team."""

    team: Team
    budget_costs: dict[str, float]
    live_payroll: float
    live_luxury_tax: float
    live_min_payroll_penalty: float
    wins: int
    losses: int
    games: int
    current_hype: float
    latest_finance: TeamSeasonFinance | None
    roster_salaries: list[dict]
    availability: list[dict]
    history: list[TeamSeasonFinance]


def build_team_finances(season: Season, team: Team) -> TeamFinances:
    """The Team Finances screen data for ``team`` in the displayed ``season``.

    ``history`` is newest Season first; ``roster_salaries`` / ``availability``
    rows are ``{"player": Player, "salary" | "games_remaining": ...}``.
    """
    history = list(
        TeamSeasonFinance.objects.filter(team=team)
        .select_related("season")
        .order_by("-season_id")
    )
    roster_salaries = [
        {"player": p, "salary": finance.salary_for_overall(p.overall_rating)}
        for p in team.active_players
    ]
    live_payroll = sum(r["salary"] for r in roster_salaries)
    availability = [
        {"player": p, "games_remaining": p.games_unavailable}
        for p in sorted(
            team.players.filter(games_unavailable__gt=0),
            key=lambda p: p.games_unavailable,
            reverse=True,
        )
    ]
    wins, games = _team_wins_games_for_season(season, team.id)
    latest_finance = history[0] if history else None
    return TeamFinances(
        team=team,
        budget_costs={
            "scouting": finance.level_to_amount(team.budget_scouting),
            "coaching": finance.level_to_amount(team.budget_coaching),
            "facilities": finance.level_to_amount(team.budget_facilities),
            "health": finance.level_to_amount(team.budget_health),
        },
        live_payroll=live_payroll,
        live_luxury_tax=finance.luxury_tax(live_payroll),
        live_min_payroll_penalty=finance.min_payroll_penalty(live_payroll),
        wins=wins,
        losses=games - wins,
        games=games,
        current_hype=latest_finance.hype if latest_finance else 0.0,
        latest_finance=latest_finance,
        roster_salaries=roster_salaries,
        availability=availability,
        history=history,
    )


def build_league_finances(season: Season) -> list[dict]:
    """One ``{"team", "finance"}`` row per enrolled Team (by name); ``finance``
    is the Team's ``TeamSeasonFinance`` for ``season`` or ``None``."""
    finance_by_team = {
        f.team_id: f for f in TeamSeasonFinance.objects.filter(season=season)
    }
    return [
        {"team": team, "finance": finance_by_team.get(team.id)}
        for team in season.teams.order_by("name")
    ]


def update_team_finances(league: League, team: Team, form: _forms.ModelForm) -> Team:
    """Save ``form`` (a bound ``TeamFinancesForm``) onto ``team``.

    Raises ``FinanceDisabled`` when the League's finances are off and
    ``TeamNotEditable`` unless ``team`` is ``league.current_team`` -- both
    before the form is looked at. An invalid form saves nothing: the caller
    reads ``form.errors``. Saves through ``update_fields``.
    """
    if not league.finance_enabled:
        raise FinanceDisabled
    if league.current_team_id is None or team.id != league.current_team_id:
        raise TeamNotEditable
    if not form.is_valid():
        return team
    for field, value in form.cleaned_data.items():
        setattr(team, field, value)
    team.save(update_fields=list(form.cleaned_data))
    return team
