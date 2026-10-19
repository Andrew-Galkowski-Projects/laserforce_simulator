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
from teams.models import Player, Team

# --- team screens (T4) imports ---
from collections import defaultdict
from typing import Any

from django.db.models import Q

from matches.models import GameEvent, GameRound, Match, Tournament
from matches.power_rankings_logic import (
    PowerRankingInput,
    PowerRankingRow,
    compute_power_rankings,
    sort_power_rankings,
)
from matches.standings import compute_standings
from matches.team_history_logic import (
    OverallRecord,
    PlayerRollup,
    SeasonRow,
    compute_overall_record,
    compute_player_rollups,
    compute_season_rows,
    round_outcome,
)
from matches.team_stats_logic import TeamStatRow, aggregate_team_stats, sort_team_stats

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


# --- team screens (T4) ---
# Team resolution shared by Team History / Roster / Finances, plus the Team
# History, Roster, Team Stats, Power Rankings and Game Log builders.


def enrolled_teams(season: Season) -> list[Team]:
    """The Teams enrolled in ``season`` (live M2M), by name -- the team-picker
    options and the allowlist for a ``team_id`` on the team screens."""
    return list(season.teams.order_by("name"))


def enrolled_team(season: Season | None, team_id: int) -> Team | None:
    """The enrolled Team ``team_id`` of ``season``, or ``None`` when it isn't
    enrolled (or there is no Season). The API turns ``None`` into a 404."""
    if season is None:
        return None
    return season.teams.filter(pk=team_id).first()


