"""LG-02a — sandbox single-elimination Tournament views.

GET-driven list / create / detail surfaces plus three POST write endpoints
(reseed / lock / play-next). The bracket STRUCTURE lives in the pure
``matches.bracket`` module; these views are the ORM side of that seam.
"""

import random
from statistics import mean

from celery.result import AsyncResult
from kombu.exceptions import OperationalError
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.forms import RosterImportForm
from teams.models import Team
from teams.roster_importer import RosterImportError, parse_roster_csv
from teams.views import _apply_roster, _check_db_slot_collisions, _generate_teams

from matches.views import _celery_state_to_job_status

from .bracket import (
    advance_winner,
    break_tie,
    default_seed_order,
)
from .models import (
    BracketNode,
    Tournament,
    TournamentParticipant,
    _node_to_dict,
    count_series_wins,
)
from .simulation.entrypoints import BatchSimulator
from .tasks import play_tournament_task
from .tournament_engine import play_next_node


def _team_mean_rating(team: Team) -> float:
    """Mean active-player overall_rating for a Team (LG-01c draft-preview
    formula verbatim). 0.0 when the Team has no active players.
    """
    players = list(team.active_players)
    if not players:
        return 0.0
    return mean(p.overall_rating for p in players)


def tournament_list(request: HttpRequest) -> HttpResponse:
    """List all Tournaments newest-first."""
    tournaments = Tournament.objects.order_by("-id")
    return render(
        request,
        "matches/tournament_list.html",
        {"tournaments": tournaments},
    )


@transaction.atomic
def tournament_create(request: HttpRequest) -> HttpResponse:
    """GET -> render create form. POST valid -> create Tournament + participants
    with default Seeding, redirect to detail. POST invalid -> re-render (200).
    """
    available_teams = Team.objects.regular()

    if request.method != "POST":
        return render(
            request,
            "matches/tournament_create.html",
            {"form": None, "available_teams": available_teams},
        )

    name = (request.POST.get("name") or "").strip()
    selected_ids = request.POST.getlist("teams")
    try:
        generate_count = int(request.POST.get("generate_count") or 0)
    except (TypeError, ValueError):
        generate_count = 0
    try:
        generate_ppt = int(request.POST.get("generate_ppt") or 6)
    except (TypeError, ValueError):
        generate_ppt = 6

    errors = []
    if not name:
        errors.append("Tournament name is required.")

    teams: list[Team] = []
    if selected_ids:
        teams.extend(list(Team.objects.filter(id__in=selected_ids)))

    if generate_count and generate_count > 0:
        rng = random.Random()
        team_names_pool = list(TEAM_NAMES)
        player_names_pool = list(PLAYER_NAMES)
        generated = _generate_teams(
            generate_count,
            generate_ppt if generate_ppt > 0 else 6,
            rng=rng,
            mean=50,
            std_dev=15,
            team_names_pool=team_names_pool,
            player_names_pool=player_names_pool,
        )
        teams.extend(generated)

    if len(teams) < 4:
        errors.append("A tournament requires at least 4 teams.")

    if errors:
        for err in errors:
            messages.error(request, err)
        return render(
            request,
            "matches/tournament_create.html",
            {"form": None, "available_teams": available_teams},
        )

    # LG-02b-2 — four per-depth Series length slots (final / semifinal /
    # quarterfinal / earlier rounds), each int-coerced with a forgiving fallback
    # to 1 then forced into {1,3,5}. No monotonicity — the four are independent.
    def _parse_series_length(field: str) -> int:
        try:
            value = int(request.POST.get(field) or 1)
        except (TypeError, ValueError):
            value = 1
        if value not in (1, 3, 5):
            value = 1
        return value

    final_series_length = _parse_series_length("final_series_length")
    semifinal_series_length = _parse_series_length("semifinal_series_length")
    quarterfinal_series_length = _parse_series_length("quarterfinal_series_length")
    earlier_series_length = _parse_series_length("earlier_series_length")

    # LG-02c — bracket format. Forgiving fallback (mirrors the series-length
    # parses): only the two known formats are accepted, anything else (absent,
    # tampered) falls back to single-elimination.
    tournament_format = request.POST.get("format")
    if tournament_format not in ("single_elimination", "double_elimination"):
        tournament_format = "single_elimination"

    tournament = Tournament.objects.create(
        name=name,
        state="setup",
        format=tournament_format,
        final_series_length=final_series_length,
        semifinal_series_length=semifinal_series_length,
        quarterfinal_series_length=quarterfinal_series_length,
        earlier_series_length=earlier_series_length,
    )

    # Default Seeding via mean active-player overall_rating.
    team_ratings = [(t.id, _team_mean_rating(t)) for t in teams]
    seed_order = default_seed_order(team_ratings)
    team_by_id = {t.id: t for t in teams}
    for idx, team_id in enumerate(seed_order, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team_by_id[team_id], seed=idx
        )

    return redirect("tournament_detail", tournament_id=tournament.id)


