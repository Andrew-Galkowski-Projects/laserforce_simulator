from dataclasses import fields
from datetime import date, datetime

from celery.result import AsyncResult
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.urls import reverse
from accounts.permissions import (
    get_owned_or_404,
    manager_or_none,
    owned_game_round_q,
    owned_match_q,
    owned_queryset,
)
from teams.models import Team, Player
from . import round_comparison
from .job_status import celery_state_to_job_status
from .playback import build_playback_map as _build_playback_map
from .models import (
    Match,
    GameRound,
)
from .simulation import BatchSimulator
from .team_match_history import build_team_match_history
from .sim_helpers.pdf_report import build_round_report
from .forms import (
    MatchSetupForm,
    SingleRoundSetupForm,
    BatchSimulateForm,
)
from .services import (  # noqa: F401 - _comparison_row / _cumulative_team_points / _player_row / _round_players_data / round_playback_payload re-exported
    MatchSetupError,
    _comparison_row,
    _cumulative_team_points,
    _player_row,
    _round_players_data,
    build_heatmap,
    build_missile_log,
    build_player_h2h,
    build_round_comparison,
    build_round_report_data,
    build_team_h2h,
    check_matchup,
    create_sandbox_match,
    create_sandbox_round,
    round_playback_payload,
    round_report_filename,
    round_scoreboard,
    shared_team_ids,
)
from .tasks import save_games_task, simulate_batch_task

# The Celery-state mapper lives in ``matches.job_status`` (shared with the
# service layer); the old private name stays importable from here.
_celery_state_to_job_status = celery_state_to_job_status


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


def build_save_status_response(async_result: AsyncResult) -> dict:
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


# ANG-01b — renamed public; the old private name stays as an alias.
_build_save_status_response = build_save_status_response

# ANG-01b — the RV-01 helpers moved to `matches.services`; re-exported under
# their historical names so existing `from matches.views import ...` keeps working.
_shared_team_ids = shared_team_ids