def parse_team_id(raw: str | None) -> int | None:
    """A raw ``?team_id=`` as an int, or ``None`` when missing / malformed
    (the HTML screens' forgiving read; the API validates in its param Form)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_league_team(
    league: League, season: Season, requested_team_id: int | None
) -> Team | None:
    """The Team a team screen shows: ``requested_team_id`` when enrolled, else
    ``league.current_team`` when enrolled, else the alphabetically-first
    enrolled Team (the LG-01g sidebar chain); ``None`` when none is enrolled.
    """
    teams = enrolled_teams(season)
    by_id = {t.id: t for t in teams}
    if requested_team_id is not None and requested_team_id in by_id:
        return by_id[requested_team_id]
    if league.current_team_id is not None and league.current_team_id in by_id:
        return by_id[league.current_team_id]
    return teams[0] if teams else None


@dataclass(frozen=True)
class TeamRoster:
    """The Team Roster screen: ``starters`` as ``(role, Player)`` for the filled
    slots (``Team.active_roster``) and the ``bench`` Players."""

    team: Team
    starters: list[tuple[str, Player]]
    bench: list[Player]


def build_team_roster(team: Team) -> TeamRoster:
    """The Team Roster screen data for ``team``."""
    return TeamRoster(
        team=team, starters=list(team.active_roster), bench=list(team.bench_players)
    )


# Team History -- moved from ``league_screens/team_history.py`` (LG-01z-e /
# LG-06c); the HTML view and the API share these.

# Seasons-table sort keys (``record`` sorts on ``wins``).
TEAM_HISTORY_SEASONS_SORT_KEYS: frozenset[str] = frozenset(
    {"year", "wins", "losses", "ties", "rank"}
)

# Players-table sort keys: top-level ``PlayerRollup`` attrs + nested ``stats``.
TEAM_HISTORY_PLAYERS_SORT_KEYS: tuple[str, ...] = (
    "name",
    "games_played",
    "points_scored",
    "tags_made",
    "times_tagged",
    "missiles_landed",
    "resupplies_given",
    "specials_used",
    "last_season_year",
)

# Nested ``stats`` dict keys (vs top-level ``PlayerRollup`` attrs).
_TH_PLAYER_STAT_KEYS: frozenset[str] = frozenset(
    {
        "points_scored",
        "tags_made",
        "times_tagged",
        "missiles_landed",
        "resupplies_given",
        "specials_used",
    }
)


def _seasons_sort_value(row: SeasonRow, key: str) -> Any:
    """Sort-value extraction on a ``SeasonRow`` (None-safe via tuple).

    ``year`` and ``rank`` are ``int | None`` -- wrapped so None sorts last in
    ascending order.
    """
    value = getattr(row, key)
    if key in ("year", "rank"):
        return (value is None, value if value is not None else 0)
    return value


def _th_players_sort_value(row: PlayerRollup, key: str) -> Any:
    """Sort-value extraction on a ``PlayerRollup`` (None-safe via tuple).

    ``name`` / ``games_played`` are top-level attrs; the six stat keys read
    from ``row.stats``; ``last_season_year`` is ``int | None``, None-safe.
    """
    if key == "last_season_year":
        value = row.last_season_year
        return (value is None, value if value is not None else 0)
    if key in _TH_PLAYER_STAT_KEYS:
        return row.stats.get(key, 0)
    # ``name`` / ``games_played``
    return getattr(row, key)


def team_history_seasons(team: Team) -> dict[int, Season]:
    """Map of ``season_id -> Season`` for every Season the team enrolled in.

    Walks every Season across every League the team is enrolled in. Used
    to scope the Overall tab's round corpus and the Seasons tab's rows.
    """
    out: dict[int, Season] = {}
    for season in Season.objects.select_related("league").all():
        if team.id in enrolled_team_ids(season):
            out[season.id] = season
    return out


def _team_overall_record(team: Team) -> OverallRecord:
    """The Overall tab -- round-level W-L-T + championships."""
    # Every persisted GameRound whose Match belongs to a Season OR to a
    # season-embedded playoff Tournament, and that the team physically played
    # either Side of. red_points / blue_points store the actual per-Round
    # scores; team_red / team_blue store the actual physical sides -- so
    # per-Round W/L/T mirrors LG-01g exactly. A season-embedded playoff Match
    # keeps season=NULL (Part2c-1 #3) and is reached through the FK chain from
    # the playoff Tournament; the season_phases__isnull=False guard
    # distinguishes it from a standalone sandbox Tournament (also season=NULL,
    # but with no SeasonPhase pointing at it). .distinct() collapses the
    # to-many series_match join duplicating a GameRound row.
    rounds = (
        GameRound.objects.filter(
            Q(match__season__isnull=False)
            | Q(match__series_match__node__tournament__season_phases__isnull=False)
            # CONF-02 -- a REGIONAL playoff Tournament is linked by the forward FK
            # Tournament.season_phase (ADR-0035), not by the season_phases reverse
            # manager, so it needs its own term. Note the one-character difference.
            | Q(match__series_match__node__tournament__season_phase__isnull=False),
            match__is_completed=True,
        )
        .filter(Q(team_red=team) | Q(team_blue=team))
        .only("team_red_id", "team_blue_id", "red_points", "blue_points")
        .distinct()
    )
    outcomes: list[str] = []
    for gr in rounds:
        if gr.team_red_id == team.id:
            outcomes.append(round_outcome(gr.red_points, gr.blue_points))
        elif gr.team_blue_id == team.id:
            outcomes.append(round_outcome(gr.blue_points, gr.red_points))

    championships = Season.objects.filter(champion_team=team).count()

    # Count of DISTINCT season-embedded playoff Tournaments the team was seeded
    # into (made the bracket), team-global across all leagues.
    # CONF-02 -- the two terms OR: ``season_phases`` finds a Season-wide embedded
    # playoff, ``season_phase`` a REGIONAL one (ADR-0035). A standalone sandbox
    # Tournament has neither, so it still counts 0. The positional Q objects MUST
    # precede the keyword argument.
    playoff_appearances = (
        Tournament.objects.filter(
            Q(season_phases__isnull=False) | Q(season_phase__isnull=False),
            participants__team=team,
        )
        .distinct()
        .count()
    )

    return compute_overall_record(
        outcomes,
        championships=championships,
        playoff_appearances=playoff_appearances,
    )


def _team_season_rows(team: Team, seasons_by_id: dict[int, Season]) -> list[SeasonRow]:
    """The Seasons tab -- one row per enrolled Season (newest id first)."""
    seasons = sorted(seasons_by_id.values(), key=lambda s: s.id, reverse=True)

    season_dicts: list[dict] = []
    for season in seasons:
        team_ids = enrolled_team_ids(season)
        enrolled = list(Team.objects.filter(id__in=team_ids).values_list("id", "name"))

        # Per-Season completed Matches -> standings dicts for rank, and the
        # team's own per-Round record for the W-L-T columns.
        # LG-07a -- exclude social member-night Matches from the Seasons-tab rank.
        completed = list(
            season.matches.filter(is_completed=True)
            .exclude(season_phase__phase_type="member_night")
            .prefetch_related("game_rounds")
        )
        match_dicts: list[dict] = []
        wins = losses = ties = 0
        for match in completed:
            match_dicts.append(
                {
                    "match_id": match.id,
                    "team_red_id": match.team_red_id,
                    "team_blue_id": match.team_blue_id,
                    "winner_team_id": match.winner_id,
                    "red_rounds_won": match.red_rounds_won,
                    "blue_rounds_won": match.blue_rounds_won,
                    "red_total_points": match.red_total_points,
                    "blue_total_points": match.blue_total_points,
                }
            )
            for gr in match.game_rounds.all():
                if gr.team_red_id == team.id:
                    outcome = round_outcome(gr.red_points, gr.blue_points)
                elif gr.team_blue_id == team.id:
                    outcome = round_outcome(gr.blue_points, gr.red_points)
                else:
                    continue
                if outcome == "W":
                    wins += 1
                elif outcome == "L":
                    losses += 1
                else:
                    ties += 1

        standings = compute_standings(match_dicts, enrolled)
        rank = next((r.rank for r in standings if r.team_id == team.id), None)

        year = season.start_date.year if season.start_date is not None else None
        season_dicts.append(
            {
                "season_id": season.id,
                "year": year,
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "rank": rank,
            }
        )

    return compute_season_rows(season_dicts)


@dataclass(frozen=True)
class TeamHistory:
    """The Team History Overall + Seasons tabs.

    ``seasons`` is sorted by the requested key (default year descending);
    ``seasons_by_id`` resolves each row's ``season_id`` to its Season.
    """

    team: Team
    overall: OverallRecord
    seasons: list[SeasonRow]
    seasons_by_id: dict[int, Season]


def build_team_history(
    team: Team,
    *,
    sort: str = "year",
    direction: str = "desc",
    seasons_by_id: dict[int, Season] | None = None,
) -> TeamHistory:
    """The Team History Overall record and Seasons table for ``team``.

    ``seasons_by_id`` (from :func:`team_history_seasons`) may be passed in to
    share the cross-League Season scan with :func:`build_team_history_players`.
    ``season_id`` desc is the always-appended secondary tiebreak.
    """
    if seasons_by_id is None:
        seasons_by_id = team_history_seasons(team)
    rows = sorted(
        _team_season_rows(team, seasons_by_id),
        key=lambda row: (_seasons_sort_value(row, sort), -row.season_id),
        reverse=(direction == "desc"),
    )
    return TeamHistory(
        team=team,
        overall=_team_overall_record(team),
        seasons=rows,
        seasons_by_id=seasons_by_id,
    )


def build_team_history_players(
    team: Team,
    *,
    sort: str = "name",
    direction: str = "asc",
    seasons_by_id: dict[int, Season] | None = None,
) -> list[PlayerRollup]:
    """The Team History Players tab: every Player who appeared for ``team``,
    with career aggregates, sorted (``player_id`` is the secondary tiebreak)."""
    if seasons_by_id is None:
        seasons_by_id = team_history_seasons(team)
    # Every PlayerRoundState row where the player physically played for the
    # team in one of the team's GameRounds (either Side).
    states = (
        PlayerRoundState.objects.filter(
            Q(game_round__team_red=team, team_color="red")
            | Q(game_round__team_blue=team, team_color="blue")
        )
        .select_related("player", "game_round", "game_round__match")
        .order_by("id")
    )

    # Resolve each round's Season start-year for the "last season played"
    # column without a per-row query.
    season_year_by_id = {
        sid: (s.start_date.year if s.start_date is not None else None)
        for sid, s in seasons_by_id.items()
    }

    player_round_dicts: list[dict] = []
    for prs in states:
        match = prs.game_round.match
        season_id = match.season_id if match is not None else None
        season_year = season_year_by_id.get(season_id) if season_id else None
        player_round_dicts.append(
            {
                "player_id": prs.player_id,
                "player_name": prs.player.name,
                "on_team": prs.player.team_id == team.id,
                "season_year": season_year,
                "points_scored": prs.points_scored,
                "tags_made": prs.tags_made,
                "times_tagged": prs.times_tagged,
                "missiles_landed": prs.missiles_landed,
                "resupplies_given": prs.resupplies_given,
                "specials_used": prs.specials_used,
            }
        )

    return sorted(
        compute_player_rollups(player_round_dicts),
        key=lambda row: (_th_players_sort_value(row, sort), row.player_id),
        reverse=(direction == "desc"),
    )


# Team Stats -- moved from ``league_screens/team_stats.py`` (LG-01z-p).


def build_team_stats(
    displayed_season: Season | None,
    round_filter: dict | None,
    *,
    sort: str = "team",
    direction: str = "asc",
) -> list[TeamStatRow]:
    """The Team Stats rows over a Season scope's ``round_filter``.

    Teams come from ``displayed_season``'s enrolment (the League's current
    roster) even under a Career / past-Season scope. Empty when
    ``round_filter`` is ``None`` (no Season).
    """
    if round_filter is None:
        return []
    team_ids = enrolled_team_ids(displayed_season) if displayed_season else []
    teams_by_id: dict[int, Team] = Team.objects.in_bulk(team_ids)
    enrolled = [
        (tid, teams_by_id[tid].name if tid in teams_by_id else "") for tid in team_ids
    ]

    # Re-point the scope onto the two join shapes: GameRound joins via
    # ``match__season...`` (verbatim); PRS / GameEvent via ``game_round__...``.
    prs_filter = {f"game_round__{k}": v for k, v in round_filter.items()}

    # --- Per-round dicts (one per Team appearance in a Round) -------------
    rounds_qs = GameRound.objects.filter(**round_filter)

    # Per-(round, color) survivor + tag/tagged sums from PlayerRoundState.
    prs_qs = PlayerRoundState.objects.filter(**prs_filter).values(
        "game_round_id", "team_color", "final_lives", "tags_made", "times_tagged"
    )
    survivors: dict[tuple[int, str], int] = defaultdict(int)
    tags_landed: dict[tuple[int, str], int] = defaultdict(int)
    times_tagged: dict[tuple[int, str], int] = defaultdict(int)
    for prs in prs_qs:
        key = (prs["game_round_id"], prs["team_color"])
        if prs["final_lives"] > 0:
            survivors[key] += 1
        tags_landed[key] += prs["tags_made"]
        times_tagged[key] += prs["times_tagged"]

    team_rounds: list[dict] = []
    for gr in rounds_qs:
        for color, team_id, points_for, points_against in (
            ("red", gr.team_red_id, gr.red_points, gr.blue_points),
            ("blue", gr.team_blue_id, gr.blue_points, gr.red_points),
        ):
            if team_id is None:
                continue
            key = (gr.id, color)
            team_rounds.append(
                {
                    "team_id": team_id,
                    "points_for": points_for,
                    "points_against": points_against,
                    "survivors": survivors[key],
                    "tags_landed": tags_landed[key],
                    "times_tagged": times_tagged[key],
                }
            )

    # --- Per-event dicts (one per relevant GameEvent) ---------------------
    # Resolve each event's actor team_id via the actor's PlayerRoundState in
    # that Round (team_color -> team_red/team_blue). Build the lookup once.
    actor_team_by_key: dict[tuple[int, int], int] = {}
    color_prs = PlayerRoundState.objects.filter(**prs_filter).values(
        "game_round_id", "player_id", "team_color"
    )
    round_sides: dict[int, tuple[int | None, int | None]] = {
        gr.id: (gr.team_red_id, gr.team_blue_id) for gr in rounds_qs
    }
    for row in color_prs:
        red_id, blue_id = round_sides.get(row["game_round_id"], (None, None))
        if row["team_color"] == "red":
            actor_team: int | None = red_id
        elif row["team_color"] == "blue":
            actor_team = blue_id
        else:
            actor_team = None
        if actor_team is not None:
            actor_team_by_key[(row["game_round_id"], row["player_id"])] = actor_team

    events_qs = GameEvent.objects.filter(
        event_type__in=["base_capture", "missiled", "special", "nuke_cancelled"],
        **prs_filter,
    ).values(
        "game_round_id",
        "actor_id",
        "event_type",
        "points_awarded",
        "metadata",
    )

    team_events: list[dict] = []
    for ev in events_qs:
        event_team = actor_team_by_key.get((ev["game_round_id"], ev["actor_id"]))
        if event_team is None:
            continue
        etype = ev["event_type"]
        meta = ev["metadata"] or {}
        if etype == "base_capture":
            team_events.append({"team_id": event_team, "kind": "base_capture"})
        elif etype == "missiled":
            team_events.append(
                {
                    "team_id": event_team,
                    "kind": "missiled",
                    "hit": meta.get("result") == "hit",
                }
            )
        elif etype == "nuke_cancelled":
            team_events.append({"team_id": event_team, "kind": "nuke_cancelled"})
        elif etype == "special":
            # Only nuke detonations count (activation rows are skipped).
            if ev["points_awarded"] == 500 and "targets" in meta:
                team_events.append({"team_id": event_team, "kind": "nuke_detonation"})

    rows = aggregate_team_stats(team_rounds, team_events, enrolled)
    return sort_team_stats(rows, sort, direction)


# Power Rankings -- moved from ``league_screens/power_rankings.py`` (LG-01z-b).


def _team_mean_rating(team: Team) -> float:
    """Mean ``overall_rating`` over a team's active-roster players.

    Returns ``0.0`` when the team has no active slots filled, so the
    power-score normalization never trips on an empty iterable.
    """
    actives = team.active_players
    if not actives:
        return 0.0
    return sum(p.overall_rating for p in actives) / len(actives)


def build_power_rankings(
    displayed_season: Season | None,
    round_filter: dict | None,
    *,
    sort: str = "rank",
    direction: str = "asc",
) -> list[tuple[PowerRankingRow, Team | None]]:
    """The Power Rankings rows over a Season scope's ``round_filter``, each
    paired with its Team.

    Ranks ``displayed_season``'s enrolled Teams (even under a Career /
    past-Season scope) by the composite power score: mean active-roster
    rating, win% from completed non-member-night Matches, and average score
    diff per Round. Empty when ``round_filter`` is ``None`` (no Season).
    """
    if round_filter is None:
        return []
    team_ids = enrolled_team_ids(displayed_season) if displayed_season else []
    teams_by_id: dict[int, Team] = Team.objects.in_bulk(team_ids)

    # Re-point the scope onto the two join shapes: Match filters directly
    # (strip the leading ``match__``); GameRound joins via ``match__season...``.
    match_filter = {k[len("match__") :]: v for k, v in round_filter.items()}

    # --- Component 2: win% via compute_standings over completed Matches ---
    # LG-07a -- member nights are social, not ranked: they must not move the
    # power rankings either.
    completed_qs = Match.objects.filter(is_completed=True, **match_filter).exclude(
        season_phase__phase_type="member_night"
    )
    completed_matches: list[dict] = []
    for match in completed_qs:
        completed_matches.append(
            {
                "match_id": match.id,
                "team_red_id": match.team_red_id,
                "team_blue_id": match.team_blue_id,
                "winner_team_id": match.winner_id,
                "red_rounds_won": match.red_rounds_won,
                "blue_rounds_won": match.blue_rounds_won,
                "red_total_points": match.red_total_points,
                "blue_total_points": match.blue_total_points,
            }
        )
    enrolled_for_standings = [
        (tid, teams_by_id[tid].name if tid in teams_by_id else "") for tid in team_ids
    ]
    standings = compute_standings(completed_matches, enrolled_for_standings)
    win_pct_by_id: dict[int, float] = {}
    for standing in standings:
        played = standing.wins + standing.losses + standing.ties
        win_pct_by_id[standing.team_id] = (standing.wins / played) if played else 0.0

    # --- Component 3: avg score diff per Round from each team's view -------
    rounds_qs = GameRound.objects.filter(**round_filter).select_related("match")
    diff_sums: dict[int, float] = defaultdict(float)
    diff_counts: dict[int, int] = defaultdict(int)
    for game_round in rounds_qs:
        red_id = game_round.team_red_id
        blue_id = game_round.team_blue_id
        if red_id is not None:
            diff_sums[red_id] += game_round.red_points - game_round.blue_points
            diff_counts[red_id] += 1
        if blue_id is not None:
            diff_sums[blue_id] += game_round.blue_points - game_round.red_points
            diff_counts[blue_id] += 1

    # --- Assemble per-team inputs (enrolled teams only) -------------------
    inputs: list[PowerRankingInput] = []
    for tid in team_ids:
        team = teams_by_id.get(tid)
        if team is None:
            # Defensive -- a stale snapshot id whose Team was deleted. Skip
            # it rather than crash the page.
            continue
        count = diff_counts.get(tid, 0)
        avg_diff = (diff_sums[tid] / count) if count else 0.0
        inputs.append(
            PowerRankingInput(
                team_id=tid,
                team_name=team.name,
                mean_rating=_team_mean_rating(team),
                win_pct=win_pct_by_id.get(tid, 0.0),
                avg_score_diff=avg_diff,
            )
        )

    ranked = sort_power_rankings(compute_power_rankings(inputs), sort, direction)
    return [(row, teams_by_id.get(row.team_id)) for row in ranked]


# Game Log -- moved from ``league_screens/game_log.py`` (LG-01z-l / LG-06c).

GAME_LOG_SORT_KEYS: tuple[str, ...] = (
    "matchday",
    "date_played",
    "team_red",
    "team_blue",
    "score",
    "winner",
)


def _game_log_sort_value(row: dict, key: str) -> Any:
    """Sort-value extraction per the LG-06c contract (None-safe via tuple)."""
    if key == "matchday":
        return row["matchday"]
    if key == "date_played":
        date_played = row["date_played"]
        return (date_played is None, date_played)
    if key == "team_red":
        return row["team_red"].name if row["team_red"] else ""
    if key == "team_blue":
        return row["team_blue"].name if row["team_blue"] else ""
    if key == "score":
        return row["red_points"] + row["blue_points"]
    # key == "winner"
    return row["winner"].name if row["winner"] else ""


def build_game_log(
    round_filter: dict | None,
    *,
    team_id: int | None = None,
    sort: str = "date_played",
    direction: str = "asc",
) -> list[dict]:
    """The Game Log rows over a Season scope's ``round_filter``: one dict per
    played ``GameRound`` (``game_round``, ``round_id``, ``matchday``,
    ``date_played``, ``team_red``, ``team_blue``, ``red_points``,
    ``blue_points``, ``winner``), optionally limited to Rounds ``team_id``
    played. ``round_id`` is the always-appended stable secondary tiebreak.
    Empty when ``round_filter`` is ``None`` (no Season).
    """
    if round_filter is None:
        return []
    rounds_qs = (
        GameRound.objects.filter(**round_filter)
        .select_related("match", "team_red", "team_blue", "winner")
        .order_by("id")
    )
    if team_id is not None:
        rounds_qs = rounds_qs.filter(Q(team_red_id=team_id) | Q(team_blue_id=team_id))

    rows: list[dict] = [
        {
            "game_round": game_round,
            "round_id": game_round.id,
            "matchday": game_round.round_number,
            "date_played": game_round.date_played,
            "team_red": game_round.team_red,
            "team_blue": game_round.team_blue,
            "red_points": game_round.red_points,
            "blue_points": game_round.blue_points,
            "winner": game_round.winner,
        }
        for game_round in rounds_qs
    ]
    rows.sort(
        key=lambda row: (_game_log_sort_value(row, sort), row["round_id"]),
        reverse=(direction == "desc"),
    )
    return rows
