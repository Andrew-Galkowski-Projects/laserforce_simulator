"""ANG-01d-1 — League / Season lifecycle + read services.

Shared by the HTML league views (``matches/league_views.py``) and the REST
API (``matches/league_api_views.py``). League creation, the Season rollover,
the owner-evaluation / finance writers, League teardown, the dashboard /
standings / schedule / history / awards / conference builders all live here;
the views only parse requests and render.

Must NOT import ``matches.league_views`` (the views import this module and
re-export every moved name for back-compat). Randomness goes through
``import random`` + ``random.Random()`` so a test patch on
``matches.league_services.random.Random`` (the global ``random`` module) still
reaches the moved code.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta
from math import ceil
from typing import TYPE_CHECKING, Iterable, Optional

from django.db import transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Player, Team
from teams.player_generator import LEAGUE_SPREAD_DELTAS, compute_tier_means
from teams.views import _generate_free_agents, _generate_teams

from . import development, finance, owner_mood
from .development import STAT_FIELDS
from .league_templates import LeagueTemplate
from .models import (
    Conference,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    PlayerRoundState,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
    TeamSeasonFinance,
    Tournament,
)
from .season_awards import (
    AwardSet,
    AwardWinner,
    ROLE_KEYS,
    compute_season_awards,
    pick_finals_mvp,
)
from .season_dashboard import (
    LeaderRow,
    compute_leaders,
    find_next_fixture,
    round_progress,
)
from .season_snapshot import (
    award_set_from_json,
    award_set_to_json,
    completed_season_cached,
    leader_boards_from_json,
    leader_boards_to_json,
)
from .standings import StandingsRow, compute_standings

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

    from .forms import CreateLeagueForm


# ---------------------------------------------------------------------------
# ANG-01d-1 — domain exceptions (the API maps them to 409 APIExceptions; the
# HTML views map them to their existing redirect / 400 responses).
# ---------------------------------------------------------------------------


class SeasonInProgressError(Exception):
    """The League already has a non-completed Season (``.season``)."""

    def __init__(self, season: Season) -> None:
        super().__init__("The League already has a Season in progress.")
        self.season = season


class NoCompletedSeasonError(Exception):
    """The League has no completed Season to roll from."""


class ManagerFiredError(Exception):
    """The Manager was fired and has not yet reassigned (``.evaluation``)."""

    def __init__(self, evaluation: OwnerEvaluation) -> None:
        super().__init__(
            "The Manager was fired. Choose a new team before starting the next Season."
        )
        self.evaluation = evaluation


# ---------------------------------------------------------------------------
# Helpers moved verbatim from ``matches/league_views.py`` (contract §2.1).
# ---------------------------------------------------------------------------


# FIN-03 — strength-ranked initial budget band for newly-created Leagues. The
# strongest enrolled Team seeds at SEED_BUDGET_MAX, the weakest at
# SEED_BUDGET_MIN, rank-linear between; a single-Team League seeds at
# SEED_BUDGET_SINGLE.
SEED_BUDGET_MIN = 20
SEED_BUDGET_MAX = 90
SEED_BUDGET_SINGLE = 55


def _compute_team_overall(team: Team) -> float:
    """Mean ``overall_rating`` over a team's six active-roster players.

    Returns ``0.0`` when the team has no slots filled (e.g. the Free
    Agents Team) so the draft-preview sort never trips on an empty
    iterable. Used only by the LG-01 draft-preview ordering on
    ``season_standings``.
    """
    actives = team.active_players
    if not actives:
        return 0.0
    return sum(p.overall_rating for p in actives) / len(actives)


# LG-06g — sortable Standings columns. Every column (the 9 LG-01 columns
# plus the 8 form / side-detail columns) is sortable via the LG-06c
# ``_coerce_sort_key`` / ``_coerce_dir`` pattern; sorting runs view-side on
# the materialised rows and never renumbers ``rank`` (it stays the frozen
# standings position, mirroring the LG-06c League-Leaders precedent).
STANDINGS_SORT_KEYS: frozenset[str] = frozenset(
    {
        "rank",
        "team",
        "matches_played",
        "wins",
        "losses",
        "ties",
        "league_points",
        "round_wins",
        "total_score",
        "match_streak",
        "match_l5",
        "round_streak",
        "round_l5",
        "red_wlt",
        "blue_wlt",
        "red_points_for",
        "blue_points_for",
    }
)


def _standings_row_attr(row: "StandingsRow | dict", key: str):
    """Attr-or-key access so the LG-06g sort works on BOTH ``StandingsRow``
    dataclasses (active / completed) and the zeroed draft-preview dicts
    (mirrors the existing ``_row_team_id`` attr-or-key precedent)."""
    if hasattr(row, key):
        return getattr(row, key)
    return row[key]


def _streak_sort_value(streak: tuple) -> int:
    """Signed run length for a ``(kind, length)`` streak: ``W`` ⇒ ``+length``,
    ``L`` ⇒ ``-length``, ``T`` / ``""`` ⇒ ``0``."""
    kind, length = streak
    if kind == "W":
        return length
    if kind == "L":
        return -length
    return 0


def _standings_sort_value(row: "StandingsRow | dict", team_name: str, key: str):
    """LG-06g — sort-value extraction over a ``StandingsRow`` (or draft dict).

    Record / L5 columns sort by ``(wins desc, losses asc)`` under one
    direction via a ``(wins, -losses)`` key; streaks by signed run length;
    ``team`` by the team name; everything else by the raw int.
    """
    if key == "team":
        return team_name
    if key in ("match_streak", "round_streak"):
        return _streak_sort_value(_standings_row_attr(row, key))
    if key in ("match_l5", "round_l5", "red_wlt", "blue_wlt"):
        wins, losses, _ties = _standings_row_attr(row, key)
        return (wins, -losses)
    return _standings_row_attr(row, key)


def _validate_conference_partition(
    names: "list[str]",
    team_to_conf_idx: "dict[int, int | None]",
    enrolled_team_ids: "set[int]",
    *,
    min_per_conference: int = 2,
) -> "tuple[list[str], list[tuple[str, list[int]]] | None]":
    """Validate a submitted Conference partition of a draft Season's Teams.

    ``names`` is the ordered list of submitted Conference names;
    ``team_to_conf_idx`` maps each enrolled team id to the 0-based index into
    ``names`` of its assigned Conference (``None`` = unassigned).

    Returns ``(errors, normalized)`` where ``normalized`` is
    ``[(name, sorted_team_ids), ...]`` on success (``None`` on error). An empty
    submission (no Conference names) is the valid **zero-Conference** (flat
    Season) case ⇒ ``([], [])``. The locked rules: every Conference name
    non-empty; a **full partition** (every enrolled Team assigned to a valid
    Conference); each Conference has **>= 2** Teams.

    CONF-02 — ``min_per_conference`` raises that floor to
    ``MIN_BRACKET_PARTICIPANTS`` when the Season carries a ``tournament``
    phase, because each Conference then has to field its own **Regional
    playoff** bracket and the engine cannot build one from fewer than 4 Teams.
    Left at the CONF-05 default of 2 for a Season with no tournament phase.
    """
    stripped = [n.strip() for n in names]
    # Zero-Conference (flat Season) — no Conference names submitted.
    if not stripped:
        return [], []

    errors: list[str] = []
    if any(n == "" for n in stripped):
        errors.append("Conference names cannot be empty.")

    members: list[list[int]] = [[] for _ in stripped]
    unassigned = False
    for team_id in enrolled_team_ids:
        idx = team_to_conf_idx.get(team_id)
        if idx is None or not (0 <= idx < len(stripped)):
            unassigned = True
            continue
        members[idx].append(team_id)
    if unassigned:
        errors.append("Every team must be assigned to a conference.")
    if any(len(m) < min_per_conference for m in members):
        if min_per_conference > 2:
            errors.append(
                f"Each conference needs at least {min_per_conference} teams "
                "to hold its own playoff bracket."
            )
        else:
            errors.append("Each conference needs at least 2 teams.")

    if errors:
        return errors, None
    normalized = [(stripped[i], sorted(members[i])) for i in range(len(stripped))]
    return [], normalized


def _member_night_game_dict(match: Match) -> dict:
    """LG-07 — one read-only display row for a member-night game (a drawn-team
    Match stamped ``season_phase.phase_type == "member_night"``). The drawn-Team
    names encode the Site (``MN <site> G<n> A/B``). ``game_rounds`` must be
    prefetched by the caller. Used by both schedule surfaces."""
    rounds = sorted(match.game_rounds.all(), key=lambda r: r.round_number)
    return {
        "match_id": match.id,
        "team_a_name": match.team_red.name if match.team_red_id else "?",
        "team_b_name": match.team_blue.name if match.team_blue_id else "?",
        "is_completed": match.is_completed,
        "red_points": match.red_total_points,
        "blue_points": match.blue_total_points,
        "winner_name": match.winner.name if match.winner_id else None,
        "rounds": [{"id": r.id, "round_number": r.round_number} for r in rounds],
    }


def _member_night_games_for_season(season: Season) -> list[dict]:
    """LG-07 — the Season's member-night games grouped by ``member_night`` phase
    ordinal (a Season may hold more than one member-night phase). Read-only /
    derived; member-night Matches are ``season=<this>, season_phase=<mn phase>``
    and excluded from Standings, but surfaced here on the League schedule."""
    matches = (
        Match.objects.filter(season=season, season_phase__phase_type="member_night")
        .select_related("team_red", "team_blue", "season_phase", "winner")
        .prefetch_related("game_rounds")
        .order_by("season_phase__ordinal", "id")
    )
    by_ordinal: dict[int, dict] = {}
    for match in matches:
        ordinal = match.season_phase.ordinal
        section = by_ordinal.setdefault(ordinal, {"ordinal": ordinal, "games": []})
        section["games"].append(_member_night_game_dict(match))
    return [by_ordinal[o] for o in sorted(by_ordinal)]


def _member_night_appearances_for_team(season: Season, team: Team) -> list[dict]:
    """LG-07 — member-night games of ``season`` in which a Player of ``team``
    appeared (borrowed onto a drawn Team). Found via ``PlayerRoundState`` →
    the real Player's ``team`` FK; each game lists which of the Team's Players
    played. Read-only / derived (the Team-schedule surface)."""
    players_by_match: dict[int, list[str]] = {}
    prs = (
        PlayerRoundState.objects.filter(
            game_round__match__season=season,
            game_round__match__season_phase__phase_type="member_night",
            player__team=team,
        )
        .select_related("player", "game_round")
        .order_by("game_round__match_id")
    )
    for state in prs:
        match_id = state.game_round.match_id
        names = players_by_match.setdefault(match_id, [])
        if state.player.name not in names:
            names.append(state.player.name)
    if not players_by_match:
        return []
    matches = (
        Match.objects.filter(id__in=players_by_match)
        .select_related("team_red", "team_blue", "winner")
        .prefetch_related("game_rounds")
        .order_by("season_phase__ordinal", "id")
    )
    appearances: list[dict] = []
    for match in matches:
        game = _member_night_game_dict(match)
        game["players"] = players_by_match.get(match.id, [])
        appearances.append(game)
    return appearances


def _developing_players(league: League) -> "list[Player]":
    """LG-04 — the rolling League's snapshot Teams' players (active slots +
    bench) plus the ``league.free_agent_pool`` players. De-duplicated by pk.

    Snapshot Teams come from the just-completed Season's frozen
    ``starting_team_ids_json`` (the same source ``next_season`` uses for the
    team-id carry-forward). ``team.players.all()`` covers both active-slot and
    bench players (``Player.team`` membership includes both). Free agents come
    from ``league.free_agent_pool.players.all()`` when the pool is not None.

    The order is by id: Teams in ascending id, each Team's players in
    ascending id, then the free agents in ascending id. The shared develop RNG
    in ``_develop_league_for_new_season`` is drawn in this order, so it must
    not follow the backend's physical row order (Postgres heap order moves
    after UPDATEs; CI-01 #216).
    """
    latest_completed = league.seasons.filter(state="completed").order_by("-id").first()
    team_ids: list[int] = []
    if latest_completed is not None:
        team_ids = list(latest_completed.starting_team_ids_json or [])

    seen: set[int] = set()
    players: list[Player] = []

    for team in Team.objects.filter(id__in=team_ids).order_by("id"):
        for player in team.players.order_by("id"):
            if player.pk not in seen:
                seen.add(player.pk)
                players.append(player)

    if league.free_agent_pool is not None:
        for player in league.free_agent_pool.players.order_by("id"):
            if player.pk not in seen:
                seen.add(player.pk)
                players.append(player)

    return players


def _rank_teams_by_strength(teams: "Iterable[Team]") -> "list[Team]":
    """CRE-01 — teams sorted strongest → weakest.

    Mean active-roster ``overall_rating`` DESC, ``team_id`` ASC tiebreak — the
    single ranking source for both the FIN-03 budget seeding
    (``_seed_team_budgets_by_strength``) and the CRE-01 difficulty manager pick
    (``_pick_manager_team``).
    """
    return sorted(teams, key=lambda t: (-_compute_team_overall(t), t.id))


def _pick_manager_team(created_teams: "list[Team]", difficulty: str) -> Team:
    """CRE-01 — pick the manager's ``current_team`` by difficulty.

    Rank the teams by strength (``_rank_teams_by_strength``), then index:
    ``easy`` → 0 (strongest), ``medium`` → ``N // 2``, ``hard`` → ``N - 1``
    (weakest). A falsy/unknown difficulty defaults to ``"medium"``.
    """
    ranked = _rank_teams_by_strength(created_teams)
    n = len(ranked)
    idx = {"easy": 0, "medium": n // 2, "hard": n - 1}.get(
        difficulty or "medium", n // 2
    )
    return ranked[idx]


def _seed_team_budgets_by_strength(teams: "Iterable[Team]") -> None:
    """FIN-03 — seed every enrolled Team's three budget levels by roster strength.

    Ranks the enrolled Teams by mean active-roster ``overall_rating`` DESC
    (tie-break ``team_id`` ASC), then assigns a rank-linear budget level across
    ``[SEED_BUDGET_MIN, SEED_BUDGET_MAX]`` — rank 0 (strongest) → SEED_BUDGET_MAX,
    rank N-1 (weakest) → SEED_BUDGET_MIN, rounded to int. A single-Team League
    seeds at ``SEED_BUDGET_SINGLE``. The same level is set on all three of
    ``budget_scouting`` / ``budget_coaching`` / ``budget_facilities``. Seeds
    EVERY enrolled Team INCLUDING ``League.current_team``. Persisted in one
    ``bulk_update``. A Team with no active players ⇒ mean 0.0.
    """
    teams = list(teams)
    if not teams:
        return

    ranked = _rank_teams_by_strength(teams)
    n = len(ranked)
    for rank, team in enumerate(ranked):
        if n == 1:
            level = SEED_BUDGET_SINGLE
        else:
            level = round(
                SEED_BUDGET_MAX - (SEED_BUDGET_MAX - SEED_BUDGET_MIN) * rank / (n - 1)
            )
        team.budget_scouting = level
        team.budget_coaching = level
        team.budget_facilities = level

    Team.objects.bulk_update(
        teams, ["budget_scouting", "budget_coaching", "budget_facilities"]
    )


def _write_baseline_ratings(season: Season, players: "Iterable[Player]") -> None:
    """LG-04 — write an as-generated PlayerSeasonRating baseline row for each
    founding Player (current stats, current age, current overall_rating).
    No development. Bulk-created in one query.

    LG-05 — also computes each Player's ``potential`` (a scouting estimate at
    founding) via a fresh ``pot_rng`` (one gauss draw per player), sets it on
    the Player, writes it into the baseline rating row, and persists the
    Player.potential mutations in one ``bulk_update``.

    FIN-01 — when the League has ``finance_enabled``, also derives each
    Player's ``salary`` from ``overall_rating`` (cap-scaled) and appends
    ``"salary"`` to the bulk_update. When finance is OFF, ``salary`` stays
    ``None`` (byte-identical to the pre-FIN-01 baseline).
    """
    players = list(players)
    finance_on = season.league.finance_enabled
    pot_rng = random.Random()

    # FIN-03 — founding pass: NO completed Season exists, so the games-weighted
    # _scouting_budget_by_team smoothing has no input. Use each founding Team's
    # CURRENT scouting budget level instead (finance ON only; OFF ⇒ {} ⇒ every
    # .get(...) yields the LG-05 default ⇒ byte-identical to LG-05). Pool players
    # / finance OFF ⇒ the DEFAULT_SCOUTING_BUDGET default applies.
    band_map: dict[int, float] = {}
    if finance_on:
        founding_team_ids = {p.team_id for p in players if p.team_id is not None}
        band_map = {
            t.id: finance.scouting_budget(t.budget_scouting)
            for t in Team.objects.filter(id__in=founding_team_ids)
        }

    rows = []
    for p in players:
        pot = development.compute_potential(
            {name: getattr(p, name) for name in STAT_FIELDS},
            p.age if p.age is not None else 25,
            pot_rng,
            scouting_budget=band_map.get(
                p.team_id, development.DEFAULT_SCOUTING_BUDGET
            ),
        )
        p.potential = pot
        if finance_on:
            p.salary = finance.salary_for_overall(p.overall_rating)
        rows.append(
            PlayerSeasonRating(
                player=p,
                season=season,
                age=p.age,
                overall_rating=p.overall_rating,
                potential=pot,
                **{name: getattr(p, name) for name in STAT_FIELDS},
            )
        )
    PlayerSeasonRating.objects.bulk_create(rows)
    update_fields = ["potential"]
    if finance_on:
        update_fields.append("salary")
    Player.objects.bulk_update(players, update_fields)


def _coaching_effect_by_team(
    league: League, latest_completed: Season
) -> "dict[int, float]":
    """FIN-02 — per developing Team's develop-curve coaching effect.

    Returns ``{team_id: coaching_effect}``. A missing team_id (e.g. a
    free-agent-pool player) yields ``0.0`` at the ``.get(tid, 0.0)`` lookup,
    so the develop step is unaffected for those players.

    Gated on ``league.finance_enabled`` ONLY — finance OFF ⇒ ``{}`` ⇒ every
    lookup yields 0.0 ⇒ byte-identical to LG-04. For each developing Team
    (the just-completed Season's snapshot Teams) the coaching budget level is
    games-weighted over the last <=3 completed-Season ``TeamSeasonFinance``
    rows (``smoothed = sum(budget_coaching * games_played) / sum(games_played)``;
    no games / no rows ⇒ the team's current ``budget_coaching``), then mapped
    to an effect via ``finance.coaching_effect(level)``.
    """
    if not league.finance_enabled:
        return {}

    result: dict[int, float] = {}
    team_ids = list(latest_completed.starting_team_ids_json or [])
    teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}
    for tid in team_ids:
        team = teams_by_id.get(tid)
        if team is None:
            continue
        rows = list(
            TeamSeasonFinance.objects.filter(
                team_id=tid, season__state="completed"
            ).order_by("-season_id")[:3]
        )
        total = sum(row.budget_coaching * row.games_played for row in rows)
        weight = sum(row.games_played for row in rows)
        smoothed_level = total / weight if weight > 0 else team.budget_coaching
        result[tid] = finance.coaching_effect(smoothed_level)
    return result


def _scouting_budget_by_team(
    league: League, latest_completed: Season
) -> "dict[int, float]":
    """FIN-03 — per developing Team's potential scouting budget.

    Returns ``{team_id: scouting_budget}``. A missing team_id (e.g. a
    free-agent-pool player) yields ``development.DEFAULT_SCOUTING_BUDGET`` at
    the ``.get(tid, DEFAULT_SCOUTING_BUDGET)`` lookup, so the potential step is
    unaffected for those players.

    Gated on ``league.finance_enabled`` ONLY — finance OFF ⇒ ``{}`` ⇒ every
    lookup yields the LG-05 default ⇒ byte-identical to LG-05. For each
    developing Team (the just-completed Season's snapshot Teams) the scouting
    budget level is games-weighted over the last <=3 completed-Season
    ``TeamSeasonFinance`` rows
    (``smoothed = sum(budget_scouting * games_played) / sum(games_played)``;
    no games / no rows ⇒ the team's current ``budget_scouting``), then mapped
    to a scouting budget via ``finance.scouting_budget(level)``.
    """
    if not league.finance_enabled:
        return {}

    result: dict[int, float] = {}
    team_ids = list(latest_completed.starting_team_ids_json or [])
    teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}
    for tid in team_ids:
        team = teams_by_id.get(tid)
        if team is None:
            continue
        rows = list(
            TeamSeasonFinance.objects.filter(
                team_id=tid, season__state="completed"
            ).order_by("-season_id")[:3]
        )
        total = sum(row.budget_scouting * row.games_played for row in rows)
        weight = sum(row.games_played for row in rows)
        smoothed_level = total / weight if weight > 0 else team.budget_scouting
        result[tid] = finance.scouting_budget(smoothed_level)
    return result


def _develop_league_for_new_season(
    league: League, new_season: Season, latest_completed: Season
) -> None:
    """LG-04 — age + develop every Player in the rolling League's developing
    set, tick total_games, and write one PlayerSeasonRating row tagged to
    new_season. Called inside next_season's atomic block, after carry-forward.

    Builds a fresh ``random.Random()`` (no stored seed). League-isolated; NO
    cross-League guard.
    """
    rng = random.Random()
    # LG-05 — a SECOND, independent Random instance for the potential gauss
    # draw, so LG-04's pinned develop RNG sequence (1 gauss + 19 uniform per
    # player) stays byte-unchanged.
    pot_rng = random.Random()
    # FIN-01 — salary recompute is gated on the per-League toggle.
    finance_on = league.finance_enabled
    players = _developing_players(league)
    if not players:
        return

    # FIN-02 — per-Team develop-curve coaching effect ({} when finance OFF, so
    # every .get(tid, 0.0) yields 0.0 ⇒ byte-identical to LG-04).
    coaching_by_team = _coaching_effect_by_team(league, latest_completed)

    # FIN-03 — per-Team potential scouting budget ({} when finance OFF, so every
    # .get(tid, DEFAULT_SCOUTING_BUDGET) yields the LG-05 default ⇒ byte-identical
    # to LG-05).
    scouting_by_team = _scouting_budget_by_team(league, latest_completed)

    # Active-Team player-id set, derived from the already-loaded developing set:
    # a developing player is on an active Team iff its team_id is one of the
    # just-completed Season's snapshot Teams (free-agent-pool players carry the
    # pool team_id, which is never in the snapshot). Avoids a redundant second
    # pass over Team.players.
    active_team_id_set = set(latest_completed.starting_team_ids_json or [])
    active_pks: set[int] = {p.pk for p in players if p.team_id in active_team_id_set}

    # Regular-season appearance counts in the just-completed Season, scoped to
    # latest_completed. Playoff rounds carry match.season = NULL (Part2c-1 #3),
    # so they are naturally excluded — only regular-season appearances count.
    appearances = dict(
        PlayerRoundState.objects.filter(game_round__match__season=latest_completed)
        .values("player_id")
        .annotate(n=Count("id"))
        .values_list("player_id", "n")
    )

    # median_active: the median over the active-Team players of their season
    # appearance count. Degenerate no-active case => 0.
    active_counts = sorted(appearances.get(pk, 0) for pk in active_pks)
    median_active = 0
    if active_counts:
        mid = len(active_counts) // 2
        if len(active_counts) % 2 == 1:
            median_active = active_counts[mid]
        else:
            median_active = (active_counts[mid - 1] + active_counts[mid]) // 2

    rating_rows: list[PlayerSeasonRating] = []
    for player in players:
        # Age coalesce: None -> 25 for the develop math; live age written as +1.
        raw_age = player.age if player.age is not None else 25
        player.age = raw_age + 1

        new_stats = development.develop_player_stats(
            {name: getattr(player, name) for name in STAT_FIELDS},
            player.age,
            rng,
            coaching_effect=coaching_by_team.get(player.team_id, 0.0),
        )
        for name, val in new_stats.items():
            setattr(player, name, val)

        # total_games tick (cosmetic — never a develop input).
        if player.pk in active_pks:
            player.total_games += appearances.get(player.pk, 0)
        else:
            player.total_games += development.free_agent_games_tick(median_active, rng)

        # LG-05 — recompute potential on the POST-development stats + the
        # already-incremented age, with the independent pot_rng.
        pot = development.compute_potential(
            {name: getattr(player, name) for name in STAT_FIELDS},
            player.age,
            pot_rng,
            scouting_budget=scouting_by_team.get(
                player.team_id, development.DEFAULT_SCOUTING_BUDGET
            ),
        )
        player.potential = pot

        # FIN-01 — recompute salary from the POST-development overall (gated).
        if finance_on:
            player.salary = finance.salary_for_overall(player.overall_rating)

        # FIN-04 — availability resets every Season rollover (no carry-over).
        player.games_unavailable = 0

        overall = sum(new_stats.values()) / len(STAT_FIELDS)
        rating_rows.append(
            PlayerSeasonRating(
                player=player,
                season=new_season,
                age=player.age,
                overall_rating=overall,
                potential=pot,
                **new_stats,
            )
        )

    develop_fields = [
        *STAT_FIELDS,
        "age",
        "total_games",
        "potential",
        "games_unavailable",
    ]
    if finance_on:
        develop_fields.append("salary")
    Player.objects.bulk_update(players, develop_fields)
    PlayerSeasonRating.objects.bulk_create(rating_rows)


def template_to_form_data(
    template: LeagueTemplate, *, league_name: str, difficulty: str
) -> "dict[str, object]":
    """CRE-01 — merge a template row ∪ league_name ∪ difficulty ∪ static
    defaults into a ``dict`` ``CreateLeagueForm(data=...)`` accepts.

    Lets the chooser reuse the Advanced validation + phase-composer parse
    verbatim. ``map_pool`` is omitted (empty multi-select); ``schedule_format``
    is disabled (Django serves its initial regardless); the two boolean keys
    pass python ``True``/``False`` (``CheckboxInput.value_from_datadict``
    accepts bools).
    """
    return {
        "league_name": league_name,
        "manager_team_name": "",
        "difficulty": difficulty,
        # CRE-02 — the chooser has no spread selector; templates are always Even.
        "league_spread": "even",
        "season_name": "Season 1",
        "start_date": timezone.localdate().isoformat(),
        "num_teams": str(template.num_teams),
        "schedule_format": "single_round_robin",
        "visibility": "closed",
        "mean": str(template.mean),
        "std_dev": str(template.std_dev),
        "map_mode": template.map_mode,  # "none"
        "map_rotation": "",
        "phases": template.phases,
        "finance_enabled": template.finance_enabled,
        "challenge_fired_luxury_tax": template.challenge_fired_luxury_tax,
    }


@transaction.atomic
def create_league_and_season(
    form: CreateLeagueForm, *, manager: "AbstractBaseUser | None" = None
) -> Season:
    """CRE-01 — the single League + Season creation body.

    The SOLE writer, shared by the chooser POST (``league_create``) and the
    Advanced POST (``league_create_advanced``). Generates teams, creates the
    League + draft Season, picks + renames the manager's ``current_team`` by
    difficulty, seeds the free-agent pool, materialises ``map_pool``, runs the
    ``SeasonPhase`` create loop over ``cleaned_data["phase_specs"]``, writes
    baseline ratings, and (finance ON) seeds FIN budgets — preserving the
    pre-CRE-01 step order. Returns the created draft Season; callers redirect to
    ``season_standings``.
    """
    cleaned = form.cleaned_data
    rng = random.Random()
    team_names_pool = list(TEAM_NAMES)
    player_names_pool = list(PLAYER_NAMES)

    # CRE-02 — transient League spread -> per-team tier-mean vector. A falsy or
    # unknown value coerces to "even" (delta = 0), and delta = 0 passes
    # ``tier_means=None`` so Even generation is byte-identical to pre-CRE-02.
    spread = cleaned.get("league_spread") or "even"
    delta = LEAGUE_SPREAD_DELTAS.get(spread, 0.0)
    tier_means = (
        compute_tier_means(cleaned["num_teams"], cleaned["mean"], delta)
        if delta
        else None
    )

    created_teams = _generate_teams(
        cleaned["num_teams"],
        6,
        rng=rng,
        mean=cleaned["mean"],
        std_dev=cleaned["std_dev"],
        team_names_pool=team_names_pool,
        player_names_pool=player_names_pool,
        tier_means=tier_means,
        manager=manager,
    )

    league = League.objects.create(
        name=cleaned["league_name"],
        mode="league",
        state="active",
        # FIN-01 — per-League finance toggle picked at create time.
        finance_enabled=cleaned["finance_enabled"],
        # FIN-05 — luxury-tax challenge-mode firing toggle.
        challenge_fired_luxury_tax=cleaned["challenge_fired_luxury_tax"],
        # UX-01 — the **Manager**, and the dormant visibility marker.
        manager=manager,
        visibility=cleaned.get("visibility") or "closed",
    )
    # This League's dedicated free-agent pool Team. Hidden from
    # ``Team.objects.regular()`` via the ``free_agent_pool`` FK, so it
    # never appears in competitive team lists.
    pool_team = Team.objects.create(
        name=f"{cleaned['league_name']} Free Agents", manager=manager
    )
    league.free_agent_pool = pool_team
    # CRE-01: pick the manager's current_team by difficulty (easy → strongest,
    # medium → middle, hard → weakest), superseding the CAR-01 alphabetical
    # auto-pick. Difficulty selects WHICH of the N teams is the manager's; a
    # non-blank manager_team_name then renames that chosen team.
    manager_team = _pick_manager_team(
        created_teams, cleaned.get("difficulty") or "medium"
    )
    manager_name = (cleaned.get("manager_team_name") or "").strip()
    if manager_name:
        manager_team.name = manager_name
        manager_team.save(update_fields=["name"])
    league.current_team = manager_team
    league.save(update_fields=["current_team", "free_agent_pool"])

    # Seed a pool of 100–200 free agents (Players on no competitive
    # roster) into THIS League's pool so its Free Agents screen is
    # populated from the start.
    _generate_free_agents(
        rng.randint(100, 200),
        rng=rng,
        mean=cleaned["mean"],
        std_dev=cleaned["std_dev"],
        player_names_pool=player_names_pool,
        team=pool_team,
    )
    season = Season.objects.create(
        league=league,
        name=cleaned["season_name"],
        start_date=cleaned["start_date"],
        state="draft",
        schedule_format=cleaned["schedule_format"],
        # LG-01j — persist the picked map_mode at create-League time.
        map_mode=cleaned["map_mode"],
        # SUB-01 — persist the author-ordered rotation list (empty for
        # every mode except ``rotate_by_matchday``).
        map_rotation_ids_json=cleaned["map_rotation_ids"],
    )
    season.teams.add(*created_teams)
    # LG-01j — materialise the M2M map_pool rows in the same atomic
    # block. ``cleaned["map_pool"]`` is the ModelMultipleChoiceField's
    # QuerySet; ``.set()`` accepts an iterable of objects or PKs.
    season.map_pool.set(cleaned["map_pool"])
    # LG-02-Part2b — create the composed phases inside the same atomic
    # block (a rollback drops them with the Season). ``phase_specs`` is the
    # parsed composer output stashed by ``CreateLeagueForm.clean()``; the
    # ``tournament`` FK is ALWAYS NULL in Part2b.
    for spec in form.cleaned_data["phase_specs"]:
        SeasonPhase.objects.create(
            season=season,
            ordinal=spec.ordinal,
            phase_type=spec.phase_type,
            schedule_format=spec.schedule_format,
            tournament=None,
            # LG-02-Part2c-3c — the composer writes the tournament_mode
            # (standings / strength / unseeded) into the spec.
            tournament_mode=spec.tournament_mode,
            # LG-02-Part2c-3d — top-N participant cut (0 = no cut).
            tournament_cut=spec.tournament_cut,
            # LG-02-Part2c-3e — per-format sub-config (now-live tournament_format
            # plus the 7 sub-config fields).
            tournament_format=spec.tournament_format,
            final_series_length=spec.final_series_length,
            semifinal_series_length=spec.semifinal_series_length,
            quarterfinal_series_length=spec.quarterfinal_series_length,
            earlier_series_length=spec.earlier_series_length,
            wb_advancers=spec.wb_advancers,
            lb_advancers=spec.lb_advancers,
            swiss_rounds=spec.swiss_rounds,
        )

    # LG-04 — write an as-generated PlayerSeasonRating baseline row for every
    # founding Player (competitive-Team active + bench players plus the
    # free-agent pool). No development at baseline. At create time there is no
    # completed Season yet, so source the founding set directly from the just-
    # created Teams + the pool (the snapshot _developing_players relies on does
    # not exist until a Season completes).
    founding_players: list[Player] = []
    seen_founding: set[int] = set()
    for team in created_teams:
        for p in team.players.all():
            if p.pk not in seen_founding:
                seen_founding.add(p.pk)
                founding_players.append(p)
    for p in pool_team.players.all():
        if p.pk not in seen_founding:
            seen_founding.add(p.pk)
            founding_players.append(p)
    # FIN-03 — seed the enrolled Teams' budget levels by roster strength
    # (finance ON only) BEFORE the baseline ratings, so the founding-pass
    # band map reads the just-seeded scouting levels.
    if cleaned["finance_enabled"]:
        _seed_team_budgets_by_strength(created_teams)

    _write_baseline_ratings(season, founding_players)

    # CONF-05 — when the create form requested Conferences, pre-create them with
    # the generated Teams auto-split evenly (round-robin, so each gets >= 2 — the
    # form's ``2 * N`` team-count guard ensures this), then send the user straight
    # to the Manage Conferences composer to rename / reassign before Start Season.
    n_conf = cleaned.get("number_of_conferences") or 0
    if n_conf > 0:
        ordered_teams = sorted(created_teams, key=lambda t: t.name)
        for i in range(n_conf):
            conf = Conference.objects.create(
                season=season, name=f"Conference {i + 1}", ordinal=i + 1
            )
            conf.teams.set([t.id for t in ordered_teams[i::n_conf]])

    return season


def build_dashboard_context(
    displayed_season: Optional[Season], season_mode: str
) -> dict:
    """Shared body context for the League and Season dashboards.

    Returns the LG-01c body context dict (since extended by LG-01j /
    LG-02-Part2c / LG-01i / CAR-03 — the original 11 keys plus
    ``map_config_label``, the playoff-cursor keys, ``live_preview_available``,
    and ``is_career_mode``). Branches on ``season_mode`` to materialise the standings
    snippet, next-fixture dict, round count, leader snippets, and the
    placeholder action-button label / state.
    """
    standings_snippet: list = []
    next_fixture: Optional[dict] = None
    round_count_completed = 0
    round_count_total = 0
    leaders_points: list[LeaderRow] = []
    leaders_tags: list[LeaderRow] = []
    leaders_ratio: list[LeaderRow] = []

    if season_mode == "none":
        action_button_label = "No Season"
        action_button_state = "none"
    elif season_mode == "draft":
        action_button_label = "Start Season"
        action_button_state = "start_season"

        # Zero-filled top-3 standings preview: teams sorted by name asc.
        teams = sorted(displayed_season.teams.all(), key=lambda t: t.name)
        top_teams = teams[:3]
        for index, team in enumerate(top_teams):
            row = {
                "team_id": team.id,
                "matches_played": 0,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "league_points": 0,
                "round_wins": 0,
                "total_score": 0,
                "rank": index + 1,
            }
            standings_snippet.append((row, team))
    else:
        # season_mode in {"active", "completed"}.
        if season_mode == "active":
            action_button_label = "Play Next"
            action_button_state = "play_next"
        else:
            action_button_label = "Start Next Season"
            action_button_state = "start_next_season"

        # --- Standings snippet (real compute_standings, top 3) --------
        # CONF-01 — when the displayed Season has >= 2 Conferences, scope the
        # snippet to the MANAGER's Conference (the one containing
        # league.current_team; fall back to the first Conference, else empty).
        # 0/1 Conference is unchanged (overall top-3). Only the snippet's match
        # corpus + enrolled-teams set change.
        conf_count = displayed_season.conferences.count()
        scope_conf = None
        if conf_count >= 2:
            conf_map = displayed_season.conference_by_team_id()
            scope_conf = conf_map.get(displayed_season.league.current_team_id)
            if scope_conf is None:
                ordered_confs = displayed_season.ordered_conferences()
                scope_conf = ordered_confs[0] if ordered_confs else None

        if conf_count >= 2 and scope_conf is None:
            # >= 2 Conferences but none resolvable ⇒ empty snippet (locked).
            standings_snippet = []
        else:
            # LG-07a — exclude social member-night Matches from the query.
            completed_qs = Match.objects.filter(
                season=displayed_season, is_completed=True
            ).exclude(season_phase__phase_type="member_night")
            if scope_conf is not None:
                completed_qs = completed_qs.filter(conference=scope_conf)
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

            if scope_conf is not None:
                if scope_conf.starting_team_ids_json is not None:
                    team_ids = list(scope_conf.starting_team_ids_json)
                else:
                    team_ids = sorted(t.id for t in scope_conf.teams.all())
            elif displayed_season.starting_team_ids_json is not None:
                team_ids = list(displayed_season.starting_team_ids_json)
            else:
                team_ids = sorted(t.id for t in displayed_season.teams.all())

            enrolled_teams = list(
                Team.objects.filter(id__in=team_ids).values_list("id", "name")
            )
            rows = compute_standings(completed_matches, enrolled_teams)
            teams_by_id = Team.objects.in_bulk(team_ids)
            top_rows = rows[:3]
            snippet_rows: list = []
            for row in top_rows:
                row_dict = {
                    "team_id": row.team_id,
                    "matches_played": row.matches_played,
                    "wins": row.wins,
                    "losses": row.losses,
                    "ties": row.ties,
                    "league_points": row.league_points,
                    "round_wins": row.round_wins,
                    "total_score": row.total_score,
                    "rank": row.rank,
                }
                snippet_rows.append((row_dict, teams_by_id.get(row.team_id)))
            standings_snippet = snippet_rows

        # --- Schedule + next fixture + round progress -----------------
        # LG-02-Part2a — route through the Season chokepoint (returns []
        # for < 2 teams, matching the prior guard).
        fixtures = displayed_season.scheduled_fixtures()

        rounds_qs = GameRound.objects.filter(
            match__season=displayed_season
        ).select_related("match")
        # LG-02-Part2c-3a — played_keys gain ``leg`` so a double_round_robin
        # phase's two legs are distinct.
        played_keys: set = set()
        for game_round in rounds_qs:
            match = game_round.match
            if match is None or match.team_red_id is None or match.team_blue_id is None:
                continue
            played_keys.add(
                (
                    frozenset({match.team_red_id, match.team_blue_id}),
                    game_round.round_number,
                    match.leg,
                )
            )

        round_count_completed, round_count_total = round_progress(fixtures, played_keys)

        fixture = find_next_fixture(fixtures, played_keys)
        if fixture is not None:
            fixture_teams = Team.objects.in_bulk([fixture.team_a_id, fixture.team_b_id])
            team_a = fixture_teams.get(fixture.team_a_id)
            team_b = fixture_teams.get(fixture.team_b_id)
            fixture_date = displayed_season.start_date + timedelta(
                days=(fixture.matchday - 1) * 7
            )
            next_fixture = {
                "matchday": fixture.matchday,
                "round_number": fixture.round_number,
                "team_a_id": fixture.team_a_id,
                "team_a_name": team_a.name if team_a is not None else "",
                "team_b_id": fixture.team_b_id,
                "team_b_name": team_b.name if team_b is not None else "",
                "date": fixture_date,
            }

        # --- Leaders snippets ----------------------------------------
        # UXP-09: a completed Season's snippets come from its summary snapshot.
        leaders_season = displayed_season
        assert leaders_season is not None  # active / completed modes have one
        leaders = completed_season_cached(
            leaders_season,
            "dashboard_leaders",
            lambda: _compute_dashboard_leaders(leaders_season),
            dump=leader_boards_to_json,
            load=leader_boards_from_json,
        )
        leaders_points = leaders["points"]
        leaders_tags = leaders["tags"]
        leaders_ratio = leaders["ratio"]

    # LG-01j — read-only map-config label for the dashboard "Map: ..."
    # line. 4 cases in pinned precedence order. Active / completed
    # Seasons read from the FROZEN SNAPSHOT; draft Seasons read the
    # live M2M (the snapshot is None pre-activation).
    map_config_label = _build_map_config_label(displayed_season, season_mode)

    # LG-02-Part2c-1 — playoff-cursor keys. NAV-01 factored the 9 play keys
    # (action button + playoff group + One-Week-Live gate + career flag) OUT
    # of this body into ``league_play_services.build_play_controls`` (the
    # topnav ``Play ▾`` dropdown source). ``build_dashboard_context`` KEEPS ``playoff_tournament_id``
    # ONLY — the read-only "View bracket" dashboard link still needs it (§4).
    (
        _playoff_phase_active,
        playoff_tournament_id,
        _playoff_completed,
        _has_following_tournament_phase,
        _following_tournament_is_final,
    ) = _playoff_cursor_keys(displayed_season)

    return {
        "displayed_season": displayed_season,
        "season_mode": season_mode,
        "standings_snippet": standings_snippet,
        "next_fixture": next_fixture,
        "round_count_completed": round_count_completed,
        "round_count_total": round_count_total,
        "leaders_points": leaders_points,
        "leaders_tags": leaders_tags,
        "leaders_ratio": leaders_ratio,
        # LG-01j — read-only map-config label.
        "map_config_label": map_config_label,
        # NAV-01 — KEPT for the read-only dashboard "View bracket" link (§4);
        # the playoff *play* forms moved to the topnav ``Play ▾`` dropdown.
        "playoff_tournament_id": playoff_tournament_id,
    }


def _compute_dashboard_leaders(displayed_season: Season) -> dict[str, list[LeaderRow]]:
    """The dashboard's three top-3 leader snippets over the Season's corpus,
    keyed ``points`` / ``tags`` / ``ratio``."""
    prs_qs = (
        PlayerRoundState.objects.filter(game_round__match__season=displayed_season)
        .select_related(
            "player",
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

    return {
        "points": compute_leaders(player_rounds, "points_per_game", limit=3),
        "tags": compute_leaders(player_rounds, "tags_per_game", limit=3),
        "ratio": compute_leaders(player_rounds, "tag_ratio", limit=3),
    }


def _playoff_cursor_keys(
    displayed_season: Optional[Season],
) -> tuple[bool, Optional[int], bool, bool, bool]:
    """LG-02-Part2c-1 — derive the dashboard playoff-cursor keys.

    Returns ``(playoff_phase_active, playoff_tournament_id,
    playoff_completed, has_following_tournament_phase,
    following_tournament_is_final)``:

    * ``playoff_phase_active`` — ``current_phase()`` is a built + active
      tournament phase (``tournament_id is not None`` AND
      ``tournament.state == "active"``).
    * ``playoff_tournament_id`` — the tournament id of a built tournament
      phase (active OR completed), else ``None``. When ``current_phase()``
      is ``None`` (Season finished), inspect the final phase.
    * ``playoff_completed`` — a built tournament phase exists with
      ``tournament.state == "completed"``.
    * ``has_following_tournament_phase`` — the phase list contains a
      ``tournament`` phase at an ordinal AFTER the current phase.
    * ``following_tournament_is_final`` — LG-02-Part2c-3c terminal-label
      split: the next tournament phase (at an ordinal > the current phase's)
      ends the Season — CONF-04 generalises this from "its ordinal == the last
      phase's ordinal" to "nothing but tournament phases follows it", so the
      derived Worlds phase does not demote a season-ending playoff (ADR-0037).
      Drives the "Until Playoffs" (final) vs "Until Tournament" (mid-season)
      relabel.
    """
    if displayed_season is None:
        return (False, None, False, False, False)

    phases = displayed_season.ordered_phases()
    current = displayed_season.current_phase()

    playoff_phase_active = False
    playoff_tournament_id: Optional[int] = None
    playoff_completed = False
    has_following_tournament_phase = False
    following_tournament_is_final = False

    if current is not None:
        following_tournament_ordinals = [
            phase.ordinal
            for phase in phases
            if phase.phase_type == "tournament" and phase.ordinal > current.ordinal
        ]
        has_following_tournament_phase = bool(following_tournament_ordinals)
        if has_following_tournament_phase:
            # CONF-04 — generalised from "the next tournament phase IS the last
            # phase" to "nothing but tournament phases follows it" (ADR-0037).
            # Appending the Worlds phase after the Regional-playoff phase would
            # otherwise flip a season-ending playoff back to the mid-season
            # "Until Tournament" label. Reproduces today's result on both
            # pre-CONF-04 shapes: RR(1)->t(2) is vacuously True (no phase has a
            # higher ordinal), and RR(1)->t(2)->RR(3)->t(4) is False (phase 3 is
            # round_robin).
            next_tournament_ordinal = min(following_tournament_ordinals)
            following_tournament_is_final = all(
                p.phase_type == "tournament"
                for p in phases
                if p.ordinal > next_tournament_ordinal
            )
        if current.phase_type == "tournament":
            # CONF-02 — read through the ``tournaments_for_phase`` seam so a
            # >= 2-Conference regional phase (whose ``tournament_id`` is NULL —
            # its N brackets hang off ``phase.regional_tournaments``) drives the
            # same controls. For a 0/1-Conference Season the list is exactly
            # ``[phase.tournament]``, so every value below is byte-identical.
            tournaments = displayed_season.tournaments_for_phase(current)
            if tournaments:
                playoff_tournament_id = tournaments[0].id
                if any(t.state == "active" for t in tournaments):
                    playoff_phase_active = True
                elif all(t.state == "completed" for t in tournaments):
                    playoff_completed = True
    else:
        # Season finished — inspect the final phase for a completed
        # tournament (the tournament-completed sub-state).
        final_phase = phases[-1]
        if final_phase.phase_type == "tournament":
            # CONF-02 — same seam as the active-cursor branch above.
            tournaments = displayed_season.tournaments_for_phase(final_phase)
            if tournaments:
                playoff_tournament_id = tournaments[0].id
                if all(t.state == "completed" for t in tournaments):
                    playoff_completed = True

    return (
        playoff_phase_active,
        playoff_tournament_id,
        playoff_completed,
        has_following_tournament_phase,
        following_tournament_is_final,
    )


def _build_map_config_label(
    displayed_season: Optional[Season], season_mode: str
) -> str:
    """LG-01j — render the per-Season Map: <...> dashboard label.

    Locked precedence (the 4-case ladder in pinned order):

        1. ``displayed_season is None`` OR ``season_mode == "none"``
           (LG-01c ``season_mode`` — distinct from ``Season.map_mode``
           — the "no Season picked" case) ⇒
           ``"Map: 3-zone fallback (no map)"``.
        2. ``displayed_season.map_mode == "none"`` ⇒
           ``"Map: 3-zone fallback (no map)"``.
        3. ``displayed_season.map_mode == "single"`` ⇒ resolve the lone
           map and render ``f"Map: Single — {name}"`` (em-dash U+2014)
           or ``"Map: Single — (map deleted)"`` when the map row was
           deleted between activation and render.
        4. ``displayed_season.map_mode == "random_per_round"`` ⇒
           ``"Map: Random per Round ({n} maps: {names})"`` (names
           alphabetical asc) or ``"Map: Random per Round (no maps)"``
           when the pool is empty / all entries deleted.

    For active / completed Seasons the pool ids come from the FROZEN
    SNAPSHOT (``starting_map_pool_ids_json``); for draft Seasons the
    snapshot is ``None`` so the live M2M is read instead.
    """
    if displayed_season is None or season_mode == "none":
        return "Map: 3-zone fallback (no map)"

    from core.models import ArenaMap

    mode = displayed_season.map_mode
    if mode == "none":
        return "Map: 3-zone fallback (no map)"

    # Resolve pool ids — snapshot for active/completed, live M2M for draft.
    if season_mode in ("active", "completed"):
        pool_ids = displayed_season.starting_map_pool_ids_json or []
    else:
        pool_ids = list(displayed_season.map_pool.values_list("id", flat=True))

    if mode == "single":
        if not pool_ids:
            return "Map: Single — (map deleted)"
        map_obj = ArenaMap.objects.filter(id=pool_ids[0]).first()
        if map_obj is None:
            return "Map: Single — (map deleted)"
        return f"Map: Single — {map_obj.name}"

    if mode == "random_per_round":
        names = list(
            ArenaMap.objects.filter(id__in=pool_ids)
            .order_by("name")
            .values_list("name", flat=True)
        )
        if not names:
            return "Map: Random per Round (no maps)"
        return f"Map: Random per Round ({len(names)} maps: {', '.join(names)})"

    if mode == "rotate_by_matchday":
        # SUB-01 — the rotation reads its OWN snapshot (active/completed) or
        # live list (draft), in AUTHOR order (NOT alphabetical).
        if season_mode in ("active", "completed"):
            ids = displayed_season.starting_map_rotation_ids_json or []
        else:
            ids = displayed_season.map_rotation_ids_json or []
        names_by_id = dict(
            ArenaMap.objects.filter(id__in=ids).values_list("id", "name")
        )
        names = [names_by_id[i] for i in ids if i in names_by_id]
        if not names:
            return "Map: Rotating (no maps)"
        return f"Map: Rotating ({len(names)} maps: {', '.join(names)})"

    if mode == "rotate_by_conference":
        # CONF-06 - one author-ordered rotation PER Conference. Same
        # snapshot-vs-live rule as ``rotate_by_matchday``, applied to each
        # Conference's own list; author order is preserved (NOT alphabetical).
        confs = list(displayed_season.conferences.all().order_by("ordinal"))
        ids_by_conf = {
            conf.id: (
                (conf.starting_map_rotation_ids_json or [])
                if season_mode in ("active", "completed")
                else (conf.map_rotation_ids_json or [])
            )
            for conf in confs
        }
        # ONE query for every Conference's rotation ids (not one per Conference).
        names_by_id = dict(
            ArenaMap.objects.filter(
                id__in={i for ids in ids_by_conf.values() for i in ids}
            ).values_list("id", "name")
        )
        parts: list[str] = []
        for conf in confs:
            names = [names_by_id[i] for i in ids_by_conf[conf.id] if i in names_by_id]
            if names:
                parts.append(f"{conf.name}: {', '.join(names)}")
        if not parts:
            return "Map: Rotating per Conference (no maps)"
        return (
            f"Map: Rotating per Conference "
            f"({len(parts)} conferences: {'; '.join(parts)})"
        )

    # Defensive fallback — an unknown enum value (admin-side raw write)
    # surfaces as the 3-zone label rather than crashing the dashboard.
    return "Map: 3-zone fallback (no map)"


def pick_displayed_season(league: League) -> Season | None:
    """LG-01f — the Season the sidebar's live LEAGUE entries target.

    Active Season takes precedence; fallback to the most-recent completed
    Season; ``None`` when the League has zero Seasons. Single-sourced so
    the 5 League-context views and the league-history view agree on
    which Season the sidebar's Standings / Schedule links resolve to.
    """
    active = league.active_season
    if active is not None:
        return active
    return league.seasons.filter(state="completed").order_by("-id").first()


# The tournament formats whose champion is crowned through a terminal Bracket
# node — only these source a Finals MVP. ``round_robin`` / ``swiss`` have no
# single deciding Match, so they (and no-playoff Seasons) yield ``None``.
_BRACKET_FINALS_FORMATS: frozenset[str] = frozenset(
    {"single_elimination", "double_elimination", "round_robin_double_elim"}
)


def _award_round_dict(prs: PlayerRoundState) -> dict:
    """Build one LG-03 seam dict from a ``PlayerRoundState`` row.

    Mirrors ``league_screens.player_stats._build_round_dicts`` team-resolution:
    the team resolves from the Round's ``team_red`` / ``team_blue`` keyed on
    the player's ``team_color``. ``accuracy`` / ``mvp`` are pre-computed here
    from the ``get_accuracy`` / ``get_mvp`` PROPERTIES (no parens) so the pure
    module never touches the MVP formula or the ORM.
    """
    game_round = prs.game_round
    if prs.team_color == "red":
        team = game_round.team_red
    elif prs.team_color == "blue":
        team = game_round.team_blue
    else:
        team = None
    return {
        "player_id": prs.player_id,
        "player_name": prs.player.name,
        "role": prs.role,
        "team_id": team.id if team is not None else 0,
        "team_name": team.name if team is not None else "",
        "points_scored": prs.points_scored,
        "tags_made": prs.tags_made,
        "times_tagged": prs.times_tagged,
        "accuracy": float(prs.get_accuracy),
        "mvp": float(prs.get_mvp),
        "resupplies_given": prs.resupplies_given,
        "specials_used": prs.specials_used,
        "own_specials_cancelled": prs.own_specials_cancelled,
    }


def _season_regular_round_dicts(season: Season) -> list[dict]:
    """Build the flat regular-season seam dicts for a Season, id-ascending.

    Regular-season corpus (LOCKED ORM): every ``PlayerRoundState`` reachable
    via ``game_round__match__season=season`` — playoff Matches carry
    ``season=NULL`` (Part2c-1 #3) so they are naturally excluded. Rows are
    ordered ``id`` ascending so the pure module's "last row wins" identity
    resolution is deterministic.
    """
    prs_qs = (
        PlayerRoundState.objects.filter(game_round__match__season=season)
        .select_related(
            "player",
            "game_round",
            "game_round__team_red",
            "game_round__team_blue",
        )
        .order_by("id")
    )
    return [_award_round_dict(prs) for prs in prs_qs]


def _finals_deciding_node(tournament) -> Optional["object"]:
    """Resolve the terminal Bracket node won by the Tournament champion.

    - ``single_elimination`` / ``round_robin_double_elim`` ⇒ the node with
      ``advances_to_id is None`` whose ``winner_id == tournament.champion_id``.
    - ``double_elimination`` ⇒ the grand-final node GF2
      (``bracket_type == "grand_final"``, ``advances_to_id is None``), or GF1
      when GF2 is a bye (the Bracket reset was skipped).

    Returns ``None`` when no node matches (defensive).
    """
    fmt = tournament.format
    if fmt == "double_elimination":
        gf_nodes = [
            n
            for n in tournament.nodes.all()
            if n.bracket_type == "grand_final" and n.advances_to_id is None
        ]
        # GF2 is the terminal grand-final node; fall back to GF1 when GF2 is a
        # bye / inert (the reset was skipped).
        for node in gf_nodes:
            if not node.is_bye and node.winner_id == tournament.champion_id:
                return node
        # GF2 inert — pick the grand-final node won by the champion (GF1).
        for node in tournament.nodes.all():
            if (
                node.bracket_type == "grand_final"
                and node.winner_id == tournament.champion_id
            ):
                return node
        return None

    # single_elimination / round_robin_double_elim.
    for node in tournament.nodes.all():
        if node.advances_to_id is None and node.winner_id == tournament.champion_id:
            return node
    return None


def _season_finals_mvp(season: Season) -> Optional[AwardWinner]:
    """Compute a Season's Finals MVP, or ``None``.

    Set only when the Season has a tournament/playoff phase whose Tournament
    has a BRACKET format (:data:`_BRACKET_FINALS_FORMATS`). Navigates
    ``ordered_phases()`` → the ``tournament`` phase with a built Tournament →
    the deciding ``BracketNode`` → ALL ``GameRound``s of ALL its
    ``SeriesMatch`` rows → ``pick_finals_mvp`` over the per-round dicts.
    """
    phase = None
    for p in season.ordered_phases():
        if p.phase_type == "tournament" and p.tournament_id is not None:
            phase = p
            break
    if phase is None:
        return None

    tournament = phase.tournament
    if tournament is None or tournament.format not in _BRACKET_FINALS_FORMATS:
        return None
    if tournament.champion_id is None:
        return None

    node = _finals_deciding_node(tournament)
    if node is None:
        return None

    final_round_dicts: list[dict] = []
    for series_match in node.series_matches.all():
        match = series_match.match
        if match is None:
            continue
        prs_qs = (
            PlayerRoundState.objects.filter(game_round__match=match)
            .select_related(
                "player",
                "game_round",
                "game_round__team_red",
                "game_round__team_blue",
            )
            .order_by("id")
        )
        final_round_dicts.extend(_award_round_dict(prs) for prs in prs_qs)

    return pick_finals_mvp(final_round_dicts)


def compute_season_award_set(season: Season) -> AwardSet:
    """Build a Season's full :class:`AwardSet` (regular-season + finals MVP).

    The single shared path used by ``season_awards`` (the view),
    ``build_history_row`` (League History), and the player-page awards badge.
    UXP-09: a completed Season's set is stored in its summary snapshot
    (``matches.season_snapshot``) after the first computation.
    """
    return completed_season_cached(
        season,
        "awards",
        lambda: _compute_season_award_set(season),
        dump=award_set_to_json,
        load=award_set_from_json,
    )


def _compute_season_award_set(season: Season) -> AwardSet:
    """Compute the :class:`AwardSet` from the Season's corpus.

    Builds the regular-season seam dicts, derives ``min_games``, calls
    ``compute_season_awards``, then stamps the separately-computed Finals MVP.
    """
    round_dicts = _season_regular_round_dicts(season)

    games_by_player: dict[int, int] = defaultdict(int)
    for row in round_dicts:
        games_by_player[row["player_id"]] += 1
    max_games = max(games_by_player.values(), default=0)
    min_games = ceil(max_games / 2)

    awards = compute_season_awards(round_dicts, min_games=min_games)
    finals_mvp = _season_finals_mvp(season)
    return replace(awards, finals_mvp=finals_mvp)


# Human labels for the player-page awards badge.
_KD_ROLE_LABELS: dict[str, str] = {
    "commander": "K/D — Commander",
    "heavy": "K/D — Heavy",
    "scout": "K/D — Scout",
    "medic": "K/D — Medic",
    "ammo": "K/D — Ammo",
}


def _player_award_labels(awards: AwardSet, player_id: int) -> list[str]:
    """Human labels of the awards ``player_id`` won in this ``AwardSet``."""
    labels: list[str] = []

    def won(winner: Optional[AwardWinner]) -> bool:
        return winner is not None and winner.player_id == player_id

    if won(awards.most_points):
        labels.append("Most Points")
    if won(awards.best_accuracy):
        labels.append("Best Accuracy")
    for role in ROLE_KEYS:
        if won(awards.kd_by_role.get(role)):
            labels.append(_KD_ROLE_LABELS[role])
    if won(awards.best_medic):
        labels.append("Best Medic")
    if won(awards.most_efficient_nuke):
        labels.append("Most Efficient Nuke")
    if won(awards.season_mvp):
        labels.append("Season MVP")
    if won(awards.finals_mvp):
        labels.append("Finals MVP")
    return labels


def build_history_row(
    season: Season,
    teams_by_id: dict[int, Team],
    *,
    is_in_progress: bool,
) -> dict:
    """LG-01f / LG-03 — build one row of the League History table.

    Returns a dict with 13 keys (the LG-01f 11 plus LG-03's
    ``season_mvp`` / ``finals_mvp``). ``None`` values render as ``"—"``
    in the template. The standings columns consume the pre-fetched
    ``season.matches.all()`` cache and the ``teams_by_id`` lookup with
    zero queries; the LG-03 award cells issue **one** per-Season
    ``PlayerRoundState`` query via ``compute_season_award_set`` —
    acceptable on the paginated (10-row) History page.
    """
    matches_list_in: list[dict] = []
    for match in season.matches.all():
        if not match.is_completed:
            continue
        # LG-07a — skip social member-night Matches in-Python so the
        # ``season.matches`` prefetch cache is preserved (a queryset .exclude
        # here would consume the prefetch).
        if (
            match.season_phase_id is not None
            and match.season_phase.phase_type == "member_night"
        ):
            continue
        matches_list_in.append(
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
    matches_played = len(matches_list_in)

    if season.starting_team_ids_json is not None:
        team_ids_for_season = list(season.starting_team_ids_json)
        teams_enrolled = len(team_ids_for_season)
    else:
        team_ids_for_season = sorted(t.id for t in season.teams.all())
        teams_enrolled = len(team_ids_for_season)

    enrolled_teams: list[tuple[int, str]] = []
    for tid in team_ids_for_season:
        team_obj = teams_by_id.get(tid)
        enrolled_teams.append((tid, team_obj.name if team_obj is not None else ""))

    standings = compute_standings(matches_list_in, enrolled_teams)
    # L-1 — a >= 2-Conference Season plays disjoint schedules, so whole-Season
    # placings compare nothing (CONF-01); only a stamped (Worlds) champion shows.
    if len(season.conferences.all()) >= 2:
        standings = []

    top_three: list = [
        teams_by_id.get(standings[i].team_id) if i < len(standings) else None
        for i in range(3)
    ]

    if is_in_progress:
        champion: Team | None = None
    else:
        champion = season.champion_team
        if champion is None and standings:
            champion = teams_by_id.get(standings[0].team_id)

    if len(standings) >= 2:
        runner_up = teams_by_id.get(standings[1].team_id)
    else:
        runner_up = None

    # LG-03 — Season MVP + Finals MVP via the shared award path (one per-season
    # PlayerRoundState query — acceptable on the paginated 10-row History page).
    award_set = compute_season_award_set(season)

    return {
        "season_id": season.id,
        "season_name": season.name,
        "season_url": reverse("season_dashboard", args=[season.id]),
        "start_date": season.start_date,
        "teams_enrolled": teams_enrolled,
        "matches_played": matches_played,
        "champion": champion,
        "runner_up": runner_up,
        "tournament_champion": None,
        "top_three": top_three,
        "is_in_progress": is_in_progress,
        "season_mvp": award_set.season_mvp,
        "finals_mvp": award_set.finals_mvp,
    }


def _render_fixture_sides(fixture, teams_by_id: dict[int, Team]):
    """LG-01g — resolve a fixture's per-Round Side assignment.

    Round 1 ⇒ ``(teams_by_id[team_a_id], teams_by_id[team_b_id])``.
    Round 2 ⇒ ``(teams_by_id[team_b_id], teams_by_id[team_a_id])`` —
    the per-Match colour swap simulated round 2 persists.

    Raises ``KeyError`` if a team id is missing from ``teams_by_id`` —
    a real bug, never swallowed (the lookup is built from the same
    ``starting_team_ids_json`` set that produced the fixtures).
    """
    if fixture.round_number == 1:
        return (teams_by_id[fixture.team_a_id], teams_by_id[fixture.team_b_id])
    return (teams_by_id[fixture.team_b_id], teams_by_id[fixture.team_a_id])


def _build_team_schedule_rows(
    displayed_season: Season,
    team: Team,
    fixtures: list,
    played_game_rounds: Iterable[GameRound],
    teams_by_id: dict[int, Team],
) -> dict[str, list[dict]]:
    """LG-01g — walk fixtures + played GameRounds, partition into
    Upcoming / Completed rows from the picked Team's perspective.

    Algorithm (§4c, pinned):
        1. ``played_keys`` keyed Side-agnostically on
           ``(frozenset({team_red_id, team_blue_id}), round_number)``.
        2. ``fixture_by_key`` over the full ``fixtures`` list for
           Completed-row matchday recovery.
        3. Filter fixtures to ones involving ``team``.
        4. Per filtered fixture: skip if played, else build the
           7-key Upcoming row via ``_render_fixture_sides``.
        5. Per played GameRound: build the 11-key Completed row from
           the persisted ``match.team_red`` / ``team_blue`` (NOT
           recomputed — the GameRound records actual physical Sides).
        6. Sort Upcoming by ``(matchday, round_number)`` asc; Completed
           keeps queryset order (id asc = chronological).
    """
    # LG-02-Part2c-3a — every key gains ``leg`` so a double_round_robin phase's
    # two legs are distinct (``gr.match.leg`` / ``fixture.leg``).
    played_game_rounds = list(played_game_rounds)
    played_keys: set[tuple[frozenset[int], int, int]] = set()
    for gr in played_game_rounds:
        match = gr.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        played_keys.add(
            (
                frozenset({match.team_red_id, match.team_blue_id}),
                gr.round_number,
                match.leg,
            )
        )

    fixture_by_key: dict[tuple[frozenset[int], int, int], object] = {}
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        fixture_by_key[key] = fixture

    upcoming: list[dict] = []
    for fixture in fixtures:
        if team.id not in {fixture.team_a_id, fixture.team_b_id}:
            continue
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        if key in played_keys:
            continue
        red_team, blue_team = _render_fixture_sides(fixture, teams_by_id)
        fixture_date = displayed_season.start_date + timedelta(
            days=(fixture.matchday - 1) * 7
        )
        upcoming.append(
            {
                "matchday": fixture.matchday,
                "round_number": fixture.round_number,
                "date": fixture_date,
                "red_team_id": red_team.id,
                "red_team_name": red_team.name,
                "blue_team_id": blue_team.id,
                "blue_team_name": blue_team.name,
            }
        )

    upcoming.sort(key=lambda r: (r["matchday"], r["round_number"]))

    completed: list[dict] = []
    for gr in played_game_rounds:
        match = gr.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            # Defensive — a GameRound with no persisted Match cannot
            # render the per-Side breakdown; skip silently.
            continue
        key = (
            frozenset({match.team_red_id, match.team_blue_id}),
            gr.round_number,
            match.leg,
        )
        fixture = fixture_by_key.get(key)
        matchday = fixture.matchday if fixture is not None else 0
        fixture_date = displayed_season.start_date + timedelta(days=(matchday - 1) * 7)
        if gr.round_number == 1:
            red_score = match.red_round1_points
            blue_score = match.blue_round1_points
        else:
            red_score = match.red_round2_points
            blue_score = match.blue_round2_points

        picked_is_red = team.id == match.team_red_id
        picked_per_round = red_score if picked_is_red else blue_score
        other_per_round = blue_score if picked_is_red else red_score
        if picked_per_round > other_per_round:
            outcome = "W"
        elif picked_per_round < other_per_round:
            outcome = "L"
        else:
            outcome = "T"

        completed.append(
            {
                "matchday": matchday,
                "round_number": gr.round_number,
                "date": fixture_date,
                "red_team_id": match.team_red_id,
                "red_team_name": match.team_red.name,
                "blue_team_id": match.team_blue_id,
                "blue_team_name": match.team_blue.name,
                "game_round_id": gr.id,
                "red_score": red_score,
                "blue_score": blue_score,
                "outcome": outcome,
            }
        )

    return {"upcoming": upcoming, "completed": completed}


def _classify_playoffs_for_team(season: Season, team_id: int) -> tuple[str, int, int]:
    """Classify ``team_id``'s playoff result in ``season`` into the flat triple.

    Returns ``(playoff_result, rounds_won, num_rounds)`` for
    ``owner_mood.compute_playoffs_delta``. Reads off the Season's embedded
    ``tournament`` phase (Part2c-1) — the ``SeasonPhase`` whose
    ``phase_type == "tournament"`` and ``tournament_id is not None``:

    - no such built phase ⇒ ``("none", 0, 0)``;
    - ``tournament.champion_id == team_id`` ⇒ ``("champion", 0, num_rounds)``;
    - in the bracket (a participant) but not champion ⇒
      ``("seeded", rounds_won, num_rounds)`` where ``rounds_won`` is the count of
      distinct ``bracket_round``s in which the team won a node
      (``BracketNode.winner_id == team_id``);
    - a participant cut / never in the bracket ⇒ ``("missed", 0, num_rounds)``.

    CONF-04 — a Season with **two or more** Conferences classifies TWO-TIER
    instead (ADR-0037): ``num_rounds`` is the Team's whole possible path — the
    rounds of its own Conference's Regional playoff PLUS the rounds of the
    Worlds bracket — and ``rounds_won`` counts the distinct bracket rounds it
    won across both. ``matches/owner_mood.py`` is NOT touched: its ``"seeded"``
    branch is already depth-proportional, so feeding it the longer path yields
    the intended ladder (Worlds champion > Worlds finalist > Conference champion
    > regional finalist > first-round exit) with no new result values and no new
    constants. The 0/1-Conference branch below is verbatim, and is deliberately
    NOT routed through ``_worlds_phase()`` — a flat Season never grows one, so
    its ``tournament_id is not None`` scan can never pick one up.
    """
    if len(season.ordered_conferences()) >= 2:
        conference = season.conference_by_team_id().get(team_id)
        if conference is None:
            # A Team in NO Conference within a partitioned Season is broken
            # data. Returning "none" (the neutral 0.0 delta) rather than
            # "missed" (the -0.2 penalty) is a pinned defensive choice: broken
            # data must not fire a Manager.
            return ("none", 0, 0)
        # Post-narrowing this is the REGIONAL-playoff phase, never the Worlds
        # phase (ADR-0037).
        regional_phase = season._final_tournament_phase()
        regional = None
        if regional_phase is not None:
            # CONF-03's read rule: a Regional playoff is any Conference-scoped
            # row that is NOT ``last_chance``, so an un-backfilled CONF-02 row
            # with ``qualifier_stage == ""`` still resolves here. The Last-chance
            # bracket is excluded from BOTH the numerator and the denominator,
            # so a Team cannot ride it past the maximum path its Conference
            # offers.
            regional = (
                regional_phase.regional_tournaments.filter(conference=conference)
                .exclude(qualifier_stage="last_chance")
                .first()
            )
        worlds_phase = season._worlds_phase()
        worlds = worlds_phase.tournament if worlds_phase is not None else None
        if regional is None and worlds is None:
            # No bracket at all — the phase never built, or the Conference was
            # too small for a Regional playoff AND Worlds is unbuilt. This row
            # and the "missed" row below are what keep the pre-CONF-04 accident
            # from returning: adding the Worlds phase (which DOES set
            # ``tournament_id``) would otherwise have switched this axis on and
            # charged every non-qualifier the full penalty regardless of how far
            # it went in its own region.
            return ("none", 0, 0)

        regional_nodes = list(regional.nodes.all()) if regional is not None else []
        worlds_nodes = list(worlds.nodes.all()) if worlds is not None else []
        # Count PER BRACKET, then ADD — never union the raw ``bracket_round``
        # integers. The two brackets number their rounds independently from 1,
        # so a Team that won round 1 of its region and round 1 of Worlds has won
        # TWO rounds; a set union would collapse them to one. ``num_rounds`` is
        # the same maximum path for every Team in that Conference, so the
        # denominator is fair within a region even when Conferences differ in
        # size.
        num_rounds = max((n.bracket_round for n in regional_nodes), default=0) + max(
            (n.bracket_round for n in worlds_nodes), default=0
        )

        if worlds is not None and worlds.champion_id == team_id:
            return ("champion", 0, num_rounds)

        in_regional = (
            regional is not None
            and regional.participants.filter(team_id=team_id).exists()
        )
        in_worlds = (
            worlds is not None and worlds.participants.filter(team_id=team_id).exists()
        )
        if not (in_regional or in_worlds):
            return ("missed", 0, num_rounds)

        rounds_won = len(
            {n.bracket_round for n in regional_nodes if n.winner_id == team_id}
        ) + len({n.bracket_round for n in worlds_nodes if n.winner_id == team_id})
        return ("seeded", rounds_won, num_rounds)

    tournament = None
    for phase in season.ordered_phases():
        if phase.phase_type == "tournament" and phase.tournament_id is not None:
            tournament = phase.tournament
            break

    if tournament is None:
        return ("none", 0, 0)

    nodes = list(tournament.nodes.all())
    num_rounds = max((n.bracket_round for n in nodes), default=0)

    if tournament.champion_id == team_id:
        return ("champion", 0, num_rounds)

    is_participant = tournament.participants.filter(team_id=team_id).exists()
    if not is_participant:
        return ("missed", 0, num_rounds)

    rounds_won = len({n.bracket_round for n in nodes if n.winner_id == team_id})
    return ("seeded", rounds_won, num_rounds)


def is_career_league(league: League) -> bool:
    """CAR-03 — the owner-mood firing lifecycle runs only in single-player
    career mode (League.mode == "league"). Sandbox / multiplayer Leagues never
    fire, evaluate, or reassign."""
    return league.mode == "league"


def _delete_team_if_orphaned(team_id: int) -> None:
    """DEL-01 — delete a candidate Team iff, after the League's Seasons /
    Matches / Tournaments are gone, it has zero remaining references.

    Deleting the Team cascades its Players. Anything still referenced (a Team
    deliberately shared across contexts, or one that played a sandbox match) is
    left behind — safe over complete (ADR-0032)."""
    team = Team.objects.filter(pk=team_id).first()
    if team is None:
        return
    still_referenced = (
        team.red_matches.exists()
        or team.blue_matches.exists()
        or team.enrolled_seasons.exists()
        or team.managed_in_leagues.exists()
        or team.free_agent_pool_for.exists()
    )
    if not still_referenced:
        team.delete()


def _league_teardown_targets(league: League) -> tuple[list[int], set[int]]:
    """DEL-01 — the PK/FK-identified teardown targets for a League: its embedded
    tournament ids and its candidate Team ids (by PK/FK, never by name). Single
    source for both the GET confirm summary and the POST teardown so the two
    cannot drift. MUST be called before any delete — the drawn-team reverse
    accessor vanishes once the Tournaments go (ADR-0032)."""
    tournament_ids = list(
        SeasonPhase.objects.filter(
            season__league=league, tournament__isnull=False
        ).values_list("tournament_id", flat=True)
    )
    candidate_team_ids: set[int] = set()
    candidate_team_ids.update(
        Team.objects.filter(enrolled_seasons__league=league).values_list(
            "id", flat=True
        )
    )
    if league.current_team_id is not None:
        candidate_team_ids.add(league.current_team_id)
    if league.free_agent_pool_id is not None:
        candidate_team_ids.add(league.free_agent_pool_id)
    candidate_team_ids.update(
        Team.objects.filter(
            drawn_player_entries__tournament_id__in=tournament_ids
        ).values_list("id", flat=True)
    )
    # L-10: member-night drawn Teams are never enrolled in the Season; they are
    # reachable only through the League's (season-attached) Matches.
    candidate_team_ids.update(
        Team.objects.filter(is_draw_team=True)
        .filter(
            Q(red_matches__season__league=league)
            | Q(blue_matches__season__league=league)
        )
        .values_list("id", flat=True)
    )
    return tournament_ids, candidate_team_ids


def _league_match_q(league: League, tournament_ids: list[int]) -> Q:
    """DEL-01 — the filter matching a League's Matches: regular-season
    (``Match.season``) plus embedded-tournament bracket Matches (which carry
    ``season=NULL`` and are reached via the series chain). Shared by the teardown
    delete and the confirm-summary count."""
    return Q(season__league=league) | Q(
        series_match__node__tournament_id__in=tournament_ids
    )


def teardown_league(league: League) -> None:
    """DEL-01 — full teardown of all data a career League owns, by PK/FK
    identity (never by name), in one atomic block (ADR-0032)."""
    with transaction.atomic():
        # 1+2 — embedded tournament ids + candidate Team ids, collected NOW
        #     (before any delete) via the shared target helper.
        tournament_ids, candidate_team_ids = _league_teardown_targets(league)

        # 3 — delete league Matches (regular-season + embedded-tournament
        #     bracket). Match.season is SET_NULL, so they must be deleted
        #     explicitly or they survive orphaned.
        Match.objects.filter(
            _league_match_q(league, tournament_ids)
        ).distinct().delete()

        # 4 — delete embedded Tournaments (cascades participants / nodes /
        #     series / entries).
        Tournament.objects.filter(id__in=tournament_ids).delete()

        # 5 — delete the League (cascades Seasons + their dependents; SET_NULLs
        #     current_team / free_agent_pool FKs).
        league.delete()

        # 6 — zero-reference guard, per candidate Team id.
        for team_id in candidate_team_ids:
            _delete_team_if_orphaned(team_id)


def _team_winp_for_season(season: Season, team_id: int) -> float:
    """Win fraction (wins / matches_played) for ``team_id`` in ``season``.

    Reads the regular-season ``StandingsRow`` via ``_team_wins_games_for_season``
    (the same ``compute_standings`` assembly). 0 games ⇒ a neutral 0.5 (no
    div-by-zero), matching the ZenGM new-team 0.500 default.
    """
    won, games = _team_wins_games_for_season(season, team_id)
    if games == 0:
        return 0.5
    return won / games


def _ensure_team_finances(league: League, up_to_season: Season) -> None:
    """FIN-01 — lazily ensure a ``TeamSeasonFinance`` row for every enrolled
    Team of every completed Season up to and including ``up_to_season``.

    The twin of ``_ensure_owner_evaluations``. First line early-returns when
    ``not is_career_league(league) or not league.finance_enabled`` (the toggle
    gate ON TOP of the mode gate) — OFF ⇒ writes ZERO rows.

    Walks completed Seasons oldest→newest so hype carries across Seasons. Per
    Season, per enrolled Team, ``get_or_create``-keyed on ``(team, season)``
    (idempotent — a present row left untouched, no backfill). Reads the active-
    roster salaries in effect for the completed Season (recomputed/refreshed for
    the Team before summing payroll), the carried ``prev_hype`` / ``winp_old``
    from the prior Season's row, computes ``compute_team_finance(...)``, persists
    the row, carries cash across Seasons (``team.cash += result.profit``), and
    persists the team's cash + refreshed active-roster salaries.

    First-season hype seeding: when no prior ``TeamSeasonFinance`` snapshot
    exists for that Team, ``prev_hype = 0.0`` and ``winp_old = 0.5`` (the ZenGM
    new-team default — neutral 0.500 baseline).
    """
    if not is_career_league(league) or not league.finance_enabled:
        return

    seasons = list(
        league.seasons.filter(state="completed", id__lte=up_to_season.id).order_by("id")
    )

    # Per-Team carried state across Seasons (hype + prior win fraction).
    prev_hype_by_team: dict[int, float] = {}
    prev_winp_by_team: dict[int, float] = {}

    for season in seasons:
        # Enrolled Teams of the completed Season: the frozen snapshot the
        # standings use (defensive fallback to the live M2M).
        team_ids = list(season.starting_team_ids_json or [])
        if not team_ids:
            team_ids = list(season.teams.values_list("id", flat=True))
        teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}

        for team_id in team_ids:
            team = teams_by_id.get(team_id)
            if team is None:
                continue

            existing = TeamSeasonFinance.objects.filter(
                team=team, season=season
            ).first()
            if existing is not None:
                # Idempotent — re-read the carried state from the present row.
                prev_hype_by_team[team_id] = existing.hype
                prev_winp_by_team[team_id] = _team_winp_for_season(season, team_id)
                continue

            # Active-roster salaries in effect for the completed Season —
            # recomputed/refreshed before summing payroll (LG-04 precedent).
            active = list(team.active_players)
            for p in active:
                p.salary = finance.salary_for_overall(p.overall_rating)
            if active:
                Player.objects.bulk_update(active, ["salary"])
            payroll = sum(p.salary or 0.0 for p in active)

            winp = _team_winp_for_season(season, team_id)
            prev_hype = prev_hype_by_team.get(team_id, 0.0)
            winp_old = prev_winp_by_team.get(team_id, 0.5)

            result = finance.compute_team_finance(
                payroll=payroll,
                scouting_level=team.budget_scouting,
                coaching_level=team.budget_coaching,
                facilities_level=team.budget_facilities,
                health_level=team.budget_health,
                ticket_price=team.ticket_price,
                prev_hype=prev_hype,
                winp=winp,
                winp_old=winp_old,
            )

            TeamSeasonFinance.objects.get_or_create(
                team=team,
                season=season,
                defaults={
                    "ticket": result.revenue_lines.ticket,
                    "national_tv": result.revenue_lines.national_tv,
                    "local_tv": result.revenue_lines.local_tv,
                    "sponsor": result.revenue_lines.sponsor,
                    "merch": result.revenue_lines.merch,
                    "payroll": result.expense_lines.payroll,
                    "scouting_cost": result.expense_lines.scouting,
                    "coaching_cost": result.expense_lines.coaching,
                    "facilities_cost": result.expense_lines.facilities,
                    "luxury_tax": result.expense_lines.luxury_tax,
                    "min_payroll_penalty": (result.expense_lines.min_payroll_penalty),
                    "health_cost": result.expense_lines.health,
                    "revenue": result.revenue,
                    "expenses": result.expenses,
                    "profit": result.profit,
                    "hype": result.hype,
                    "budget_scouting": team.budget_scouting,
                    "budget_coaching": team.budget_coaching,
                    "budget_facilities": team.budget_facilities,
                    "games_played": _team_wins_games_for_season(season, team_id)[1],
                },
            )

            # Cash carries across Seasons (ZenGM precedent).
            team.cash += result.profit
            team.save(update_fields=["cash"])

            prev_hype_by_team[team_id] = result.hype
            prev_winp_by_team[team_id] = winp


def _ensure_owner_evaluations(league: League, up_to_season: Season) -> None:
    """CAR-02 — lazily ensure an ``OwnerEvaluation`` row for every completed
    Season of ``league`` up to and including ``up_to_season``.

    Written oldest→newest in Season order so the per-factor caps + cumulatives +
    tenure derivation are correct. ``get_or_create``-keyed on ``(league, season)``
    (idempotent — a row already present is left untouched; no backfill of Seasons
    before the first computable one, ADR-0004).

    Tenure boundaries derive from the snapshot chain: the first ever row's
    ``team_managed`` is ``league.current_team`` at write time (the founding team);
    each subsequent row inherits the prior row's ``team_managed`` UNLESS the prior
    row's verdict was ``"fired"`` (which ends the tenure — the next row reads
    ``league.current_team`` again, the post-Reassignment team). A ``team_managed``
    change between consecutive rows resets the cumulative + the grace counter.
    """
    if not is_career_league(league):
        return
    seasons = list(
        league.seasons.filter(state="completed", id__lte=up_to_season.id).order_by("id")
    )

    # Running per-factor cumulative totals + the tenure marker, threaded oldest→newest.
    running_wins = 0.0
    running_playoffs = 0.0
    running_money = 0.0
    prev_team_managed_id: Optional[int] = None
    prev_verdict: Optional[str] = None
    seasons_in_tenure = 0
    first_row = True

    for season in seasons:
        existing = OwnerEvaluation.objects.filter(league=league, season=season).first()
        if existing is not None:
            # The persisted row is the source of truth for prior Seasons — re-read
            # its totals / team_managed into the running state and continue. Tenure
            # tracking re-derives from the persisted chain.
            team_managed_id = existing.team_managed_id
            if (not first_row) and team_managed_id == prev_team_managed_id:
                seasons_in_tenure += 1
            else:
                seasons_in_tenure = 1
            running_wins = existing.wins_total
            running_playoffs = existing.playoffs_total
            running_money = existing.money_total
            prev_team_managed_id = team_managed_id
            prev_verdict = existing.verdict
            first_row = False
            continue

        # Resolve team_managed for this Season from the snapshot chain (§3.1).
        if first_row or prev_verdict == "fired":
            team = league.current_team
            team_managed_id = team.id if team is not None else None
        else:
            team_managed_id = prev_team_managed_id

        # Tenure reset on a team_managed change (or first in-tenure row).
        if first_row or team_managed_id != prev_team_managed_id:
            running_wins = 0.0
            running_playoffs = 0.0
            running_money = 0.0
            seasons_in_tenure = 1
        else:
            seasons_in_tenure += 1

        # Build the flat inputs (§3.1a) for the managed team.
        if team_managed_id is not None:
            won, games = _team_wins_games_for_season(season, team_managed_id)
            playoff_result, rounds_won, num_rounds = _classify_playoffs_for_team(
                season, team_managed_id
            )
        else:
            won, games = 0, 0
            playoff_result, rounds_won, num_rounds = ("none", 0, 0)

        wins_delta = owner_mood.compute_wins_delta(won, games)
        playoffs_delta = owner_mood.compute_playoffs_delta(
            playoff_result, rounds_won, num_rounds
        )

        # FIN-01 — the money axis. When finance is ON and a TeamSeasonFinance
        # row exists for the managed Team, read its profit and compute the
        # money delta; cap-chain the cumulative through the existing
        # ``running_money`` thread. When OFF (or no row), keep money_delta /
        # money_total at 0.0 exactly as today (byte-identical-when-OFF).
        money_delta = 0.0
        money_total = 0.0
        # FIN-05 — whether the managed team paid the luxury tax this Season.
        # Initialised False BEFORE the finance gate so a finance-OFF League
        # leaves it False (no luxury fire possible).
        luxury_tax_paid = False
        if league.finance_enabled and team_managed_id is not None:
            tsf = TeamSeasonFinance.objects.filter(
                team_id=team_managed_id, season=season
            ).first()
            if tsf is not None:
                money_delta = finance.money_delta(tsf.profit)
                money_total = owner_mood.cap_cumulative(running_money, money_delta)
                luxury_tax_paid = tsf.luxury_tax > 0

        wins_total = owner_mood.cap_cumulative(running_wins, wins_delta)
        playoffs_total = owner_mood.cap_cumulative(running_playoffs, playoffs_delta)

        verdict = owner_mood.decide_verdict(
            owner_mood.MoodTotals(
                wins=wins_total, playoffs=playoffs_total, money=money_total
            ),
            owner_mood.MoodDeltas(
                wins=wins_delta, playoffs=playoffs_delta, money=money_delta
            ),
            seasons_in_tenure=seasons_in_tenure,
            luxury_tax_paid=luxury_tax_paid,
            challenge_fired_luxury_tax=league.challenge_fired_luxury_tax,
        )

        # FIN-05 — reconstruct the firing reason from the same inputs the
        # decider used; stamped on the immutable row, read back verbatim.
        fired_reason = (
            "luxury_tax"
            if (
                verdict.outcome == "fired"
                and league.challenge_fired_luxury_tax
                and luxury_tax_paid
            )
            else ("owner_mood" if verdict.outcome == "fired" else "")
        )

        OwnerEvaluation.objects.get_or_create(
            league=league,
            season=season,
            defaults={
                "team_managed_id": team_managed_id,
                "wins_delta": wins_delta,
                "playoffs_delta": playoffs_delta,
                "money_delta": money_delta,
                "wins_total": wins_total,
                "playoffs_total": playoffs_total,
                "money_total": money_total,
                "verdict": verdict.outcome,
                "hot_seat_level": verdict.hot_seat_level,
                "fired_reason": fired_reason,
            },
        )

        running_wins = wins_total
        running_playoffs = playoffs_total
        running_money = money_total
        prev_team_managed_id = team_managed_id
        prev_verdict = verdict.outcome
        first_row = False


def _team_wins_games_for_season(season: Season, team_id: int) -> tuple[int, int]:
    """Regular-season (W, games) record for ``team_id`` in ``season``.

    Reuses ``Season._final_standings_for_phase`` (the same ``compute_standings``
    assembly the playoff seeding uses) and reads the ``StandingsRow`` for
    ``team_id``. Returns ``(0, 0)`` when the team has no row.
    """
    rows = season._final_standings_for_phase(season.ordered_phases()[-1])
    for row in rows:
        if row.team_id == team_id:
            return (row.wins, row.matches_played)
    return (0, 0)


def run_season_rollover(league: League, latest_completed: Season) -> Season:
    """Create + return the next draft Season (teams/map/phases carried, players
    developed). The shared body consumed by BOTH the normal Start-Next-Season
    path and the reassign-then-roll path.

    NOT separately decorated — the caller owns the ``@transaction.atomic``
    boundary (both call sites are atomic). Body extracted VERBATIM from the
    pre-CAR-02 ``next_season``.
    """
    all_seasons = list(league.seasons.all())

    name = f"Season {len(all_seasons) + 1}"
    start_date = date(latest_completed.start_date.year + 1, 1, 1)
    schedule_format = latest_completed.schedule_format

    new_season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format=schedule_format,
        state="draft",
        # LG-01j — carry map_mode verbatim from the previous Season.
        # CONF-06 — the rollover carries no Conferences, so a carried
        # ``rotate_by_conference`` mode would resolve None for every fixture
        # all season. Downgrade it to the 3-zone fallback; carry every other
        # mode verbatim.
        map_mode=(
            "none"
            if latest_completed.map_mode == "rotate_by_conference"
            else latest_completed.map_mode
        ),
    )

    team_ids = latest_completed.starting_team_ids_json or []
    if team_ids:
        teams_qs = Team.objects.filter(id__in=team_ids)
        new_season.teams.add(*teams_qs)

    # LG-01j — rehydrate the new Season's map_pool from the previous
    # Season's FROZEN SNAPSHOT (NOT its live M2M). The snapshot is the
    # source of truth post-activation: admin-side edits to the live
    # ``map_pool`` of the completed Season don't leak into the next
    # Season's pool. Deleted maps simply drop out of the queryset.
    from core.models import ArenaMap

    map_pool_ids = latest_completed.starting_map_pool_ids_json or []
    if map_pool_ids:
        new_season.map_pool.set(ArenaMap.objects.filter(id__in=map_pool_ids))

    # SUB-01 — carry the previous Season's author-ordered rotation list
    # forward verbatim (no re-sort).
    new_season.map_rotation_ids_json = list(
        latest_completed.map_rotation_ids_json or []
    )
    new_season.save(update_fields=["map_rotation_ids_json"])

    # LG-02-Part2b — carry the previous Season's full phase composition
    # forward (mirrors the team-id / map-pool carry-forward). Copy
    # ordinal / phase_type / schedule_format verbatim; reset tournament to
    # NULL. ``Meta.ordering = ["ordinal"]`` guarantees the source order.
    # LG-02-Part2c-3b — also carry ``tournament_mode`` verbatim so a future
    # non-``standings`` mode (Part2c-3c) reproduces across seasons.
    for src in latest_completed.phases.all():
        # CONF-04 — do NOT carry the derived Worlds phase forward (ADR-0037).
        # The rollover carries no Conferences, so a copied Worlds phase would
        # land on a flat Season whose ``worlds_qualifiers()`` returns ``[]``
        # forever, stranding it at ``active``. The new Season grows its own
        # Worlds phase at ``start_season`` if it is ever partitioned. Because the
        # Worlds phase always holds the HIGHEST ordinal, skipping it leaves the
        # copied ordinals contiguous from 1 — no renumbering is needed.
        if src.tournament_mode == "worlds":
            continue
        SeasonPhase.objects.create(
            season=new_season,
            ordinal=src.ordinal,
            phase_type=src.phase_type,
            schedule_format=src.schedule_format,
            tournament=None,
            tournament_mode=src.tournament_mode,
            # LG-02-Part2c-3d / 3e — carry all tournament columns forward verbatim
            # (the source row has real persisted values for each).
            tournament_cut=src.tournament_cut,
            tournament_format=src.tournament_format,
            final_series_length=src.final_series_length,
            semifinal_series_length=src.semifinal_series_length,
            quarterfinal_series_length=src.quarterfinal_series_length,
            earlier_series_length=src.earlier_series_length,
            wb_advancers=src.wb_advancers,
            lb_advancers=src.lb_advancers,
            swiss_rounds=src.swiss_rounds,
        )

    # LG-04 — age + develop every Player in the rolling League's developing set,
    # tick total_games, and write one PlayerSeasonRating row tagged to the NEW
    # Season. Inside the same atomic block (after carry-forward, before redirect)
    # so a failure rolls back the whole rollover.
    _develop_league_for_new_season(league, new_season, latest_completed)

    return new_season


# CAR-02 — the worst-N eligible teams a fired Manager may choose from.
WORST_N_ELIGIBLE = 5


def eligible_new_teams(league: League, latest_completed: Season) -> list:
    """CAR-02 — the worst-``WORST_N_ELIGIBLE`` teams by the just-completed
    Season's final Standings, EXCLUDING the just-left team
    (``league.current_team``).

    Returns ``list[(StandingsRow, Team)]`` so the template renders id + name +
    rank. The just-left team is dropped BEFORE the slice so a manager always sees
    a full worst-N list (where the field is large enough).
    """
    rows = latest_completed._final_standings_for_phase(
        latest_completed.ordered_phases()[-1]
    )
    # Worst = highest rank; sort descending by rank.
    rows_sorted = sorted(rows, key=lambda r: r.rank, reverse=True)
    current_id = league.current_team_id
    eligible = [r for r in rows_sorted if r.team_id != current_id]
    eligible = eligible[:WORST_N_ELIGIBLE]
    teams_by_id = Team.objects.in_bulk([r.team_id for r in eligible])
    return [(r, teams_by_id.get(r.team_id)) for r in eligible]


# ---------------------------------------------------------------------------
# ANG-01d-1 — new builders / orchestrators (contract §2.2).
# ---------------------------------------------------------------------------


def season_mode_for(season: Season | None) -> str:
    """The dashboard ``season_mode`` ladder for a displayed Season.

    ``None`` ⇒ ``"none"``; a ``draft`` / ``completed`` Season ⇒ its state;
    anything else ⇒ ``"active"`` (exactly the ``league_dashboard`` ladder).
    """
    if season is None:
        return "none"
    if season.state == "draft":
        return "draft"
    if season.state == "completed":
        return "completed"
    return "active"


def latest_completed_season(league: League) -> Season | None:
    """The League's newest completed Season (highest id), or ``None``."""
    return league.seasons.filter(state="completed").order_by("-id").first()


def start_next_season(league: League) -> Season:
    """The Start-Next-Season orchestration (guards + verdict gate + rollover).

    NOT decorated — the caller owns the atomic boundary (like
    ``run_season_rollover``). Raises, in order: ``SeasonInProgressError`` when a
    non-completed Season exists; ``NoCompletedSeasonError`` when there is no
    completed Season; ``ManagerFiredError`` when the Manager was fired and has
    not reassigned (``league.current_team`` still == the evaluation's
    ``team_managed``). The finance + owner-evaluation ensures run before the
    verdict read (FIN-01 order — finance rows feed the money axis).
    """
    active = league.active_season
    if active is not None:
        raise SeasonInProgressError(active)
    latest = latest_completed_season(league)
    if latest is None:
        raise NoCompletedSeasonError("No completed Season in this League.")

    _ensure_team_finances(league, latest)
    _ensure_owner_evaluations(league, latest)
    evaluation = OwnerEvaluation.objects.filter(league=league, season=latest).first()
    if (
        evaluation is not None
        and evaluation.verdict == "fired"
        and league.current_team_id == evaluation.team_managed_id
    ):
        raise ManagerFiredError(evaluation)

    return run_season_rollover(league, latest)


def reassign_and_roll(league: League, team: Team, latest_completed: Season) -> Season:
    """Point the Manager at ``team`` (a new tenure), then roll the Season.

    The caller owns the atomic boundary AND all validation (career mode,
    eligibility, ownership).
    """
    league.current_team = team
    league.save(update_fields=["current_team"])
    return run_season_rollover(league, latest_completed)


def ensure_owner_evaluation(season: Season) -> OwnerEvaluation | None:
    """Lazily write the finance + owner-evaluation rows up to ``season`` and
    return its ``OwnerEvaluation`` (``None`` for a non-completed Season or a
    non-career League — the writers skip both).

    FIN-01 order: finance rows first so the money axis reads a real profit.
    """
    league = season.league
    _ensure_team_finances(league, season)
    _ensure_owner_evaluations(league, season)
    return OwnerEvaluation.objects.filter(league=league, season=season).first()


def build_season_standings(
    season: Season, *, sort: str = "rank", direction: str = "asc"
) -> dict:
    """The Season standings tables — one group per Conference (CONF-01).

    Draft Season ⇒ the zero-filled preview rows sorted by team strength; active
    / completed ⇒ ``compute_standings`` over the completed Matches (member
    nights excluded). ``sort`` must be a ``STANDINGS_SORT_KEYS`` member and
    ``direction`` ``"asc"`` / ``"desc"`` (callers coerce). ``rank`` is never
    renumbered by the sort.
    """
    is_draft_preview = season.state == "draft"

    def _row_team_id(row) -> int:
        if hasattr(row, "team_id"):
            return row.team_id
        return row["team_id"]

    def _draft_group(teams_list):
        teams = sorted(teams_list, key=lambda t: (-_compute_team_overall(t), t.name))
        teams_by_id = {t.id: t for t in teams}
        group_rows: list = []
        for index, team in enumerate(teams):
            group_rows.append(
                {
                    "team_id": team.id,
                    "matches_played": 0,
                    "wins": 0,
                    "losses": 0,
                    "ties": 0,
                    "league_points": 0,
                    "round_wins": 0,
                    "total_score": 0,
                    "rank": index + 1,
                    # LG-06g — zeroed form / side-detail cells.
                    "match_streak": ("", 0),
                    "match_l5": (0, 0, 0),
                    "round_streak": ("", 0),
                    "round_l5": (0, 0, 0),
                    "red_wlt": (0, 0, 0),
                    "blue_wlt": (0, 0, 0),
                    "red_points_for": 0,
                    "blue_points_for": 0,
                }
            )
        return group_rows, teams_by_id

    def _live_group(team_ids, match_qs, rounds_qs):
        completed_matches: list[dict] = []
        for match in match_qs:
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
                    "date_played": match.date_played,
                }
            )
        # LG-06g — every persisted Round (incl. Rounds of in-progress Matches)
        # for the Round-grain form + per-physical-side split. The stored
        # ``team_red_id`` / ``team_blue_id`` are the actual physical sides
        # (SIM-08), so red/blue points map straight to each side.
        season_rounds = [
            {
                "round_id": r["id"],
                "team_red_id": r["team_red_id"],
                "team_blue_id": r["team_blue_id"],
                "red_points": r["red_points"],
                "blue_points": r["blue_points"],
                "date_played": r["date_played"],
            }
            for r in rounds_qs.values(
                "id",
                "team_red_id",
                "team_blue_id",
                "red_points",
                "blue_points",
                "date_played",
            )
        ]
        enrolled_teams = list(
            Team.objects.filter(id__in=team_ids).values_list("id", "name")
        )
        group_rows = compute_standings(completed_matches, enrolled_teams, season_rounds)
        teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}
        return group_rows, teams_by_id

    def _sort_and_pair(group_rows, teams_by_id):
        name_by_id = {tid: team.name for tid, team in teams_by_id.items()}
        group_rows.sort(
            key=lambda r: (
                _standings_sort_value(r, name_by_id.get(_row_team_id(r), ""), sort),
                _row_team_id(r),
            ),
            reverse=(direction == "desc"),
        )
        return [(row, teams_by_id.get(_row_team_id(row))) for row in group_rows]

    # CONF-01 — one standings group per Conference; a zero-Conference Season
    # renders exactly one group (``conference=None``), byte-identical to today.
    conferences = season.ordered_conferences()
    standings_groups: list[dict] = []
    rows: list = []
    rows_with_teams: list = []

    if not conferences:
        if is_draft_preview:
            group_rows, group_teams = _draft_group(list(season.teams.all()))
        else:
            # LG-07a — member-night Matches are social, never ranked.
            match_qs = Match.objects.filter(season=season, is_completed=True).exclude(
                season_phase__phase_type="member_night"
            )
            rounds_qs = GameRound.objects.filter(match__season=season).exclude(
                match__season_phase__phase_type="member_night"
            )
            if season.starting_team_ids_json is not None:
                team_ids = list(season.starting_team_ids_json)
            else:
                team_ids = sorted(t.id for t in season.teams.all())
            group_rows, group_teams = _live_group(team_ids, match_qs, rounds_qs)
        rwt = _sort_and_pair(group_rows, group_teams)
        standings_groups.append(
            {"conference": None, "name": None, "rows_with_teams": rwt}
        )
        rows = group_rows
        rows_with_teams = rwt
    else:
        for conf in conferences:
            if is_draft_preview:
                group_rows, group_teams = _draft_group(list(conf.teams.all()))
            else:
                match_qs = Match.objects.filter(
                    season=season, conference=conf, is_completed=True
                ).exclude(season_phase__phase_type="member_night")
                rounds_qs = GameRound.objects.filter(
                    match__season=season, match__conference=conf
                ).exclude(match__season_phase__phase_type="member_night")
                if conf.starting_team_ids_json is not None:
                    team_ids = list(conf.starting_team_ids_json)
                else:
                    team_ids = sorted(t.id for t in conf.teams.all())
                group_rows, group_teams = _live_group(team_ids, match_qs, rounds_qs)
            rwt = _sort_and_pair(group_rows, group_teams)
            standings_groups.append(
                {"conference": conf, "name": conf.name, "rows_with_teams": rwt}
            )
            rows.extend(group_rows)
            rows_with_teams.extend(rwt)

    return {
        "is_draft_preview": is_draft_preview,
        "standings_groups": standings_groups,
        "rows": rows,
        "rows_with_teams": rows_with_teams,
    }


def build_season_schedule(season: Season) -> dict:
    """The Season schedule: matchdays of fixtures with played Rounds overlaid,
    plus the member-night games (LG-07). ``< 2`` enrolled teams ⇒ no matchdays.

    Each fixture dict keeps the 11 ``season_schedule`` keys, incl. the Team
    instances ``team_a`` / ``team_b``; the display date for matchday ``n`` is
    ``season.start_date + (n - 1) * 7 days``.
    """
    if season.state == "draft":
        team_ids = sorted(t.id for t in season.teams.all())
    else:
        team_ids = list(season.starting_team_ids_json or [])

    if len(team_ids) < 2:
        # Cannot generate a schedule with fewer than 2 teams.
        return {
            "matchdays": [],
            "member_night_games": _member_night_games_for_season(season),
        }

    # LG-02-Part2a — source fixtures through the Season chokepoint.
    fixtures = season.scheduled_fixtures()

    teams_by_id: dict[int, Team] = {
        t.id: t for t in Team.objects.filter(id__in=team_ids)
    }

    # Index played GameRounds by (frozenset of team ids, round_number, leg).
    # LG-02-Part2c-3a — leg distinguishes a double_round_robin phase's two legs.
    rounds_qs = GameRound.objects.filter(match__season=season).select_related("match")
    played_by_key: dict[tuple[frozenset[int], int, int], GameRound] = {}
    for game_round in rounds_qs:
        match = game_round.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        key = (
            frozenset({match.team_red_id, match.team_blue_id}),
            game_round.round_number,
            match.leg,
        )
        played_by_key[key] = game_round

    # Build per-fixture dicts.
    per_fixture: list[dict] = []
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        game_round = played_by_key.get(key)
        if game_round is not None:
            played = True
            game_round_id = game_round.id
            red_score = game_round.red_points
            blue_score = game_round.blue_points
        else:
            played = False
            game_round_id = None
            red_score = None
            blue_score = None

        fixture_date = season.start_date + timedelta(days=(fixture.matchday - 1) * 7)
        per_fixture.append(
            {
                "matchday": fixture.matchday,
                "round_number": fixture.round_number,
                "team_a_id": fixture.team_a_id,
                "team_b_id": fixture.team_b_id,
                "team_a": teams_by_id.get(fixture.team_a_id),
                "team_b": teams_by_id.get(fixture.team_b_id),
                "played": played,
                "game_round_id": game_round_id,
                "red_score": red_score,
                "blue_score": blue_score,
                "date": fixture_date,
            }
        )

    # Group by matchday in matchday-asc order.
    matchdays: list[dict] = []
    grouped: dict[int, list[dict]] = defaultdict(list)
    for f in per_fixture:
        grouped[f["matchday"]].append(f)
    for matchday in sorted(grouped.keys()):
        matchday_fixtures = grouped[matchday]
        matchday_date = matchday_fixtures[0]["date"]
        matchdays.append(
            {
                "matchday": matchday,
                "date": matchday_date,
                "fixtures": matchday_fixtures,
            }
        )

    return {
        "matchdays": matchdays,
        "member_night_games": _member_night_games_for_season(season),
    }


def build_team_schedule(displayed_season: Season, team: Team) -> dict:
    """One Team's Upcoming / Completed per-Round schedule in a Season, plus its
    member-night appearances (LG-01g / LG-07)."""
    if displayed_season.starting_team_ids_json:
        team_ids = list(displayed_season.starting_team_ids_json)
    else:
        team_ids = sorted(t.id for t in displayed_season.teams.all())

    # LG-02-Part2a — route fixtures through the Season chokepoint.
    fixtures = displayed_season.scheduled_fixtures()
    teams_by_id = Team.objects.in_bulk(team_ids)

    played_game_rounds = list(
        GameRound.objects.filter(match__season=displayed_season)
        .filter(Q(match__team_red=team) | Q(match__team_blue=team))
        .select_related("match", "match__team_red", "match__team_blue")
        .order_by("id")
    )

    rows = _build_team_schedule_rows(
        displayed_season=displayed_season,
        team=team,
        fixtures=fixtures,
        played_game_rounds=played_game_rounds,
        teams_by_id=teams_by_id,
    )
    return {
        "upcoming": rows["upcoming"],
        "completed": rows["completed"],
        "member_night_appearances": _member_night_appearances_for_team(
            displayed_season, team
        ),
    }


def history_seasons(
    league: League,
) -> tuple[Season | None, list[Season], dict[int, Team]]:
    """The League History query block: ``(in_progress_season,
    completed_seasons newest-first, teams_by_id)``."""
    seasons_qs = (
        league.seasons.select_related("champion_team")
        # LG-07a — prefetch ``matches__season_phase`` so the member-night
        # in-Python skip in ``build_history_row`` reads the FK off the cache
        # (no per-Match N+1 on ``match.season_phase``).
        .prefetch_related("matches", "matches__season_phase", "teams", "conferences")
        .filter(state__in=["active", "draft", "completed"])
        .order_by("-id")
    )
    seasons = list(seasons_qs)

    team_ids: set[int] = set()
    for s in seasons:
        team_ids.update(s.starting_team_ids_json or [])
        if s.state in {"active", "draft"}:
            team_ids.update(t.id for t in s.teams.all())
    teams_by_id = Team.objects.in_bulk(team_ids)

    in_progress_season = next(
        (s for s in seasons if s.state in {"active", "draft"}),
        None,
    )
    completed_seasons = [s for s in seasons if s.state == "completed"]
    return in_progress_season, completed_seasons, teams_by_id


def build_season_awards(season: Season) -> dict:
    """LG-03 — a Season's award set, the 5 K/D rows, and whether it has any
    regular-season Rounds."""
    awards = compute_season_award_set(season)
    has_rounds = PlayerRoundState.objects.filter(
        game_round__match__season=season
    ).exists()

    # ``kd_by_role`` rendered as ordered (role, label, winner) tuples so the
    # template can emit the 5 ``season-awards-kd-{role}`` rows without
    # dict-indexing by a loop variable.
    kd_rows = [
        (role, _KD_ROLE_LABELS[role], awards.kd_by_role.get(role)) for role in ROLE_KEYS
    ]
    return {"awards": awards, "kd_rows": kd_rows, "has_rounds": has_rounds}


def build_conferences_state(season: Season) -> dict:
    """CONF-05 / CONF-06 — the Season's Conference partition as plain data.

    ``teams`` is the enrolled Teams sorted by name; each Conference carries its
    sorted live team ids, its live + activation-snapshot rotation lists and its
    team snapshot; ``unassigned_team_ids`` are the enrolled ids in no
    Conference (sorted).
    """
    teams = sorted(season.teams.all(), key=lambda t: t.name)
    assigned: set[int] = set()
    conferences: list[dict] = []
    # Same ordinal order as ``ordered_conferences()``, with ``teams`` prefetched
    # so the per-Conference membership read is not an N+1.
    for conf in season.conferences.prefetch_related("teams"):
        team_ids = sorted(t.id for t in conf.teams.all())
        assigned.update(team_ids)
        conferences.append(
            {
                "id": conf.id,
                "name": conf.name,
                "ordinal": conf.ordinal,
                "team_ids": team_ids,
                "map_rotation_ids": list(conf.map_rotation_ids_json or []),
                "starting_team_ids": conf.starting_team_ids_json,
                "starting_map_rotation_ids": conf.starting_map_rotation_ids_json,
            }
        )
    return {
        "editable": season.state == "draft",
        "map_mode": season.map_mode,
        "teams": teams,
        "conferences": conferences,
        "unassigned_team_ids": sorted(t.id for t in teams if t.id not in assigned),
    }


@transaction.atomic
def replace_conferences(
    season: Season, specs: list[tuple[str, list[int], list[int]]]
) -> None:
    """Replace the Season's Conferences with ``specs`` (``[]`` clears them).

    Each spec is ``(name, team_ids, map_rotation_ids)``; ordinals are assigned
    ``1..N`` in spec order. Callers validate first (``ConferencePartitionForm``).
    """
    season.conferences.all().delete()
    for ordinal, (name, team_ids, rotation) in enumerate(specs, start=1):
        conf = Conference.objects.create(
            season=season,
            name=name,
            ordinal=ordinal,
            map_rotation_ids_json=rotation,
        )
        conf.teams.set(team_ids)
