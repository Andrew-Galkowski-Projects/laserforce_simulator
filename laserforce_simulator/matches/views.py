import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from celery.result import AsyncResult
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Q, QuerySet
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.urls import reverse
from django.utils.text import slugify
from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Team, Player
from teams.views import _generate_teams
from . import h2h_stats, player_h2h_stats
from .models import Match, GameRound, PlayerRoundState, GameEvent, Season, League
from .schedule_generator import generate_schedule
from .season_dashboard import (
    LeaderRow,
    compute_leaders,
    find_next_fixture,
    round_progress,
    select_play_fixtures,
)
from .standings import compute_standings
from .simulation import BatchSimulator
from .sim_helpers.pdf_report import build_round_report
from .forms import (
    MatchSetupForm,
    SingleRoundSetupForm,
    BatchSimulateForm,
    CreateLeagueForm,
)
from .tasks import play_season_task, save_games_task, simulate_batch_task


def _celery_state_to_job_status(state: str) -> str:
    """Map a Celery native state to the public SIM-10 vocabulary."""
    if state == "SUCCESS":
        return "complete"
    if state in ("FAILURE", "REVOKED"):
        return "error"
    # PENDING / STARTED / PROGRESS / RETRY / anything else → keep polling.
    return "running"


def int_or_none(raw: str | None) -> int | None:
    """Coerce a request GET param to int; return None on missing/invalid."""
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def build_batch_status_response(
    async_result: AsyncResult,
    *,
    team_red_id: int | None,
    team_blue_id: int | None,
    arena_map_id: int | None,
) -> dict:
    """Build the polling JSON dict for a batch job from an AsyncResult."""
    state = async_result.state
    status = _celery_state_to_job_status(state)

    completed = 0
    total = 0
    partial: dict | None = None
    error: str | None = None

    if state == "PROGRESS":
        info = async_result.info or {}
        if isinstance(info, dict):
            completed = int(info.get("completed", 0) or 0)
            total = int(info.get("total", 0) or 0)
            partial = info.get("aggregate")
    elif state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            n_val = int(result.get("n", 0) or 0)
            completed = n_val
            total = n_val
            partial = result
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {
        "status": status,
        "completed": completed,
        "total": total,
        "partial": partial,
        "error": error,
        "team_red_id": team_red_id,
        "team_blue_id": team_blue_id,
        "arena_map_id": arena_map_id,
    }


def _build_save_status_response(async_result: AsyncResult) -> dict:
    """Build the polling JSON dict for a save job from an AsyncResult."""
    state = async_result.state
    status = _celery_state_to_job_status(state)

    round_ids: list[int] = []
    error: str | None = None

    if state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            round_ids = list(result.get("round_ids", []) or [])
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {"status": status, "error": error, "round_ids": round_ids}


# RV-01: ordered stat keys for the round-comparison delta table. `mvp` and
# `accuracy` are computed from properties; every other key is a same-named
# PlayerRoundState IntegerField.
_COMPARE_STAT_KEYS: list[str] = [
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
]

# Stat keys that map directly to same-named PlayerRoundState IntegerFields.
_COMPARE_FIELD_STAT_KEYS: list[str] = [
    "points_scored",
    "tags_made",
    "times_tagged",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
]


def _shared_team_ids(round_a: GameRound, round_b: GameRound) -> list[int]:
    """RV-01: sorted list of Team ids present in both rounds, ignoring Side.

    A team counts as shared whether it played red in one round and blue in the
    other — the comparison is by team identity, not physical Side.
    """
    ids_a = {round_a.team_red_id, round_a.team_blue_id}
    ids_b = {round_b.team_red_id, round_b.team_blue_id}
    return sorted(ids_a & ids_b)


def _stat_values(ps: PlayerRoundState) -> dict:
    """RV-01: extract the ordered comparison stats from one PlayerRoundState.

    `mvp` reads the `get_mvp` property (float), `accuracy` the `get_accuracy`
    property (int percent, divide-by-zero guarded); all other keys are the
    same-named IntegerField.
    """
    values: dict = {"mvp": ps.get_mvp, "accuracy": ps.get_accuracy}
    for key in _COMPARE_FIELD_STAT_KEYS:
        values[key] = getattr(ps, key)
    return values


def _player_stat_deltas(
    round_a: GameRound, round_b: GameRound, team_ids: list[int]
) -> list[dict]:
    """RV-01: per-player stat-delta rows for the two rounds, paired by player.

    Only players whose ``player.team_id`` is in ``team_ids`` are included.
    Rows are paired by ``player_id``; a player present in only one round yields
    a row whose missing side's ``role_*``/``side_*`` and per-stat value are
    ``None`` (and the delta ``None``). ``delta = b - a`` when both sides exist.
    Rows are ordered by name.
    """
    team_id_set = set(team_ids)

    def _states_for(game_round: GameRound) -> dict[int, PlayerRoundState]:
        return {
            ps.player_id: ps
            for ps in game_round.player_states.select_related("player").all()
            if ps.player.team_id in team_id_set
        }

    states_a = _states_for(round_a)
    states_b = _states_for(round_b)

    rows: list[dict] = []
    for player_id in set(states_a) | set(states_b):
        ps_a = states_a.get(player_id)
        ps_b = states_b.get(player_id)
        name = (ps_a or ps_b).player.name

        values_a = _stat_values(ps_a) if ps_a is not None else None
        values_b = _stat_values(ps_b) if ps_b is not None else None

        stats: dict = {}
        for key in _COMPARE_STAT_KEYS:
            a_val = values_a[key] if values_a is not None else None
            b_val = values_b[key] if values_b is not None else None
            delta = (
                (b_val - a_val) if (a_val is not None and b_val is not None) else None
            )
            stats[key] = {"a": a_val, "b": b_val, "delta": delta}

        rows.append(
            {
                "player_id": player_id,
                "name": name,
                "role_a": ps_a.role if ps_a is not None else None,
                "role_b": ps_b.role if ps_b is not None else None,
                "side_a": ps_a.team_color if ps_a is not None else None,
                "side_b": ps_b.team_color if ps_b is not None else None,
                "stats": stats,
                # Template-friendly ordered view of ``stats`` (Django templates
                # cannot do dynamic dict lookup by a variable key). Additive —
                # the contracted ``stats`` dict above is unchanged and remains
                # the JSON source of truth.
                "cells": [stats[key] for key in _COMPARE_STAT_KEYS],
            }
        )

    rows.sort(key=lambda row: row["name"])
    return rows


