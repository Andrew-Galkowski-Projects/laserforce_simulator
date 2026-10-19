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

# --- player list screens (T2) imports ---
from django.db.models import F, QuerySet

from matches.development import STAT_FIELDS
from matches.league_leaders_logic import compute_leaderboards
from matches.season_dashboard import LeaderRow
from teams.views import _SORT_KEYS as RATING_SORT_KEYS

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


# ---------------------------------------------------------------------------
# --- player list screens (T2) ---
# ---------------------------------------------------------------------------


def build_player_stats(
    scope: SeasonScope,
    *,
    rate: str,
    team_id: int | None,
    sort: str,
    direction: str,
) -> list[PlayerStatRow]:
    """The Player Stats screen rows over ``scope``.

    Pipeline order (LG-06d, locked): aggregate -> ``apply_rate`` -> team
    filter -> sort (so the sort runs on the rate-adjusted values). Empty in
    the empty state (``scope.round_filter is None``).
    """
    if scope.round_filter is None:
        return []
    prs_filter = {f"game_round__{k}": v for k, v in scope.round_filter.items()}
    rows = aggregate_player_stats(build_round_dicts(prs_filter))
    rows = apply_rate(rows, rate)
    if team_id is not None:
        rows = [r for r in rows if r.team_id == team_id]
    return sort_player_stats(rows, sort, direction)


def _annotate_overall_rating(qs: QuerySet[Player]) -> QuerySet[Player]:
    """Annotate ``overall_rating_db`` (mean of the 19 stats) so the LG-00c
    ``overall_rating`` sort key has an ORM target (mirrors
    ``teams.views.player_list``, incl. the capital-O ``Offensive_synergy``)."""
    total = F(STAT_FIELDS[0])
    for field in STAT_FIELDS[1:]:
        total = total + F(field)
    return qs.annotate(overall_rating_db=total / 19.0)


def _order_rated_players(
    qs: QuerySet[Player], sort: str, direction: str
) -> QuerySet[Player] | list[Player]:
    """Order a ratings-screen queryset by an LG-00c sort key.

    ``preferred_roles`` sorts in Python (a JSON list); ``potential`` is
    nullable and sorts nulls last in BOTH directions; every other key is an
    ORM sort through ``teams.views._SORT_KEYS``. ``name`` breaks ties.
    """
    if sort == "preferred_roles":
        rows = list(qs)
        rows.sort(
            key=lambda p: (",".join(p.preferred_roles or []), p.name),
            reverse=(direction == "desc"),
        )
        return rows
    if sort == "potential":
        ordering = (
            F("potential").desc(nulls_last=True)
            if direction == "desc"
            else F("potential").asc(nulls_last=True)
        )
        return qs.order_by(ordering, "name")
    prefix = "" if direction == "asc" else "-"
    return qs.order_by(prefix + RATING_SORT_KEYS[sort], "name")


def build_player_ratings_queryset(
    displayed_season: Season | None,
    *,
    team_id: int | None,
    sort: str,
    direction: str,
) -> QuerySet[Player] | list[Player]:
    """The Player Ratings screen: Players on the displayed Season's Teams.

    Live ratings (no Season scope); ``team_id`` narrows to one enrolled Team.
    Empty (``[]``) when the League has no Season.
    """
    if displayed_season is None:
        return []
    enrolled_ids = set(displayed_season.teams.values_list("id", flat=True))
    qs = Player.objects.select_related("team").filter(team_id__in=enrolled_ids)
    if team_id is not None:
        qs = qs.filter(team_id=team_id)
    return _order_rated_players(_annotate_overall_rating(qs), sort, direction)


def build_free_agents_queryset(
    league: League,
    displayed_season: Season | None,
    *,
    sort: str,
    direction: str,
) -> QuerySet[Player] | list[Player]:
    """The Free Agents screen: Players on ``league.free_agent_pool``.

    A League with no pool yields no rows (every Player has a team). Empty
    (``[]``) when the League has no Season -- the HTML empty state.
    """
    if displayed_season is None:
        return []
    qs = Player.objects.select_related("team").filter(team_id=league.free_agent_pool_id)
    return _order_rated_players(_annotate_overall_rating(qs), sort, direction)


LEADER_BOARDS: tuple[str, ...] = ("avg_tags", "avg_score", "fewest_tagged", "tag_ratio")