def _build_rounds(tournament: Tournament) -> dict:
    """Group nodes into a 3-key dict (one slice per sub-bracket) for the tree
    render: ``{"winners": [...], "losers": [...], "grand_final": [...]}``, each
    slice a list of ``{bracket_round, nodes}`` ordered by round.

    For a single-elim Tournament ``"losers"`` and ``"grand_final"`` are empty
    lists and ``"winners"`` carries the whole tree (so the WB section renders
    exactly the old bracket).
    """
    nodes = list(
        tournament.nodes.select_related("team_a", "team_b", "winner")
        .prefetch_related("series_matches")
        .order_by("bracket_round", "position")
    )
    # by_section[bracket_type][bracket_round] -> list of node view-dicts.
    by_section: dict[str, dict[int, list]] = {
        "winners": {},
        "losers": {},
        "grand_final": {},
    }
    for node in nodes:
        # LG-02b — Series win counts per slot, derived from SeriesMatch rows.
        series_matches = list(node.series_matches.all())
        wins_a, wins_b = count_series_wins(
            series_matches, node.team_a_id, node.team_b_id
        )
        view_dict = {
            "bracket_round": node.bracket_round,
            "position": node.position,
            "bracket_type": node.bracket_type,
            "team_a": node.team_a,
            "team_b": node.team_b,
            "seed_a": node.seed_a,
            "seed_b": node.seed_b,
            "is_bye": node.is_bye,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "series_length": node.series_length,
            "series_matches": series_matches,
            "winner": node.winner,
        }
        section = by_section.setdefault(node.bracket_type, {})
        section.setdefault(node.bracket_round, []).append(view_dict)
    return {
        bt: [{"bracket_round": r, "nodes": rounds[r]} for r in sorted(rounds)]
        for bt, rounds in by_section.items()
    }


def _detail_context(tournament: Tournament) -> dict:
    """Shared tournament_detail context (LG-02a keys + LG-02a-2 import keys)."""
    participants = list(tournament.participants.select_related("team").order_by("seed"))
    rounds = _build_rounds(tournament)
    next_node = tournament.find_next_playable_node()
    return {
        "tournament": tournament,
        "participants": participants,
        "rounds": rounds,
        "next_node": next_node,
        "is_locked": tournament.is_locked,
        "can_play": tournament.state == "active" and next_node is not None,
        "import_form": RosterImportForm(),
        "import_row_errors": [],
    }