def _cumulative_team_points(game_round: GameRound, team_id: int) -> list[list]:
    """RV-01: cumulative points-over-time series for one team in one round.

    Walks the round's events for actors on ``team_id`` ordered by timestamp,
    accumulating ``points_awarded`` (NULL coalesced to 0). Returns
    ``[[tick, cumulative_points], ...]``; an empty event set yields ``[]``.
    """
    series: list[list] = []
    cumulative = 0
    events = (
        game_round.events.filter(actor__team_id=team_id)
        .order_by("timestamp")
        .values_list("timestamp", "points_awarded")
    )
    for timestamp, points_awarded in events:
        cumulative += points_awarded or 0
        series.append([timestamp, cumulative])
    return series


def compare_rounds(request) -> HttpResponse:
    """RV-01: read-only side-by-side comparison of two GameRounds.

    Reads ``round_a``/``round_b`` GET params. Missing/empty → picker mode.
    Both present → fetch each (404 on invalid or non-numeric id); same round or
    no shared team → error mode (200). Distinct rounds sharing >=1 team → full
    comparison.
    """
    all_rounds = GameRound.objects.select_related("team_red", "team_blue").order_by(
        "-id"
    )

    raw_a = request.GET.get("round_a")
    raw_b = request.GET.get("round_b")

    context = {
        "round_a": None,
        "round_b": None,
        "all_rounds": all_rounds,
        "mode": "picker",
        "error_message": None,
        "stat_keys": _COMPARE_STAT_KEYS,
        "deltas": None,
        "points_series": None,
    }

    if not raw_a or not raw_b:
        return render(request, "matches/compare_rounds.html", context)

    # Coerce here so a non-numeric param (?round_a=abc) is a clean 404 rather
    # than a 500 from int() failing inside the ORM query.
    try:
        id_a, id_b = int(raw_a), int(raw_b)
    except (TypeError, ValueError):
        raise Http404("Invalid round id")

    round_a = get_object_or_404(GameRound, id=id_a)
    round_b = get_object_or_404(GameRound, id=id_b)
    context["round_a"] = round_a
    context["round_b"] = round_b

    if round_a.id == round_b.id:
        context["mode"] = "error"
        context["error_message"] = "Pick two different rounds to compare."
        return render(request, "matches/compare_rounds.html", context)

    team_ids = _shared_team_ids(round_a, round_b)
    if not team_ids:
        context["mode"] = "error"
        context["error_message"] = (
            "These rounds share no team, so there is nothing to compare."
        )
        return render(request, "matches/compare_rounds.html", context)

    points_series = []
    for team_id in team_ids:
        team = Team.objects.get(id=team_id)
        points_series.append(
            {
                "team_id": team_id,
                "team_name": team.name,
                "a": _cumulative_team_points(round_a, team_id),
                "b": _cumulative_team_points(round_b, team_id),
            }
        )

    context["mode"] = "compare"
    context["deltas"] = _player_stat_deltas(round_a, round_b, team_ids)
    context["points_series"] = points_series
    return render(request, "matches/compare_rounds.html", context)


def match_list(request):
    """Display all matches and standalone game rounds."""
    matches = (
        Match.objects.all()
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )
    detailed_rounds = (
        GameRound.objects.filter(match__isnull=True)
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )

    return render(
        request,
        "matches/match_list.html",
        {
            "matches": matches,
            "detailed_rounds": detailed_rounds,
        },
    )


def match_detail(request, match_id):
    """Display detailed match results with player stats"""
    match = get_object_or_404(Match, id=match_id)

    # Get detailed round data if available
    game_rounds = match.game_rounds.all().prefetch_related("player_states__player")

    context = {
        "match": match,
        "game_rounds": game_rounds,
        "round1_winner": (
            match.team_red
            if match.red_round1_points > match.blue_round1_points
            else match.team_blue
        ),
        "round2_winner": (
            match.team_red
            if match.red_round2_points > match.blue_round2_points
            else match.team_blue
        ),
    }

    return render(request, "matches/enhanced_match_detail.html", context)


def game_round_detail(request, round_id):
    """Display detailed single round results with player performance"""
    game_round = get_object_or_404(GameRound, id=round_id)

    # Get player performances grouped by team
    red_performances = (
        game_round.player_states.filter(player__team=game_round.team_red)
        .select_related("player")
        .order_by("-points_scored", "role", "player__name")
    )

    blue_performances = (
        game_round.player_states.filter(player__team=game_round.team_blue)
        .select_related("player")
        .order_by("-points_scored", "role", "player__name")
    )

    context = {
        "round": game_round,
        "red_performances": red_performances,
        "blue_performances": blue_performances,
    }

    return render(request, "matches/game_round_detail.html", context)


def create_match(request):
    """Set up and simulate a new match with detailed tracking"""
    if request.method == "POST":
        form = MatchSetupForm(request.POST)
        if form.is_valid():
            team_red = form.cleaned_data["team_red"]
            team_blue = form.cleaned_data["team_blue"]
            match_type = form.cleaned_data["match_type"]

            # Validate teams are different
            if team_red == team_blue:
                messages.error(request, "A team cannot play against itself!")
                return render(
                    request,
                    "matches/enhanced_match_setup.html",
                    {"form": form, "title": "Create Tournament Match"},
                )

            # Check if both teams have valid rosters
            red_errors = team_red.roster_errors
            if red_errors:
                messages.error(
                    request,
                    f"{team_red.name} has an invalid roster: {'; '.join(red_errors)}",
                )
                return render(
                    request,
                    "matches/enhanced_match_setup.html",
                    {"form": form, "title": "Create Tournament Match"},
                )

            blue_errors = team_blue.roster_errors
            if blue_errors:
                messages.error(
                    request,
                    f"{team_blue.name} has an invalid roster: {'; '.join(blue_errors)}",
                )
                return render(
                    request,
                    "matches/enhanced_match_setup.html",
                    {"form": form, "title": "Create Tournament Match"},
                )

            arena_map = form.cleaned_data.get("arena_map")
            simulator = BatchSimulator()
            try:
                match = simulator.simulate_match(
                    team_red, team_blue, match_type, arena_map=arena_map
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "matches/enhanced_match_setup.html",
                    {"form": form, "title": "Create Tournament Match"},
                )

            messages.success(
                request,
                f"Match simulated! {match.winner.name if match.winner else 'Tie'} won!",
            )
            return redirect("match_detail", match_id=match.id)
    else:
        form = MatchSetupForm()

    return render(
        request,
        "matches/enhanced_match_setup.html",
        {"form": form, "title": "Create Tournament Match"},
    )


