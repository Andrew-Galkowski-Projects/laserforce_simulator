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

# --- league player page (T3) imports ---
from matches import development
from matches.league_services import _player_award_labels, compute_season_award_set
from matches.models import PlayerSeasonRating
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


# --- league player page (T3) ---


@dataclass(frozen=True)
class PlayerDetail:
    """The League player page sections (LG-06h / LG-03 / LG-04).

    The row dicts keep the HTML template's shapes:

    - ``rs_rows``: per-Season ``{year, season_id, team_name, team_id, games,
      stats}``, newest first; only Seasons the Player has Rounds in.
    - ``career_row``: the same shape (``year="Career"``, ``season_id=None``),
      or ``None`` when the Player has no Rounds in this League.
    - ``player_awards``: ``{season_id, season_name, award_labels}``, newest
      first; only Seasons with >= 1 award.
    - ``ratings_history``: ``{season_id, season_name, age, overall_rating,
      potential, stats}``, oldest first.
    """

    rs_rows: list[dict]
    career_row: dict | None
    player_awards: list[dict]
    ratings_history: list[dict]


def _row_from_stat_row(
    stat_row: PlayerStatRow, year: str, season_id: int | None
) -> dict:
    """Build a per-Season / Career row dict from an aggregated PlayerStatRow."""
    return {
        "year": year,
        "season_id": season_id,
        "team_name": stat_row.team_name,
        "team_id": stat_row.team_id,
        "games": stat_row.games,
        "stats": stat_row.stats,
    }


def build_player_detail(league: League, player: Player) -> PlayerDetail:
    """The League player page for ``player`` in ``league``.

    Lenient: a Player with no Rounds in this League gets empty ``rs_rows`` /
    ``player_awards`` and ``career_row=None`` -- never an error.
    """
    seasons = list(league.seasons.order_by("-id"))

    # Per-Season rows -- one aggregation pass per Season the Player has
    # Rounds in (newest first).
    rs_rows: list[dict] = []
    for season in seasons:
        round_dicts = build_round_dicts(
            {"game_round__match__season": season, "player_id": player.id}
        )
        if not round_dicts:
            continue
        agg = aggregate_player_stats(round_dicts)
        if agg:
            rs_rows.append(_row_from_stat_row(agg[0], season.name, season.id))

    # Career-in-League row -- one League-wide aggregation pass.
    career_row: dict | None = None
    career_dicts = build_round_dicts(
        {"game_round__match__season__league": league, "player_id": player.id}
    )
    if career_dicts:
        career_agg = aggregate_player_stats(career_dicts)
        if career_agg:
            career_row = _row_from_stat_row(career_agg[0], "Career", None)

    # LG-03 -- per-Season awards (newest first). Seasons the Player never
    # appeared in are pruned by a cheap existence check before the
    # O(PlayerRoundState) award computation.
    player_awards: list[dict] = []
    for season in seasons:
        if not PlayerRoundState.objects.filter(
            game_round__match__season=season, player_id=player.id
        ).exists():
            continue
        labels = _player_award_labels(compute_season_award_set(season), player.id)
        if labels:
            player_awards.append(
                {
                    "season_id": season.id,
                    "season_name": season.name,
                    "award_labels": labels,
                }
            )

    # LG-04 -- per-Season ratings history in THIS League, oldest first.
    ratings_history = [
        {
            "season_id": r.season_id,
            "season_name": r.season.name,
            "age": r.age,
            "overall_rating": r.overall_rating,
            "potential": r.potential,
            "stats": {name: getattr(r, name) for name in development.STAT_FIELDS},
        }
        for r in PlayerSeasonRating.objects.filter(player=player, season__league=league)
        .select_related("season")
        .order_by("season_id")
    ]

    return PlayerDetail(
        rs_rows=rs_rows,
        career_row=career_row,
        player_awards=player_awards,
        ratings_history=ratings_history,
    )