def tournament_detail(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Render the bracket tree + (in setup) the Seeding-edit form + play
    controls.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    return render(
        request, "matches/tournament_detail.html", _detail_context(tournament)
    )


@transaction.atomic
def tournament_reseed(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Persist a manually reordered Seeding (new seed ints from POST)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.is_locked:
        messages.error(request, "Seeding cannot be edited once the bracket is locked.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    participants = list(tournament.participants.all())
    # Collect (participant, new_seed) from POST keys seed_<team_id>.
    new_seeds: dict[int, int] = {}
    for participant in participants:
        raw = request.POST.get(f"seed_{participant.team_id}")
        if raw is None:
            continue
        try:
            new_seeds[participant.id] = int(raw)
        except (TypeError, ValueError):
            continue

    # Two-phase write to dodge the unique (tournament, seed) constraint: offset
    # every seed first, then write the final values.
    offset = 1000000
    for participant in participants:
        participant.seed = participant.seed + offset
        participant.save(update_fields=["seed"])
    for participant in participants:
        if participant.id in new_seeds:
            participant.seed = new_seeds[participant.id]
        else:
            participant.seed = participant.seed - offset
        participant.save(update_fields=["seed"])

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_lock(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Call ``tournament.lock_and_build()`` (setup->active, builds + persists
    nodes). On ValidationError redirect back with a flash.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    try:
        tournament.lock_and_build()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_play_next(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Find next playable node, sim ONE Match, resolve winner (incl. tie-break),
    Advance, stamp champion if final. Delegates to ``play_next_node``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.state != "active":
        messages.error(request, "The tournament is not active.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    node = play_next_node(tournament)
    if node is None:
        messages.error(request, "No playable match is ready.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_import_participants(
    request: HttpRequest, tournament_id: int
) -> HttpResponse:
    """Import participants from a roster CSV (LG-00b reuse). Setup-only.

    Only brand-new Teams (``created_teams``) become participants; appended
    Teams are created/extended but NOT auto-added. The whole field is then
    re-seeded by talent.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.is_locked:
        messages.error(request, "Participants can only be imported during setup.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(import_row_errors: list) -> HttpResponse:
        # Build the read-only detail context BEFORE flagging rollback — a
        # rolled-back atomic block forbids further queries, so set_rollback
        # must be the last DB-touching action before the render.
        ctx = _detail_context(tournament)
        ctx["import_form"] = form
        ctx["import_row_errors"] = import_row_errors
        transaction.set_rollback(True)
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])

    try:
        parsed = parse_roster_csv(form.cleaned_data["csv_file"])
        _check_db_slot_collisions(parsed)
        created_teams, _appended_teams, _player_count = _apply_roster(parsed)
    except RosterImportError as exc:
        return _render_error(exc.errors)

    # Only brand-new Teams become participants (no uniq collision possible).
    existing_team_ids = set(tournament.participants.values_list("team_id", flat=True))
    next_seed = tournament.participants.count() + 1
    for team in created_teams:
        if team.id in existing_team_ids:
            continue
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=next_seed
        )
        next_seed += 1

    # Re-seed the WHOLE field by talent.
    participants = list(tournament.participants.select_related("team"))
    team_ratings = [(p.team_id, _team_mean_rating(p.team)) for p in participants]
    seed_order = default_seed_order(team_ratings)
    seed_by_team = {team_id: idx for idx, team_id in enumerate(seed_order, start=1)}

    # Two-phase offset write to dodge the uniq (tournament, seed) constraint.
    offset = 1000000
    for participant in participants:
        participant.seed = participant.seed + offset
        participant.save(update_fields=["seed"])
    for participant in participants:
        participant.seed = seed_by_team[participant.team_id]
        participant.save(update_fields=["seed"])

    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_play_all(request: HttpRequest, tournament_id: int) -> JsonResponse:
    """Enqueue the async Play Tournament job. POST-only. 202 + job JSON."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.state != "active":
        return JsonResponse({"error": "Tournament is not active."}, status=409)

    try:
        # retry=False so an unreachable broker raises OperationalError after a
        # single bounded attempt instead of retry-hanging the request.
        result = play_tournament_task.apply_async((tournament_id,), retry=False)
    except OperationalError:
        # The Celery broker (Redis) is unreachable. Return a clean JSON error
        # (503) instead of a 500 HTML page so the UI can show a clear message
        # rather than a JSON-parse failure on the error page.
        return JsonResponse(
            {
                "error": (
                    "Couldn't start the Play All job — the background task "
                    "queue is unavailable. Start a Celery worker + broker, or "
                    "run the server with LF_CELERY_EAGER=1 for local play."
                )
            },
            status=503,
        )
    return JsonResponse(
        {"job_id": result.id, "tournament_id": tournament.id}, status=202
    )


def _build_tournament_play_status_response(
    async_result: AsyncResult, *, tournament_id: int
) -> dict:
    """Locked 5-key polling JSON for a Play Tournament job."""
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
        "tournament_id": tournament_id,
    }


def tournament_play_status(
    request: HttpRequest, tournament_id: int, job_id: str
) -> JsonResponse:
    """Poll a Play Tournament job. GET-only. Returns the locked 5-key JSON."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    get_object_or_404(Tournament, pk=tournament_id)
    async_result = AsyncResult(job_id)
    return JsonResponse(
        _build_tournament_play_status_response(
            async_result, tournament_id=tournament_id
        )
    )