def create_single_round(request):
    """Set up and simulate a detailed single round."""
    if request.method == "POST":
        form = SingleRoundSetupForm(request.POST)
        if form.is_valid():
            team_red = form.cleaned_data["team_red"]
            team_blue = form.cleaned_data["team_blue"]

            # Validate teams are different
            if team_red == team_blue:
                messages.error(request, "A team cannot play against itself!")
                return render(
                    request,
                    "matches/enhanced_single_round_setup.html",
                    {"form": form, "title": "Create Single Round"},
                )

            # Check if both teams have valid rosters
            red_errors = team_red.roster_errors
            if red_errors:
                messages.error(
                    request,
                    f"{team_red.name} has an invalid roster: {'; '.join(red_errors)}",
                )
                return render(
                    request,
                    "matches/enhanced_single_round_setup.html",
                    {"form": form, "title": "Create Single Round"},
                )

            blue_errors = team_blue.roster_errors
            if blue_errors:
                messages.error(
                    request,
                    f"{team_blue.name} has an invalid roster: {'; '.join(blue_errors)}",
                )
                return render(
                    request,
                    "matches/enhanced_single_round_setup.html",
                    {"form": form, "title": "Create Single Round"},
                )

            arena_map = form.cleaned_data.get("arena_map")
            simulator = BatchSimulator()
            try:
                game_round = simulator.simulate_single_round_detailed(
                    team_red, team_blue, arena_map=arena_map
                )
            except ValueError as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "matches/enhanced_single_round_setup.html",
                    {"form": form, "title": "Create Single Round"},
                )
            messages.success(
                request,
                f"Round complete! {game_round.winner.name if game_round.winner else 'Tie'} won!",
            )
            return redirect("game_round_detail", round_id=game_round.id)
    else:
        form = SingleRoundSetupForm()

    return render(
        request,
        "matches/enhanced_single_round_setup.html",
        {"form": form, "title": "Create Single Round"},
    )


def team_match_history(request, team_id):
    """Display match history for a specific team"""
    team = get_object_or_404(Team, id=team_id)

    matches = (
        Match.objects.filter(Q(team_red=team) | Q(team_blue=team))
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )

    detailed_rounds = (
        GameRound.objects.filter(
            Q(team_red=team) | Q(team_blue=team),
            match__isnull=True,
        )
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )

    # Calculate stats
    total_matches = matches.count()
    wins = matches.filter(winner=team).count()
    losses = matches.exclude(winner=team).exclude(winner=None).count()
    ties = matches.filter(winner=None).count()

    total_rounds = detailed_rounds.count()
    round_wins = detailed_rounds.filter(winner=team).count()
    round_losses = detailed_rounds.exclude(winner=team).exclude(winner=None).count()
    round_ties = detailed_rounds.filter(winner=None).count()

    # Player performance stats
    player_stats = []
    for player in team.players.all():
        performances = PlayerRoundState.objects.filter(player=player)
        if performances.exists():
            total_points = sum(p.points_scored for p in performances)
            total_tags = sum(p.tags_made for p in performances)
            total_deaths = sum(p.times_tagged for p in performances)
            games_played = performances.count()

            player_stats.append(
                {
                    "player": player,
                    "games_played": games_played,
                    "total_points": total_points,
                    "total_tags": total_tags,
                    "total_deaths": total_deaths,
                    "avg_points": (
                        total_points / games_played if games_played > 0 else 0
                    ),
                    "avg_tags": total_tags / games_played if games_played > 0 else 0,
                }
            )

    # Unique opponents (HX-03 entry point — one anchor per opponent, not per
    # match row; matches the seam contract's per-unique-opponent wording).
    opponent_ids: set[int] = set()
    unique_opponents: list[Team] = []
    for m in matches:
        opp = m.team_blue if m.team_red_id == team.id else m.team_red
        if opp.id not in opponent_ids:
            opponent_ids.add(opp.id)
            unique_opponents.append(opp)
    for r in detailed_rounds:
        opp = r.team_blue if r.team_red_id == team.id else r.team_red
        if opp.id not in opponent_ids:
            opponent_ids.add(opp.id)
            unique_opponents.append(opp)
    unique_opponents.sort(key=lambda t: t.name)

    context = {
        "team": team,
        "matches": matches,
        "detailed_rounds": detailed_rounds,
        "unique_opponents": unique_opponents,
        "player_stats": sorted(
            player_stats, key=lambda x: x["avg_points"], reverse=True
        ),
        "stats": {
            "total_matches": total_matches,
            "match_wins": wins,
            "match_losses": losses,
            "match_ties": ties,
            "total_rounds": total_rounds,
            "round_wins": round_wins,
            "round_losses": round_losses,
            "round_ties": round_ties,
        },
    }

    return render(request, "matches/team_history.html", context)


def simulate_batch(request):
    """Run N in-memory simulations asynchronously via Celery (API-03).

    GET → render the form. POST → validate, enqueue a Celery batch-sim task,
    and return JSON ``{job_id, team_red_id, team_red_name, team_blue_id,
    team_blue_name, arena_map_id, n}``. The client polls
    :func:`batch_simulate_status` for progress and final aggregate.
    """
    form = BatchSimulateForm(request.POST or None)
    context = {"form": form}

    if request.method == "POST" and form.is_valid():
        team_red = form.cleaned_data["team_red"]
        team_blue = form.cleaned_data["team_blue"]
        n = int(form.cleaned_data["n"])

        if team_red == team_blue:
            return JsonResponse(
                {"detail": "A team cannot play against itself!"}, status=400
            )

        for team, label in [(team_red, team_red.name), (team_blue, team_blue.name)]:
            errors = team.roster_errors
            if errors:
                return JsonResponse(
                    {"detail": (f"{label} has an invalid roster: {'; '.join(errors)}")},
                    status=400,
                )

        arena_map = form.cleaned_data.get("arena_map")
        arena_map_id = arena_map.id if arena_map else None
        team_red_id = team_red.id
        team_blue_id = team_blue.id

        async_result = simulate_batch_task.delay(
            team_red_id=team_red_id,
            team_blue_id=team_blue_id,
            n=n,
            arena_map_id=arena_map_id,
            master_seed=None,
        )

        return JsonResponse(
            {
                "job_id": async_result.id,
                "team_red_id": team_red_id,
                "team_red_name": team_red.name,
                "team_blue_id": team_blue_id,
                "team_blue_name": team_blue.name,
                "arena_map_id": arena_map_id,
                "n": n,
            }
        )

    return render(request, "matches/batch_simulate.html", context)