def build_leaderboards(scope: SeasonScope) -> dict[str, list[LeaderRow]]:
    """The League Leaders screen: four top-10 boards over ``scope``'s Rounds,
    each in the pure module's default rank order.

    Every board is ``[]`` in the empty state (``scope.round_filter is None``).
    """
    if scope.round_filter is None:
        return {board: [] for board in LEADER_BOARDS}
    prs_filter = {f"game_round__{k}": v for k, v in scope.round_filter.items()}
    # order_by("id") makes the "last row wins" defensive fallback in the pure
    # module deterministic.
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
    player_rounds: list[dict] = []
    for prs in prs_qs:
        game_round = prs.game_round
        if prs.team_color == "red":
            team = game_round.team_red
        elif prs.team_color == "blue":
            team = game_round.team_blue
        else:
            team = None
        player_rounds.append(
            {
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "role": prs.role,
                "team_id": team.id if team is not None else 0,
                "team_name": team.name if team is not None else "",
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "points_scored": prs.points_scored,
            }
        )
    return compute_leaderboards(player_rounds, limit=10)


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


# --- feats + playoffs (T6) ---------------------------------------------------
# Imports kept inside this block so sibling tickets' edits to the header never
# collide with it.

from collections import defaultdict  # noqa: E402

from matches import stat_feats  # noqa: E402
from matches.models import GameEvent, GameRound, Match  # noqa: E402
from teams.models import Team  # noqa: E402

# LG-06e -- every Statistical Feats column is sortable: the descriptor /
# identity columns plus the 13 box-score columns. Default ``round`` desc.
FEATS_SORT_KEYS: frozenset[str] = frozenset(
    {
        # descriptors / identity
        "name",
        "role",
        "team",
        "opp",
        "result",
        "season",
        "round",
        "feat",
        # box-score columns (13)
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
    }
)


def is_nuke_detonation(event: GameEvent) -> bool:
    """A `special` GameEvent that is a nuke DETONATION (not activation).

    RV-02 / EventLog: nuke detonation is the ``special`` row carrying
    ``metadata["targets"]`` with ``points_awarded == 500``. The activation
    row (``points_awarded == 0`` + ``metadata["fires_at"]``) is excluded.
    """
    if event.event_type != "special":
        return False
    if event.points_awarded != 500:
        return False
    metadata = event.metadata or {}
    return "targets" in metadata


def feat_row_sort_value(row: stat_feats.FeatRow, key: str) -> str | int | float:
    """Extract the sort value for ``row`` under sort ``key`` (LG-06e section 3.4).

    String descriptor keys sort case-insensitively; ``result`` sorts lexically
    ("L" < "T" < "W"); the box-score keys sort numerically; ``feat`` sorts on a
    stable join of the row's badge kinds. No key may raise on a ``None`` (the
    dataclass defaults descriptors to ``""``).
    """
    if key == "name":
        return row.player_name.lower()
    if key == "role":
        return row.role
    if key == "team":
        return row.team_name.lower()
    if key == "opp":
        return row.opp_team_name.lower()
    if key == "result":
        return row.result
    if key == "season":
        return row.season_name.lower()
    if key == "round":
        return row.round_id
    if key == "feat":
        return ",".join(sorted(b.kind for b in row.feats))
    # Any box-score key.
    return row.stats.get(key, 0.0)


def feats_enrolled_teams(displayed_season: Season | None) -> list[Team]:
    """The Statistical Feats team-filter options: the displayed Season's
    enrolled Teams (live M2M) by name; ``[]`` when the League has no Season."""
    if displayed_season is None:
        return []
    return list(displayed_season.teams.order_by("name"))