def compare_rounds(request) -> HttpResponse:
    """RV-01: read-only side-by-side comparison of two GameRounds.

    Reads ``round_a``/``round_b`` GET params. Missing/empty → picker mode.
    Both present → fetch each (404 on invalid or non-numeric id); same round or
    no shared team → error mode (200). Distinct rounds sharing >=1 team → full
    comparison.
    """
    all_rounds = (
        GameRound.objects.filter(owned_game_round_q(request.user))
        .select_related("team_red", "team_blue")
        .order_by("-id")
    )

    raw_a = request.GET.get("round_a")
    raw_b = request.GET.get("round_b")

    context = {
        "round_a": None,
        "round_b": None,
        "all_rounds": all_rounds,
        "mode": "picker",
        "error_message": None,
        "stat_keys": list(round_comparison.COMPARE_STAT_KEYS),
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

    round_a = get_owned_or_404(GameRound, request, id=id_a)
    round_b = get_owned_or_404(GameRound, request, id=id_b)
    context["round_a"] = round_a
    context["round_b"] = round_b

    if round_a.id == round_b.id:
        context["mode"] = "error"
        context["error_message"] = "Pick two different rounds to compare."
        return render(request, "matches/compare_rounds.html", context)

    if not shared_team_ids(round_a, round_b):
        context["mode"] = "error"
        context["error_message"] = (
            "These rounds share no team, so there is nothing to compare."
        )
        return render(request, "matches/compare_rounds.html", context)

    comparison = build_round_comparison(round_a, round_b, request.user)
    context["mode"] = "compare"
    context["deltas"] = comparison["deltas"]
    context["points_series"] = comparison["points_series"]
    return render(request, "matches/compare_rounds.html", context)


def match_list(request):
    """Display all matches and standalone game rounds."""
    matches = (
        Match.objects.filter(owned_match_q(request.user))
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )
    detailed_rounds = (
        GameRound.objects.filter(match__isnull=True)
        .filter(owned_game_round_q(request.user))
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
    match = get_owned_or_404(Match, request, id=match_id)

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
    """Display detailed single round results with player performance.

    Materialises the same 28-key **Round scoreboard** dict the
    ``export_round_report`` PDF consumes (GLOSSARY.md, ``Round scoreboard``)
    so the HTML scoreboard and the PDF scoreboard cannot drift. ANG-06d: the
    rows come from ``round_scoreboard``, the builder ``GET /api/rounds/<id>/``
    serves as ``scoreboard``.
    """
    game_round = get_owned_or_404(GameRound, request, id=round_id)
    board = round_scoreboard(game_round)

    context = {
        "round": game_round,
        "red_players": board["red"]["players"],
        "blue_players": board["blue"]["players"],
        "red_survivors": board["red"]["survivors"],
        "blue_survivors": board["blue"]["survivors"],
        "red_eliminated": board["red"]["eliminated"],
        "blue_eliminated": board["blue"]["eliminated"],
    }

    return render(request, "matches/game_round_detail.html", context)


def create_match(request):
    """Set up and simulate a new match with detailed tracking"""
    if request.method == "POST":
        form = MatchSetupForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                match = create_sandbox_match(
                    form.cleaned_data["team_red"],
                    form.cleaned_data["team_blue"],
                    form.cleaned_data["match_type"],
                    arena_map=form.cleaned_data.get("arena_map"),
                    user=request.user,
                )
            except MatchSetupError as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "matches/enhanced_match_setup.html",
                    {"form": form, "title": "Create Match"},
                )

            messages.success(
                request,
                f"Match simulated! {match.winner.name if match.winner else 'Tie'} won!",
            )
            return redirect("match_detail", match_id=match.id)
    else:
        form = MatchSetupForm(user=request.user)

    return render(
        request,
        "matches/enhanced_match_setup.html",
        {"form": form, "title": "Create Match"},
    )


def create_single_round(request):
    """Set up and simulate a detailed single round."""
    if request.method == "POST":
        form = SingleRoundSetupForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                game_round = create_sandbox_round(
                    form.cleaned_data["team_red"],
                    form.cleaned_data["team_blue"],
                    arena_map=form.cleaned_data.get("arena_map"),
                    user=request.user,
                )
            except MatchSetupError as exc:
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
        form = SingleRoundSetupForm(user=request.user)

    return render(
        request,
        "matches/enhanced_single_round_setup.html",
        {"form": form, "title": "Create Single Round"},
    )


def team_match_history(request, team_id):
    """Display match history for a specific team"""
    team = get_owned_or_404(Team, request, id=team_id)

    # "applied" is a hidden marker on the toggle form, so an explicit "nothing
    # selected" (Overall toggled off) is distinguishable from a fresh page load
    # (which defaults to Overall = every scope on).
    scopes = set(request.GET.getlist("scope")) if "applied" in request.GET else None
    history = build_team_match_history(team, request.user, scopes)
    context = {field.name: getattr(history, field.name) for field in fields(history)}

    # The doughnut is HTML-only (ADR-0045 keeps the SPA line-only).
    context["winloss_chart"] = {
        key: getattr(history.stats, key)
        for key in (
            "match_wins",
            "match_losses",
            "match_ties",
            "round_wins",
            "round_losses",
            "round_ties",
        )
    }
    return render(request, "matches/team_history.html", context)


def simulate_batch(request):
    """Run N in-memory simulations asynchronously via Celery (API-03).

    GET → render the form. POST → validate, enqueue a Celery batch-sim task,
    and return JSON ``{job_id, team_red_id, team_red_name, team_blue_id,
    team_blue_name, arena_map_id, n}``. The client polls
    :func:`batch_simulate_status` for progress and final aggregate.
    """
    form = BatchSimulateForm(request.POST or None, user=request.user)
    context = {"form": form}

    if request.method == "POST" and form.is_valid():
        team_red = form.cleaned_data["team_red"]
        team_blue = form.cleaned_data["team_blue"]
        n = int(form.cleaned_data["n"])

        try:
            check_matchup(team_red, team_blue)
        except MatchSetupError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

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

    # ANG-01b — saved rounds are owned by the saver, not Unmanaged.
    async_result = save_games_task.delay(
        team_red_id=seeds_data["team_red_id"],
        team_blue_id=seeds_data["team_blue_id"],
        seeds=seeds,
        n=n,
        arena_map_id=arena_map_id,
        manager_id=getattr(manager_or_none(request), "pk", None),
    )
    return JsonResponse({"job_id": async_result.id})


def save_batch_status(request, job_id):
    """Return JSON status of a save job from Celery."""
    async_result = AsyncResult(job_id)
    return JsonResponse(build_save_status_response(async_result))


# RV-05 (#286): ``_round_players_data`` / ``round_playback_payload`` moved to
# ``matches.services`` (the report builder consumes them there); both stay
# importable from this module for their existing importers. ANG-06f moved
# ``_build_playback_map`` to ``matches.playback.build_playback_map`` (imported
# above under the old name for the Event log page and its tests).


def game_round_events(request: HttpRequest, round_id: int) -> HttpResponse:
    """Display the detailed event log for a game round.

    M-1: events are never server-rendered as one DOM row each; the timeline
    is windowed client-side. UXP-06: the events (and the map-playback
    routes) are no longer inlined into the page either — a 1,300-event
    round made the HTML ~600 KB. The page carries only the summary, the
    small player roster and the highlights; its script fetches the event
    list from ``game_round_events_data`` and feeds it to the timeline, the
    charts and the SIM-05 playback engine.
    """
    game_round = get_owned_or_404(GameRound, request, id=round_id)
    # GEN-01: this surface needs the full event log + movement + cell
    # occupancy. Lazily upgrade the round to ``full`` (no-op if already
    # there); the first click pays the re-sim + backfill once.
    BatchSimulator().ensure_fidelity(game_round, "full")

    # Map playback overlay: the routes arrive with the events JSON; the page
    # only needs to know whether to lay out the map stage.
    processed_image_url = (
        reverse("processed_image", args=[game_round.arena_map_id])
        if game_round.arena_map_id is not None
        else None
    )

    context = {
        "round": game_round,
        "events_data_url": reverse(
            "game_round_events_data", kwargs={"round_id": game_round.id}
        ),
        "players_data": _round_players_data(game_round),
        "event_summary": game_round.get_event_summary(),
        # RV-02: auto-flagged highlights (built at round completion). Coalesce
        # null (pre-RV-02 rounds) to [] so the template/JS always sees a list.
        "highlights_json": game_round.highlights_json or [],
        "processed_image_url": processed_image_url,
    }

    return render(request, "matches/game_round_events.html", context)


def game_round_events_data(request: HttpRequest, round_id: int) -> HttpResponse:
    """UXP-06: the Round Event Log's data, fetched by its page script.

    Returns ``{"events": [...], "playback_map": {...} | None}``: ``events``
    is the 13-key M-1 / SIM-05 event list from ``round_playback_payload``
    (chronological), ``playback_map`` the ``_build_playback_map`` routes
    (``None`` for a map-less round). GET only; a cross-Account round is 404.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    game_round: GameRound = get_owned_or_404(GameRound, request, id=round_id)
    BatchSimulator().ensure_fidelity(game_round, "full")
    events_data, _players = round_playback_payload(game_round)
    return JsonResponse(
        {"events": events_data, "playback_map": _build_playback_map(game_round)}
    )


def missile_log(request, round_id):
    """RES-03: render the per-round missile usage log.

    Delegates to ``matches.services.build_missile_log`` (which lazily upgrades
    the round to ``combat`` fidelity and summarises the ``missiled`` events
    into fired / hit / efficiency plus flat display rows). Friendly-fire hits
    count as hits.
    """
    game_round = get_owned_or_404(GameRound, request, id=round_id)
    context = {
        "round": game_round,
        **build_missile_log(game_round),
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

    game_round = get_owned_or_404(GameRound, request, pk=round_id)
    context = {
        "game_round": game_round,
        **build_heatmap(game_round),
    }
    return render(request, "matches/movement_heatmap.html", context)


def export_round_report(request, round_id: int):
    """RV-03: export a Round-report PDF for one GameRound (GET only).

    Assembles the report_data dict via ``build_round_report_data`` (scoreboard
    ordering mirrors game_round_detail exactly) and hands it to the pure
    build_round_report builder; the watermark is gated on
    game_round.is_simulated. RV-05: the round is upgraded to ``full``
    fidelity first, exactly as the events page does, so the chart section is
    drawn from the same events the page would show.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    game_round = get_owned_or_404(GameRound, request, pk=round_id)
    BatchSimulator().ensure_fidelity(game_round, "full")

    pdf_bytes = build_round_report(
        build_round_report_data(game_round), watermark=game_round.is_simulated
    )

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{round_report_filename(game_round)}"'
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


def head_to_head(request) -> HttpResponse:
    """HX-03: read-only head-to-head record between two Teams.

    Reads ``team_a`` / ``team_b`` from ``request.GET`` plus optional
    ``provenance`` / ``from`` / ``to`` filters. Routes through the four
    RV-01 modes (``picker`` / ``404`` / ``error`` / ``results``).
    """
    owned_teams = owned_queryset(Team.objects.all(), request.user)
    all_teams = owned_teams.order_by("name")
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
            context["team_a"] = owned_teams.filter(id=preselect_a).first()
        if preselect_b is not None:
            context["team_b"] = owned_teams.filter(id=preselect_b).first()
        return render(request, "matches/head_to_head.html", context)

    try:
        team_a_id = int(raw_a)
        team_b_id = int(raw_b)
    except (TypeError, ValueError):
        raise Http404("Invalid team id")

    team_a = get_owned_or_404(Team, request, id=team_a_id)
    team_b = get_owned_or_404(Team, request, id=team_b_id)
    context["team_a"] = team_a
    context["team_b"] = team_b

    if team_a.id == team_b.id:
        context["mode"] = "error"
        context["error_message"] = "Pick two different teams to compare."
        return render(request, "matches/head_to_head.html", context)

    context.update(
        {
            "mode": "results",
            **build_team_h2h(
                team_a,
                team_b,
                provenance=provenance,
                date_from=date_from,
                date_to=date_to,
            ),
        }
    )

    return render(request, "matches/head_to_head.html", context)


def player_head_to_head(request) -> HttpResponse:
    """HX-04: read-only head-to-head record between two Players.

    Reads ``player_a`` / ``player_b`` from ``request.GET`` plus optional
    ``role`` / ``provenance`` / ``from`` / ``to`` filters. Routes through
    four modes (``picker`` / ``404`` / ``error`` / ``results``); empty
    results render ``mode="results"`` with zeroed aggregates and the
    ``player-h2h-no-games-notice`` block.
    """
    owned_players = owned_queryset(
        Player.objects.select_related("team"), request.user, path="team"
    )
    all_players = owned_players.order_by("team__name", "name")
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
            context["player_a"] = owned_players.filter(id=preselect_a).first()
        if preselect_b is not None:
            context["player_b"] = owned_players.filter(id=preselect_b).first()
        return render(request, "matches/player_head_to_head.html", context)

    try:
        player_a_id = int(raw_a)
        player_b_id = int(raw_b)
    except (TypeError, ValueError):
        raise Http404("Invalid player id")

    player_a = get_owned_or_404(Player, request, id=player_a_id)
    player_b = get_owned_or_404(Player, request, id=player_b_id)
    context["player_a"] = player_a
    context["player_b"] = player_b

    if player_a.id == player_b.id:
        context["mode"] = "error"
        context["error_message"] = "Pick two different players to compare."
        return render(request, "matches/player_head_to_head.html", context)

    context.update(
        {
            "mode": "results",
            **build_player_h2h(
                player_a,
                player_b,
                role=role,
                provenance=provenance,
                date_from=date_from,
                date_to=date_to,
            ),
        }
    )

    return render(request, "matches/player_head_to_head.html", context)


# ====================================================================
# LG-01h — Shared placeholder view + feature registry
# ====================================================================

# Locked vocabulary per the LG-01h seam contract. 35 entries total:
# 10 League-scoped (3 LEAGUE + 3 TEAM + 4 reserved for LG-02), 6 PLAYERS,
# 6 STATS, 6 Help, 4 Tools. Note the contract says "League-scoped (10)"
# but enumerates only 3 (Playoffs / Finances / Power Rankings) — the
# Team-scoped 3 are listed separately, and Standings / Schedule /
# Schedule_team / History are already LIVE in LG-01f/g (not in this
# registry). Total entries below: 3 + 3 + 6 + 6 + 6 + 4 = 28… plus the
# 7 sidebar-active enum entries needed for forward-compat. Counted
# literally: 3 League + 3 Team + 6 Players + 6 Stats + 6 Help + 4 Tools
# = 28 — but the contract pins "Total: 35" — see PLAN.md grilling notes.
# Source of truth is the contract's enumeration in §"Shared placeholder
# view" which lists exactly the keys below. Here we ship every key
# enumerated literally; the count discrepancy in the contract preamble
# is acknowledged in the agent task notes.
_FEATURE_REGISTRY: dict[str, dict[str, str | None]] = {
    # League-scoped placeholders still blocked on unbuilt models.
    # LG-01z made Power Rankings live and FIN-01 made League / Team Finances
    # live; the rest carry a ``blocker`` note rendered on the explainer page.
    # Players-scoped placeholders (Free Agents + Watch List went live).
    "players_trade": {
        "label": "Trade",
        "section": "players",
        "sidebar_active": "trade",
        "blocker": (
            "Needs cap-space validation from the salary / contract "
            "model — not yet built."
        ),
    },
    "players_trading_block": {
        "label": "Trading Block",
        "section": "players",
        "sidebar_active": "trading_block",
        "blocker": (
            "Needs a Player.on_trading_block field and the Trade "
            "builder — not yet built."
        ),
    },
    "players_prospects": {
        "label": "Prospects",
        "section": "players",
        "sidebar_active": "prospects",
        "blocker": "Needs the LG-05 player-potential model — not yet built.",
    },
    "players_hall_of_fame": {
        "label": "Hall of Fame",
        "section": "players",
        "sidebar_active": "hall_of_fame",
        "blocker": (
            "Needs LG-03 season-end awards + LG-04 stat updates — " "not yet built."
        ),
    },
    # Help (6) — sandbox-mode, no sidebar
    "help_overview": {
        "label": "Overview",
        "section": "help",
        "sidebar_active": None,
    },
    "help_changes": {
        "label": "Changes",
        "section": "help",
        "sidebar_active": None,
    },
    "help_custom_rosters": {
        "label": "Custom Rosters",
        "section": "help",
        "sidebar_active": None,
    },
    "help_debugging": {
        "label": "Debugging",
        "section": "help",
        "sidebar_active": None,
    },
    "help_lol_gm_forums": {
        "label": "LOL GM Forums",
        "section": "help",
        "sidebar_active": None,
    },
    "help_zen_gm_forums": {
        "label": "Zen GM Forums",
        "section": "help",
        "sidebar_active": None,
    },
    # Tools (4) — sandbox-mode, no sidebar
    "tools_achievements": {
        "label": "Achievements",
        "section": "tools",
        "sidebar_active": None,
    },
    "tools_screenshot": {
        "label": "Screenshot",
        "section": "tools",
        "sidebar_active": None,
    },
    "tools_debug_mode": {
        "label": "Enable Debug Mode",
        "section": "tools",
        "sidebar_active": None,
    },
    "tools_reset_db": {
        "label": "Reset DB",
        "section": "tools",
        "sidebar_active": None,
    },
}


def coming_soon(
    request: HttpRequest,
    feature_key: str,
    league_id: int | None = None,
    team_id: int | None = None,
) -> HttpResponse:
    """LG-01h — single shared placeholder view for every disabled link.

    Routes from ``coming_soon_*`` URL names dispatch here. The view
    renders ``_placeholder.html`` with the feature's label / section /
    sidebar-active enum (looked up against ``_FEATURE_REGISTRY``); the
    sidebar partial is included only when ``league_id`` is provided
    (League / Team / Players / Stats placeholders). Help / Tools
    placeholders render in sandbox mode with no sidebar.

    The ``team_id`` kwarg is reserved for forward-compat with LG-02+
    per-Team routes and currently unused.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    # Local imports keep the module-level import surface lean and avoid
    # a circular import path (league_views imports from .views).
    from .models import League
    from .league_views import _build_league_sidebar_links

    if league_id is not None:
        league = get_owned_or_404(League, request, pk=league_id)
    else:
        league = None

    feature = _FEATURE_REGISTRY.get(feature_key)
    if feature is None:
        raise Http404(f"Unknown placeholder feature {feature_key!r}.")

    sidebar_active = feature["sidebar_active"]

    if league is not None:
        # Session-pin the League id so the top-bar dropdown URLs stay
        # fresh as the user clicks through placeholder pages.
        try:
            request.session["last_league_id"] = league.id
        except AttributeError:
            # RequestFactory-built requests with no session middleware.
            pass

        displayed_season = (
            league.active_season
            or league.seasons.filter(state="completed").order_by("-id").first()
        )
        sidebar_links = _build_league_sidebar_links(
            league, displayed_season, sidebar_active
        )
    else:
        displayed_season = None
        sidebar_links = []

    context = {
        "league": league,
        "displayed_season": displayed_season,
        "feature_key": feature_key,
        "feature_label": feature["label"],
        "feature_section": feature["section"],
        "blocker": feature.get("blocker"),
        "sidebar_links": sidebar_links,
        "sidebar_active": sidebar_active,
    }
    return render(request, "_placeholder.html", context)