def batch_simulate_status(request, job_id):
    """API-03: return JSON status of a batch-simulate job from Celery.

    On the FIRST poll observing ``status == "complete"`` (guarded by a
    ``job_id`` marker inside ``request.session["batch_seeds"]``) also copies
    avg/outlier seeds into ``request.session`` so the existing
    :func:`save_batch_games` flow keeps working unchanged.

    ``team_red_id`` / ``team_blue_id`` / ``arena_map_id`` are carried by the
    JS poll as URL query params (POST response stashes them client-side).
    """
    async_result = AsyncResult(job_id)
    team_red_id = int_or_none(request.GET.get("team_red_id"))
    team_blue_id = int_or_none(request.GET.get("team_blue_id"))
    arena_map_id = int_or_none(request.GET.get("arena_map_id"))

    response = build_batch_status_response(
        async_result,
        team_red_id=team_red_id,
        team_blue_id=team_blue_id,
        arena_map_id=arena_map_id,
    )

    # SIM-10 session handover guard — preserved verbatim from the
    # pre-API-03 view. The "first poll observing complete" semantics
    # are unchanged; only the source of `aggregate` is now
    # async_result.result instead of _BATCH_JOBS[job_id]["partial"].
    if response["status"] == "complete":
        existing = request.session.get("batch_seeds") or {}
        if existing.get("job_id") != job_id:
            agg = response.get("partial") or {}
            request.session["batch_seeds"] = {
                "job_id": job_id,
                "team_red_id": team_red_id,
                "team_blue_id": team_blue_id,
                "arena_map_id": arena_map_id,
                "avg_seeds": agg.get("avg_seeds", []),
                "outlier_seeds": agg.get("outlier_seeds", []),
            }
            request.session.modified = True

    return JsonResponse(response)


def save_batch_games(request):
    """Start an async save of selected batch games via Celery; returns JSON {job_id}."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    seeds_data = request.session.get("batch_seeds")
    if not seeds_data:
        return JsonResponse(
            {"error": "No batch results found. Run a simulation first."}, status=400
        )

    game_type = request.POST.get("game_type")  # "avg" or "outlier"
    n = max(1, min(10, int(request.POST.get("n", 1))))

    raw = seeds_data.get("avg_seeds" if game_type == "avg" else "outlier_seeds", [])
    seeds = raw[:n]
    if not seeds:
        return JsonResponse({"error": "No saved seeds for this category."}, status=400)

    arena_map_id = seeds_data.get("arena_map_id")

    async_result = save_games_task.delay(
        team_red_id=seeds_data["team_red_id"],
        team_blue_id=seeds_data["team_blue_id"],
        seeds=seeds,
        n=n,
        arena_map_id=arena_map_id,
    )
    return JsonResponse({"job_id": async_result.id})


def save_batch_status(request, job_id):
    """Return JSON status of a save job from Celery."""
    async_result = AsyncResult(job_id)
    return JsonResponse(_build_save_status_response(async_result))


def game_round_events(request, round_id):
    """Display the detailed event log for a game round.

    M-1: every event is emitted **once** as a compact JSON list
    (``events_data``) instead of one server-rendered DOM row each. The
    template renders only a window of the timeline client-side and feeds
    the same JSON to the charts and the SIM-05 playback engine, so the
    page stays bounded regardless of round length (the old design emitted
    ~20k DOM nodes for a single round). Keep the per-event keys short —
    they are read directly by ``game_round_events.html``; the shape is
    pinned by ``TestM1EventLogWindowing``.
    """
    game_round = get_object_or_404(GameRound, id=round_id)

    events_qs = game_round.events.all().select_related(
        "actor", "target", "actor__team", "target__team"
    )
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

    players_data = [
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

    context = {
        "round": game_round,
        "events_data": events_data,
        "players_data": players_data,
        "event_summary": game_round.get_event_summary(),
        # RV-02: auto-flagged highlights (built at round completion). Coalesce
        # null (pre-RV-02 rounds) to [] so the template/JS always sees a list.
        "highlights_json": game_round.highlights_json or [],
    }

    return render(request, "matches/game_round_events.html", context)


def missile_log(request, round_id):
    """RES-03: render the per-round missile usage log.

    Filters ``GameEvent`` rows to the ``locking`` / ``missiled`` event-type
    pair (the post-RES-03 split of the legacy ``"missile"`` event), then
    computes a view-side fired / hit / efficiency summary. Friendly-fire
    hits count as hits.
    """
    game_round = get_object_or_404(GameRound, id=round_id)

    events = list(
        GameEvent.objects.filter(
            game_round=game_round,
            event_type__in=["locking", "missiled"],
        )
        .select_related("actor", "target")
        .order_by("timestamp")
    )

    # Only missiled rows render in the table; locking events are kept for
    # the count surface (and for any future "fired but never resolved"
    # column).
    missiled_events = [e for e in events if e.event_type == "missiled"]

    fired = len(missiled_events)
    hit = sum(1 for e in missiled_events if (e.metadata or {}).get("result") == "hit")
    efficiency = (hit / fired * 100.0) if fired else 0.0

    # Pre-compute display-friendly rows so the template stays declarative.
    rows = []
    for ev in missiled_events:
        meta = ev.metadata or {}
        ts = ev.timestamp or 0
        seconds_total = int(ts) // 2
        minutes = seconds_total // 60
        seconds = seconds_total % 60
        mmss = f"{minutes:02d}:{seconds:02d}"
        friendly = bool(meta.get("friendly_fire"))
        rows.append(
            {
                "event": ev,
                "timestamp_mmss": mmss,
                "actor_role": meta.get("actor_role", ""),
                "target_role": meta.get("target_role", ""),
                "result": meta.get("result", ""),
                "friendly_fire": friendly,
                "row_class": "missile-row friendly-fire" if friendly else "missile-row",
            }
        )

    context = {
        "round": game_round,
        "rows": rows,
        "fired": fired,
        "hit": hit,
        "efficiency": efficiency,
    }
    return render(request, "matches/missile_log.html", context)


def movement_heatmap(request, round_id: int):
    """RES-04: render the per-round movement heatmap page.

    Aggregates per-cell occupancy ticks across the round, surfaces the player
    roster for client-side filtering, and overlays the result as a canvas on
    the processed map image. When the round has no associated map, the
    template renders a "No map" notice instead.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    game_round = get_object_or_404(GameRound, pk=round_id)

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

    context = {
        "game_round": game_round,
        "cell_occupancy_json": game_round.cell_occupancy_json or {},
        "player_roster": player_roster,
        "has_map": has_map,
        "arena_map": arena_map,
        "zone_size": game_round.zone_size,
        "processed_image_url": processed_image_url,
    }
    return render(request, "matches/movement_heatmap.html", context)


def _player_row(state: PlayerRoundState) -> dict:
    """RV-03: build one player_row dict (frozen seam-contract key order) from a
    PlayerRoundState. `mvp` sources the get_mvp property, `accuracy` the
    get_accuracy property; the other 10 are IntegerFields."""
    return {
        "name": state.player.name,
        "role": state.role,
        "points_scored": state.points_scored,
        "mvp": state.get_mvp,
        "tags_made": state.tags_made,
        "times_tagged": state.times_tagged,
        "accuracy": state.get_accuracy,
        "final_lives": state.final_lives,
        "resupplies_given": state.resupplies_given,
        "missiles_landed": state.missiles_landed,
        "specials_used": state.specials_used,
        "follow_up_shots": state.follow_up_shots,
        "reaction_shots": state.reaction_shots,
        "combo_resupply_count": state.combo_resupply_count,
    }