def _feats_player_rounds(prs_filter: dict) -> list[dict]:
    """The EXTENDED per-(player, round) ``stat_feats`` seam dicts."""
    # Per-Round nuke-detonation counts, keyed (round_id, actor_id).
    detonations: dict[tuple[int, int], int] = defaultdict(int)
    events_qs = GameEvent.objects.filter(
        event_type="special",
        points_awarded=500,
        **prs_filter,
    ).only("game_round_id", "actor_id", "event_type", "points_awarded", "metadata")
    for event in events_qs:
        if is_nuke_detonation(event):
            detonations[(event.game_round_id, event.actor_id)] += 1

    prs_qs = (
        PlayerRoundState.objects.filter(**prs_filter)
        .select_related(
            "player",
            "game_round",
            "game_round__match",
            "game_round__match__season",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )
    player_rounds: list[dict] = []
    for prs in prs_qs:
        game_round = prs.game_round
        # Own team + opponent team for this Round, from the player's side.
        if prs.team_color == "red":
            own_team = game_round.team_red
            opp_team = game_round.team_blue
            own_points = game_round.red_points
            opp_points = game_round.blue_points
        elif prs.team_color == "blue":
            own_team = game_round.team_blue
            opp_team = game_round.team_red
            own_points = game_round.blue_points
            opp_points = game_round.red_points
        else:
            own_team = None
            opp_team = None
            own_points = 0
            opp_points = 0

        # Per-ROUND result (own vs opp points in THIS GameRound -- NOT the
        # Match winner).
        if own_points > opp_points:
            result = "W"
        elif own_points < opp_points:
            result = "L"
        else:
            result = "T"

        match = game_round.match
        season = match.season if match is not None else None

        player_rounds.append(
            {
                # identity / deep-link
                "round_id": game_round.id,
                "match_id": game_round.match_id,
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "role": prs.role,
                "team_id": own_team.id if own_team is not None else None,
                "team_name": own_team.name if own_team is not None else "",
                # descriptor columns
                "opp_team_name": opp_team.name if opp_team is not None else "",
                "result": result,
                "season_id": season.id if season is not None else None,
                "season_name": season.name if season is not None else "",
                # box-score line (13 BOX_SCORE_KEYS, per-round values).
                # ``get_mvp`` / ``get_accuracy`` are @properties (no parens).
                "points_scored": prs.points_scored,
                "mvp": float(prs.get_mvp),
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "accuracy": float(prs.get_accuracy),
                "final_lives": prs.final_lives,
                "resupplies_given": prs.resupplies_given,
                "missiles_landed": prs.missiles_landed,
                "specials_used": prs.specials_used,
                "follow_up_shots": prs.follow_up_shots,
                "reaction_shots": prs.reaction_shots,
                "combo_resupply_count": prs.combo_resupply_count,
                "nuke_detonations": detonations.get((game_round.id, prs.player_id), 0),
                # predicate-only field (NOT a box-score column)
                "shots_missed": prs.shots_missed,
            }
        )
    return player_rounds


def _feats_matches(round_filter: dict) -> list[dict]:
    """The per-Match ``stat_feats`` seam dicts (comeback win)."""
    match_filter = {k[len("match__") :]: v for k, v in round_filter.items()}
    round2_by_match: dict[int, int] = {}
    for gr in GameRound.objects.filter(round_number=2, **round_filter).only(
        "id", "match_id"
    ):
        if gr.match_id is not None:
            round2_by_match[gr.match_id] = gr.id

    matches: list[dict] = []
    matches_qs = (
        Match.objects.filter(is_completed=True, **match_filter)
        .select_related("team_red", "team_blue", "winner")
        .order_by("id")
    )
    for match in matches_qs:
        matches.append(
            {
                "match_id": match.id,
                "round_id": round2_by_match.get(match.id),
                "winner_team_id": match.winner_id,
                "winner_team_name": (
                    match.winner.name if match.winner is not None else ""
                ),
                "red_team_id": match.team_red_id,
                "blue_team_id": match.team_blue_id,
                "red_round1_points": match.red_round1_points,
                "blue_round1_points": match.blue_round1_points,
            }
        )
    return matches


def build_statistical_feats(
    scope: SeasonScope,
    *,
    team_id: int | None,
    sort: str,
    direction: str,
) -> tuple[list[stat_feats.FeatRow], list[stat_feats.TeamFeatRecord]]:
    """The Statistical Feats screen: ``(feat_rows, team_feats)``.

    ``feat_rows`` -- one row per (Player, Round) that achieved a feat over
    ``scope``, limited to ``team_id`` when given, sorted by ``sort`` /
    ``direction`` with the deterministic secondary tiebreak (round_id desc,
    player_id asc). ``team_feats`` -- the comeback-win records. Both empty in
    the empty state (``scope.round_filter is None``).
    """
    if scope.round_filter is None:
        return [], []
    prs_filter = {f"game_round__{k}": v for k, v in scope.round_filter.items()}
    player_rounds = _feats_player_rounds(prs_filter)
    matches = _feats_matches(scope.round_filter)

    # LG-06b -- apply the team filter to both seam inputs before scanning.
    if team_id is not None:
        player_rounds = [pr for pr in player_rounds if pr["team_id"] == team_id]
        matches = [
            m for m in matches if team_id in {m["red_team_id"], m["blue_team_id"]}
        ]

    feat_rows, team_feats = stat_feats.scan_feats(player_rounds, matches)

    # LG-06c -- sort by the secondary key first (stable), then by the primary
    # so equal-primary rows keep the secondary order. ``reverse`` only flips
    # the PRIMARY pass.
    feat_rows.sort(key=lambda r: (-r.round_id, r.player_id))
    feat_rows.sort(
        key=lambda r: feat_row_sort_value(r, sort), reverse=(direction == "desc")
    )
    return feat_rows, team_feats


@dataclass(frozen=True)
class PlayoffsScreen:
    """The Playoffs screen for one Season.

    - ``brackets``: one dict per bracket section, in phase-ordinal then
      Conference order -- ``phase``, ``tournament`` (or ``None`` while the
      phase is unbuilt), ``name``, ``rounds`` (``_build_rounds(t)["winners"]``),
      ``champion``, ``pending``, ``conference``, ``key`` (the DOM-id
      discriminator), ``stage`` and ``stage_label`` (CONF-02/03/04).
    - ``worlds_qualifiers``: ``Season.worlds_qualifiers()`` (``[]`` until
      qualification completes, and for a 0/1-Conference Season).
    """

    brackets: list[dict]
    worlds_qualifiers: list


def build_playoffs(season: Season | None) -> PlayoffsScreen:
    """Derive the Playoffs screen for ``season``; empty for ``None`` or a
    Season with no ``tournament`` phase.

    Embedded season tournaments are single-elimination, so every node lives in
    the ``"winners"`` slice of ``matches.tournament_views._build_rounds``.
    """
    if season is None:
        return PlayoffsScreen(brackets=[], worlds_qualifiers=[])
    # Deferred import -- keep the import graph shallow and dodge any
    # tournament_views <-> views/tasks import-order surprise at load time.
    from matches.tournament_views import _build_rounds

    brackets: list[dict] = []
    phases = (
        season.phases.filter(phase_type="tournament")
        .select_related("tournament")
        .order_by("ordinal")
    )
    for phase in phases:
        # CONF-02 -- a phase may hold N regional brackets, one per Conference
        # (ADR-0035). A 0/1-Conference Season yields the single Season-wide
        # bracket.
        tournaments = season.tournaments_for_phase(phase)
        is_worlds = phase.tournament_mode == "worlds"
        if not tournaments:
            # Phase exists but its bracket has not been built yet: a pending
            # stub. CONF-04 -- the stub gets the same Worlds treatment as the
            # built entry, so a phase's DOM id is stable across the unbuilt ->
            # built transition; its heading mirrors the built Tournament's
            # name (``"<season> Worlds"``) so the badge is not echoed.
            brackets.append(
                {
                    "phase": phase,
                    "tournament": None,
                    "name": f"{season.name} Worlds" if is_worlds else "Playoffs",
                    "rounds": [],
                    "champion": None,
                    "pending": True,
                    "conference": None,
                    "key": (
                        f"{phase.ordinal}-worlds" if is_worlds else str(phase.ordinal)
                    ),
                    "stage": "worlds" if is_worlds else "",
                    "stage_label": "Worlds" if is_worlds else "",
                }
            )
            continue
        for tournament in tournaments:
            rounds = _build_rounds(tournament)["winners"]
            conference = tournament.conference
            # ``key`` is the DOM-id discriminator: ``str(phase.ordinal)`` for a
            # Season-wide bracket (every pre-CONF-02 id unchanged), a
            # ``-<conf>`` regional suffix (CONF-02), ``-lc`` for Last chance
            # (CONF-03), ``-worlds`` for Worlds (CONF-04). The Worlds branch is
            # first: the Worlds Tournament's ``conference`` is ``None``.
            # ``is_worlds`` asks the PHASE flavour, ``is_last_chance`` the
            # BRACKET stage.
            is_last_chance = tournament.qualifier_stage == "last_chance"
            if is_worlds:
                key = f"{phase.ordinal}-worlds"
                stage = "worlds"
            elif conference is None:
                key = str(phase.ordinal)
                stage = ""
            elif is_last_chance:
                key = f"{phase.ordinal}-{conference.ordinal}-lc"
                stage = "last_chance"
            else:
                key = f"{phase.ordinal}-{conference.ordinal}"
                stage = "regional_playoff"
            # Deliberately EMPTY for a Season-wide and a regional bracket.
            if is_worlds:
                stage_label = "Worlds"
            elif is_last_chance:
                stage_label = "Last Chance Qualifier"
            else:
                stage_label = ""
            brackets.append(
                {
                    "phase": phase,
                    "tournament": tournament,
                    "name": tournament.name,
                    "rounds": rounds,
                    "champion": tournament.champion,
                    # Only an unseeded Last-chance bracket is still ``setup``.
                    "pending": tournament.state == "setup",
                    "conference": conference,
                    "key": key,
                    "stage": stage,
                    "stage_label": stage_label,
                }
            )
    return PlayoffsScreen(
        brackets=brackets, worlds_qualifiers=season.worlds_qualifiers()
    )