def _team_totals(player_rows: list[dict], team_points: int) -> dict:
    """RV-03: summed per-team resource totals plus derived team values."""
    return {
        "resupplies_given": sum(p["resupplies_given"] for p in player_rows),
        "missiles_landed": sum(p["missiles_landed"] for p in player_rows),
        "specials_used": sum(p["specials_used"] for p in player_rows),
        "tags_made": sum(p["tags_made"] for p in player_rows),
        "survivors": sum(1 for p in player_rows if p["final_lives"] > 0),
        "team_points": team_points,
    }


def export_round_report(request, round_id: int):
    """RV-03: export a Round-report PDF for one GameRound (GET only).

    Assembles the report_data dict from the ORM (scoreboard ordering mirrors
    game_round_detail exactly) and hands it to the pure build_round_report
    builder; the watermark is gated on game_round.is_simulated.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    game_round = get_object_or_404(GameRound, pk=round_id)

    red_states = (
        game_round.player_states.filter(player__team=game_round.team_red)
        .select_related("player")
        .order_by("-points_scored", "role", "player__name")
    )
    blue_states = (
        game_round.player_states.filter(player__team=game_round.team_blue)
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

    report_data = {
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
        "red_totals": _team_totals(red_players, game_round.red_points),
        "blue_totals": _team_totals(blue_players, game_round.blue_points),
    }

    pdf_bytes = build_round_report(report_data, watermark=game_round.is_simulated)

    red_slug = slugify(game_round.team_red.name) or "red"
    blue_slug = slugify(game_round.team_blue.name) or "blue"

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="round-{round_id}-{red_slug}-vs-{blue_slug}.pdf"'
    )
    return response


# ---------------------------------------------------------------------------
# HX-03: head-to-head record
# ---------------------------------------------------------------------------


_H2H_PROVENANCE_VALUES: frozenset[str] = frozenset({"all", "real", "sim"})


def _parse_provenance(raw: str | None) -> str:
    """HX-03: return one of ``"all"`` / ``"real"`` / ``"sim"``.

    Anything else (``None``, ``""``, malformed) falls back to ``"all"``
    (HX-02 forgiving-fallback pattern).
    """
    if raw in _H2H_PROVENANCE_VALUES:
        return raw
    return "all"


def _parse_date(raw: str | None) -> date | None:
    """HX-03: parse a ``YYYY-MM-DD`` date.

    ``None`` / ``""`` / ``ValueError`` → ``None`` (silently treated as
    unbounded on that side).
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _h2h_matches_qs(
    team_a: Team,
    team_b: Team,
    provenance: str,
    date_from: date | None,
    date_to: date | None,
):
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
):
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


def head_to_head(request) -> HttpResponse:
    """HX-03: read-only head-to-head record between two Teams.

    Reads ``team_a`` / ``team_b`` from ``request.GET`` plus optional
    ``provenance`` / ``from`` / ``to`` filters. Routes through the four
    RV-01 modes (``picker`` / ``404`` / ``error`` / ``results``).
    """
    all_teams = Team.objects.all().order_by("name")
    raw_a = request.GET.get("team_a")
    raw_b = request.GET.get("team_b")
    provenance = _parse_provenance(request.GET.get("provenance"))
    date_from = _parse_date(request.GET.get("from"))
    date_to = _parse_date(request.GET.get("to"))

    context: dict = {
        "mode": "picker",
        "error_message": None,
        "team_a": None,
        "team_b": None,
        "all_teams": all_teams,
        "date_from": date_from,
        "date_to": date_to,
        "provenance": provenance,
    }

    # Surface the raw ids back into the picker selects so the user's prior
    # pick stays selected when one side is missing.
    try:
        preselect_a = int(raw_a) if raw_a else None
    except (TypeError, ValueError):
        preselect_a = None
    try:
        preselect_b = int(raw_b) if raw_b else None
    except (TypeError, ValueError):
        preselect_b = None
    context["preselect_a_id"] = preselect_a
    context["preselect_b_id"] = preselect_b

    if not raw_a or not raw_b:
        # Picker mode — resolve whichever side parsed cleanly so the picker
        # can re-render with the user's prior selection populated (test
        # ``test_picker_mode_only_team_a_param_renders_form_with_a_preselected_200``).
        if preselect_a is not None:
            context["team_a"] = Team.objects.filter(id=preselect_a).first()
        if preselect_b is not None:
            context["team_b"] = Team.objects.filter(id=preselect_b).first()
        return render(request, "matches/head_to_head.html", context)

    try:
        team_a_id = int(raw_a)
        team_b_id = int(raw_b)
    except (TypeError, ValueError):
        raise Http404("Invalid team id")

    team_a = get_object_or_404(Team, id=team_a_id)
    team_b = get_object_or_404(Team, id=team_b_id)
    context["team_a"] = team_a
    context["team_b"] = team_b

    if team_a.id == team_b.id:
        context["mode"] = "error"
        context["error_message"] = "Pick two different teams to compare."
        return render(request, "matches/head_to_head.html", context)

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

    match_record = h2h_stats.compute_match_record(matches_list, team_a.id, team_b.id)
    round_record = h2h_stats.compute_round_record(rounds_list)
    score_margin = h2h_stats.compute_score_margin(rounds_list)
    avg_survivors = h2h_stats.compute_avg_survivors(rounds_list)
    top_impactful = h2h_stats.top_impactful_per_team(
        player_rounds_list, team_a.id, team_b.id
    )
    per_map_breakdown = h2h_stats.compute_per_map_breakdown(rounds_list)
    detail_list = _build_detail_list(
        matches_list, rounds_list, match_lookup, team_a, team_b
    )
    margin_series_data = h2h_stats.margin_series(rounds_list)
    cumulative_wl_data = h2h_stats.cumulative_wl_series(rounds_list)

    context.update(
        {
            "mode": "results",
            "match_record": match_record,
            "round_record": round_record,
            "score_margin": score_margin,
            "avg_survivors": avg_survivors,
            "top_impactful": top_impactful,
            "per_map_breakdown": per_map_breakdown,
            "detail_list": detail_list,
            "margin_series": margin_series_data,
            "cumulative_wl_series": cumulative_wl_data,
        }
    )

    return render(request, "matches/head_to_head.html", context)


# ---------------------------------------------------------------------------
# HX-04: player head-to-head record
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
):
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


def player_head_to_head(request) -> HttpResponse:
    """HX-04: read-only head-to-head record between two Players.

    Reads ``player_a`` / ``player_b`` from ``request.GET`` plus optional
    ``role`` / ``provenance`` / ``from`` / ``to`` filters. Routes through
    four modes (``picker`` / ``404`` / ``error`` / ``results``); empty
    results render ``mode="results"`` with zeroed aggregates and the
    ``player-h2h-no-games-notice`` block.
    """
    all_players = Player.objects.select_related("team").order_by("team__name", "name")
    raw_a = request.GET.get("player_a")
    raw_b = request.GET.get("player_b")
    role = request.GET.get("role") or None
    provenance = _parse_provenance(request.GET.get("provenance"))
    date_from = _parse_date(request.GET.get("from"))
    date_to = _parse_date(request.GET.get("to"))

    context: dict = {
        "mode": "picker",
        "error_message": None,
        "player_a": None,
        "player_b": None,
        "all_players": all_players,
        "role": role,
        "provenance": provenance,
        "date_from": date_from,
        "date_to": date_to,
        "round_record": {"wins": 0, "losses": 0, "ties": 0, "n": 0},
        "score_margin": {"mean_margin": 0.0, "n": 0},
        "tag_stats": {
            "avg_tags_a_to_b": 0.0,
            "avg_tags_b_to_a": 0.0,
            "total_tags_a_to_b": 0,
            "total_tags_b_to_a": 0,
            "n": 0,
        },
        "per_role_breakdown": [],
        "per_map_breakdown": [],
        "detail_list": [],
        "margin_series": [],
        "cumulative_wl_series": [],
    }

    # Surface the raw ids back into the picker selects so the user's prior
    # pick stays selected when one side is missing.
    try:
        preselect_a = int(raw_a) if raw_a else None
    except (TypeError, ValueError):
        preselect_a = None
    try:
        preselect_b = int(raw_b) if raw_b else None
    except (TypeError, ValueError):
        preselect_b = None
    context["preselect_a_id"] = preselect_a
    context["preselect_b_id"] = preselect_b

    if not raw_a or not raw_b:
        if preselect_a is not None:
            context["player_a"] = Player.objects.filter(id=preselect_a).first()
        if preselect_b is not None:
            context["player_b"] = Player.objects.filter(id=preselect_b).first()
        return render(request, "matches/player_head_to_head.html", context)

    try:
        player_a_id = int(raw_a)
        player_b_id = int(raw_b)
    except (TypeError, ValueError):
        raise Http404("Invalid player id")

    player_a = get_object_or_404(Player, id=player_a_id)
    player_b = get_object_or_404(Player, id=player_b_id)
    context["player_a"] = player_a
    context["player_b"] = player_b

    if player_a.id == player_b.id:
        context["mode"] = "error"
        context["error_message"] = "Pick two different players to compare."
        return render(request, "matches/player_head_to_head.html", context)

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

    round_record = player_h2h_stats.compute_round_record(rounds_list)
    score_margin = player_h2h_stats.compute_score_margin(rounds_list)
    tag_stats = player_h2h_stats.compute_tag_stats(rounds_list)
    per_role_breakdown = player_h2h_stats.compute_per_role_breakdown(rounds_list)
    per_map_breakdown = player_h2h_stats.compute_per_map_breakdown(rounds_list)
    detail_list = _build_player_h2h_detail_list(rounds_list)
    margin_series_data = player_h2h_stats.margin_series(rounds_list)
    cumulative_wl_data = player_h2h_stats.cumulative_wl_series(rounds_list)

    context.update(
        {
            "mode": "results",
            "round_record": round_record,
            "score_margin": score_margin,
            "tag_stats": tag_stats,
            "per_role_breakdown": per_role_breakdown,
            "per_map_breakdown": per_map_breakdown,
            "detail_list": detail_list,
            "margin_series": margin_series_data,
            "cumulative_wl_series": cumulative_wl_data,
        }
    )

    return render(request, "matches/player_head_to_head.html", context)


# ====================================================================
# LG-01 — Season views
# ====================================================================


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


def season_standings(request, season_id: int) -> HttpResponse:
    """LG-01 — Standings page for a Season.

    Draft preview: when ``season.state == "draft"`` the page lists the
    enrolled teams sorted by computed ``team_overall`` (mean of the 6
    active-roster players' ``overall_rating``, 0.0 when no slots are
    filled), then by team name. Rows are emitted as zeroed
    ``StandingsRow``-shaped dicts so the template renders the same 9
    columns whether or not the Season has started.

    Active / completed: aggregates the Season's completed Matches via
    ``compute_standings``.
    """
    season = get_object_or_404(Season, pk=season_id)

    is_draft_preview = season.state == "draft"
    rows: list = []
    teams_by_id: dict[int, Team] = {}

    if is_draft_preview:
        teams = list(season.teams.all())
        teams.sort(key=lambda t: (-_compute_team_overall(t), t.name))
        teams_by_id = {t.id: t for t in teams}
        for index, team in enumerate(teams):
            rows.append(
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
                }
            )
    else:
        completed_qs = Match.objects.filter(season=season, is_completed=True)
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

        if season.starting_team_ids_json is not None:
            team_ids = list(season.starting_team_ids_json)
        else:
            team_ids = sorted(t.id for t in season.teams.all())

        enrolled_teams = list(
            Team.objects.filter(id__in=team_ids).values_list("id", "name")
        )
        rows = compute_standings(completed_matches, enrolled_teams)
        teams_by_id = {t.id: t for t in Team.objects.filter(id__in=team_ids)}

    def _row_team_id(row) -> int:
        if hasattr(row, "team_id"):
            return row.team_id
        return row["team_id"]

    rows_with_teams = [(row, teams_by_id.get(_row_team_id(row))) for row in rows]

    context = {
        "season": season,
        "rows": rows,
        "rows_with_teams": rows_with_teams,
        "is_draft_preview": is_draft_preview,
    }
    return render(request, "seasons/standings.html", context)


def season_schedule(request, season_id: int) -> HttpResponse:
    """LG-01 — Schedule page for a Season.

    Renders the deterministic fixture list from ``generate_schedule``
    with persisted ``GameRound``s overlaid. Fixtures are grouped by
    matchday; the display date for matchday ``n`` is
    ``season.start_date + (n - 1) * 7 days``.
    """
    season = get_object_or_404(Season, pk=season_id)

    if season.state == "draft":
        team_ids = sorted(t.id for t in season.teams.all())
    else:
        team_ids = list(season.starting_team_ids_json or [])

    if len(team_ids) < 2:
        # Cannot generate a schedule with fewer than 2 teams — render
        # an empty schedule with the empty-state notice.
        context = {"season": season, "matchdays": []}
        return render(request, "seasons/schedule.html", context)

    fixtures = generate_schedule(team_ids, season.schedule_format)

    teams_by_id: dict[int, Team] = {
        t.id: t for t in Team.objects.filter(id__in=team_ids)
    }

    # Index played GameRounds by (frozenset of team ids, round_number).
    rounds_qs = GameRound.objects.filter(match__season=season).select_related("match")
    played_by_key: dict[tuple[frozenset[int], int], GameRound] = {}
    for game_round in rounds_qs:
        match = game_round.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        key = (
            frozenset({match.team_red_id, match.team_blue_id}),
            game_round.round_number,
        )
        played_by_key[key] = game_round

    # Build per-fixture dicts.
    per_fixture: list[dict] = []
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
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

    context = {"season": season, "matchdays": matchdays}
    return render(request, "seasons/schedule.html", context)


def league_list(request) -> HttpResponse:
    """LG-01a — flat list of all Leagues (active + archived sections)."""
    active_leagues = list(League.objects.filter(state="active").order_by("-id"))
    archived_leagues = list(League.objects.filter(state="archived").order_by("-id"))
    return render(
        request,
        "leagues/list.html",
        {
            "active_leagues": active_leagues,
            "archived_leagues": archived_leagues,
        },
    )


@transaction.atomic
def league_create(request) -> HttpResponse:
    """LG-01b — Create-League flow.

    GET renders the empty form; POST validates the form, generates
    ``num_teams`` Teams (each with 6 Players) via the LG-00 generator,
    creates the League + draft Season, enrols the new Teams, and
    redirects to the Season standings view.
    """
    if request.method != "POST":
        return render(
            request,
            "leagues/create.html",
            {"form": CreateLeagueForm()},
        )

    form = CreateLeagueForm(request.POST)
    if not form.is_valid():
        return render(request, "leagues/create.html", {"form": form})

    cleaned = form.cleaned_data
    rng = random.Random()
    team_names_pool = list(TEAM_NAMES)
    player_names_pool = list(PLAYER_NAMES)

    created_teams = _generate_teams(
        cleaned["num_teams"],
        6,
        rng=rng,
        mean=cleaned["mean"],
        std_dev=cleaned["std_dev"],
        team_names_pool=team_names_pool,
        player_names_pool=player_names_pool,
    )

    league = League.objects.create(
        name=cleaned["league_name"],
        mode="league",
        state="active",
    )
    season = Season.objects.create(
        league=league,
        name=cleaned["season_name"],
        start_date=cleaned["start_date"],
        state="draft",
        schedule_format=cleaned["schedule_format"],
    )
    season.teams.add(*created_teams)

    return redirect("season_standings", season_id=season.id)


# ---------------------------------------------------------------------------
# LG-01c — League / Season dashboard
# ---------------------------------------------------------------------------


def _build_dashboard_context(
    displayed_season: Optional[Season], season_mode: str
) -> dict:
    """Shared body context for the League and Season dashboards.

    Returns the 11-key body context dict described by the LG-01c seam
    contract. Branches on ``season_mode`` to materialise the standings
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
        completed_qs = Match.objects.filter(season=displayed_season, is_completed=True)
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

        if displayed_season.starting_team_ids_json is not None:
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
        if len(team_ids) >= 2:
            fixtures = generate_schedule(team_ids, displayed_season.schedule_format)
        else:
            fixtures = []

        rounds_qs = GameRound.objects.filter(
            match__season=displayed_season
        ).select_related("match")
        played_keys: set = set()
        for game_round in rounds_qs:
            match = game_round.match
            if match is None or match.team_red_id is None or match.team_blue_id is None:
                continue
            played_keys.add(
                (
                    frozenset({match.team_red_id, match.team_blue_id}),
                    game_round.round_number,
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

        leaders_points = compute_leaders(player_rounds, "points_per_game", limit=3)
        leaders_tags = compute_leaders(player_rounds, "tags_per_game", limit=3)
        leaders_ratio = compute_leaders(player_rounds, "tag_ratio", limit=3)

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
        "action_button_label": action_button_label,
        "action_button_state": action_button_state,
    }


def league_dashboard(request, league_id: int) -> HttpResponse:
    """LG-01c — Dashboard for a single League.

    Picks one Season to display (active > most-recent completed > none),
    renders state badge + placeholder action button + top-3 standings +
    next round + round count + three leaders snippets.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    league = get_object_or_404(League, pk=league_id)

    active = league.active_season
    if active is not None:
        displayed_season = active
        season_mode = "draft" if active.state == "draft" else "active"
    else:
        completed_recent = (
            league.seasons.filter(state="completed").order_by("-id").first()
        )
        if completed_recent is not None:
            displayed_season = completed_recent
            season_mode = "completed"
        else:
            displayed_season = None
            season_mode = "none"

    body = _build_dashboard_context(displayed_season, season_mode)
    context = {
        "league": league,
        **body,
        "play_error": None,
        "play_job_id": None,
    }
    return render(request, "leagues/dashboard.html", context)


def season_dashboard(request, season_id: int) -> HttpResponse:
    """LG-01c — Dashboard for a single Season.

    Same body surface as the League dashboard plus a sidebar with live
    links to standings / schedule and disabled placeholders for Teams /
    History.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    season = get_object_or_404(Season, pk=season_id)
    displayed_season = season
    season_mode = season.state

    body = _build_dashboard_context(displayed_season, season_mode)
    sidebar_links = [
        {
            "key": "overview",
            "label": "Overview",
            "url": None,
            "disabled": False,
            "active": True,
        },
        {
            "key": "standings",
            "label": "Standings",
            "url": reverse("season_standings", args=[season.id]),
            "disabled": False,
            "active": False,
        },
        {
            "key": "schedule",
            "label": "Schedule",
            "url": reverse("season_schedule", args=[season.id]),
            "disabled": False,
            "active": False,
        },
        {
            "key": "teams",
            "label": "Teams",
            "url": None,
            "disabled": True,
            "active": False,
        },
        {
            "key": "history",
            "label": "History",
            "url": None,
            "disabled": True,
            "active": False,
        },
    ]
    context = {
        "season": season,
        **body,
        "sidebar_active": "overview",
        "sidebar_links": sidebar_links,
        "play_error": None,
        "play_job_id": None,
    }
    return render(request, "seasons/dashboard.html", context)


# ---------------------------------------------------------------------------
# LG-01d — Play Season views + helper
# ---------------------------------------------------------------------------


def _season_sidebar_links(season: Season) -> list[dict]:
    """LG-01c sidebar shape, reused on LG-01d error re-render paths."""
    return [
        {
            "key": "overview",
            "label": "Overview",
            "url": None,
            "disabled": False,
            "active": True,
        },
        {
            "key": "standings",
            "label": "Standings",
            "url": reverse("season_standings", args=[season.id]),
            "disabled": False,
            "active": False,
        },
        {
            "key": "schedule",
            "label": "Schedule",
            "url": reverse("season_schedule", args=[season.id]),
            "disabled": False,
            "active": False,
        },
        {
            "key": "teams",
            "label": "Teams",
            "url": None,
            "disabled": True,
            "active": False,
        },
        {
            "key": "history",
            "label": "History",
            "url": None,
            "disabled": True,
            "active": False,
        },
    ]


def _render_season_dashboard_error(
    request: "HttpRequest", season: Season, play_error: str
) -> HttpResponse:
    """LG-01d — re-render the Season dashboard with ``play_error`` set."""
    season_mode = season.state
    body = _build_dashboard_context(season, season_mode)
    context = {
        "season": season,
        **body,
        "sidebar_active": "overview",
        "sidebar_links": _season_sidebar_links(season),
        "play_error": play_error,
        "play_job_id": None,
    }
    return render(request, "seasons/dashboard.html", context, status=400)


def start_season(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the ``draft → active`` transition.

    POST only. Idempotent on the "already active" double-submit race —
    a ``ValidationError`` whose message contains the substring
    ``"non-completed"`` (the LG-01 ``Season.clean()`` wording) is
    swallowed and the user is redirected to the dashboard. Any other
    ``ValidationError`` re-renders the Season dashboard with
    ``play_error`` populated and HTTP 400.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)

    try:
        season.start_season()
    except ValidationError as exc:
        messages_list = getattr(exc, "messages", None) or [str(exc)]
        joined = " ".join(messages_list)
        season.refresh_from_db()
        if "non-completed" in joined or season.state == "active":
            return redirect("season_dashboard", season_id=season.id)
        return _render_season_dashboard_error(request, season, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def play_week(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for Play One Week (one matchday).

    Sync, single ``@transaction.atomic`` wrapping every Round in the
    next unplayed matchday. On a Season already finished (no unplayed
    fixtures) ⇒ idempotent 302 redirect. On a non-``active`` Season ⇒
    400 + ``play_error``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)

    if season.state != "active":
        return _render_season_dashboard_error(
            request,
            season,
            f"Season must be active to play; got state={season.state!r}",
        )

    try:
        with transaction.atomic():
            fixtures = generate_schedule(
                season.starting_team_ids_json or [], season.schedule_format
            )
            played_keys = {
                (
                    frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
                    gr.round_number,
                )
                for gr in GameRound.objects.filter(match__season=season).select_related(
                    "match"
                )
            }
            to_play = select_play_fixtures(fixtures, played_keys, 1)
            if not to_play:
                return redirect("season_dashboard", season_id=season.id)
            team_ids = {f.team_a_id for f in to_play} | {f.team_b_id for f in to_play}
            team_by_id = Team.objects.in_bulk(team_ids)
            for fixture in to_play:
                team_a = team_by_id[fixture.team_a_id]
                team_b = team_by_id[fixture.team_b_id]
                BatchSimulator().simulate_scheduled_round(
                    season, team_a, team_b, fixture.round_number
                )
    except (ValidationError, ValueError) as exc:
        return _render_season_dashboard_error(request, season, str(exc))

    return redirect("season_dashboard", season_id=season.id)


def play_two_months(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the Play Two Months async run.

    Validates the Season is ``active`` (else 400 + ``play_error``), then
    enqueues ``play_season_task.delay(season_id, max_matchdays=8)`` and
    returns ``JsonResponse({"job_id", "season_id"}, status=202)``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)

    if season.state != "active":
        return _render_season_dashboard_error(
            request,
            season,
            f"Season must be active to play; got state={season.state!r}",
        )

    result = play_season_task.delay(season.id, max_matchdays=8)
    return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)


def play_until_end(request, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the Play Until End of Season async run."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    season = get_object_or_404(Season, pk=season_id)

    if season.state != "active":
        return _render_season_dashboard_error(
            request,
            season,
            f"Season must be active to play; got state={season.state!r}",
        )

    result = play_season_task.delay(season.id, max_matchdays=None)
    return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)


def _build_play_status_response(
    async_result: AsyncResult,
    *,
    season_id: int,
) -> dict:
    """Assemble the locked 5-key polling JSON for a Play Season job.

    Returns ``{"status", "completed", "total", "error", "season_id"}``
    per the LG-01d seam contract §3.
    """
    state = async_result.state
    status = _celery_state_to_job_status(state)

    completed = 0
    total = 0
    error: str | None = None

    if state == "PROGRESS":
        info = async_result.info or {}
        if isinstance(info, dict):
            completed = int(info.get("completed", 0) or 0)
            total = int(info.get("total", 0) or 0)
    elif state == "SUCCESS":
        result = async_result.result or {}
        if isinstance(result, dict):
            completed = int(result.get("completed", 0) or 0)
            total = int(result.get("total", 0) or 0)
    elif state in ("FAILURE", "REVOKED"):
        info = async_result.info
        if info is not None:
            error = str(info)

    return {
        "status": status,
        "completed": completed,
        "total": total,
        "error": error,
        "season_id": season_id,
    }


def play_status(request, season_id: int, job_id: str) -> JsonResponse:
    """LG-01d — Shared polling endpoint for both async play tasks.

    GET only. Returns the locked 5-key polling JSON. The URL kwarg
    ``season_id`` is authoritative; any ``?season_id=`` query param is
    the carry pattern but the URL kwarg wins on disagreement.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    async_result = AsyncResult(job_id)
    return JsonResponse(_build_play_status_response(async_result, season_id=season_id))


@transaction.atomic
def next_season(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01e — POST entry point for the Start Next Season action.

    Creates a fresh ``draft`` Season inside ``league_id`` with copied
    teams from the latest completed Season's snapshot, an auto-generated
    name, and a Jan-1-next-year start date. Redirects to the new
    Season's dashboard on success.

    Guards (in order):
        1. 405 on non-POST.
        2. 404 on missing League.
        3. 302 redirect to ``season_dashboard`` of ``league.active_season``
           when a non-completed Season already exists (active-Season
           guard — idempotent on double-submit; the UI hides the
           button when a Season is in progress, but a stray POST
           lands the user on the in-progress Season's dashboard).
        4. 400 ``HttpResponseBadRequest("No completed Season in this League.")``
           when no completed Season exists (defensive — should never
           fire because the LG-01c button only shows when displayed
           Season is completed).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    league = get_object_or_404(League, pk=league_id)

    if league.active_season is not None:
        return redirect("season_dashboard", season_id=league.active_season.id)

    all_seasons = list(league.seasons.all())
    completed = [s for s in all_seasons if s.state == "completed"]
    if not completed:
        return HttpResponseBadRequest("No completed Season in this League.")
    latest_completed = max(completed, key=lambda s: s.id)

    name = f"Season {len(all_seasons) + 1}"
    start_date = date(latest_completed.start_date.year + 1, 1, 1)
    schedule_format = latest_completed.schedule_format

    new_season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format=schedule_format,
        state="draft",
    )

    team_ids = latest_completed.starting_team_ids_json or []
    if team_ids:
        teams_qs = Team.objects.filter(id__in=team_ids)
        new_season.teams.add(*teams_qs)

    return redirect("season_dashboard", season_id=new_season.id)
