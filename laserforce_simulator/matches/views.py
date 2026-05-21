import threading
import uuid

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.urls import reverse
from teams.models import Team
from .models import Match, GameRound, PlayerRoundState, GameEvent
from .simulation import BatchSimulator
from .forms import MatchSetupForm, SingleRoundSetupForm, BatchSimulateForm

# In-process job store for async save operations
_SAVE_JOBS: dict = {}
# SIM-10: in-process job store for async batch-simulate operations; shares
# the existing _JOBS_LOCK (no new lock).
_BATCH_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _run_save_job(
    job_id: str,
    team_red_id: int,
    team_blue_id: int,
    seeds,
    n: int,
    arena_map_id: int | None,
) -> None:
    """Background thread: replay and persist n games, then update job status.

    SIM-08: ``seeds`` is a list of ``[seed, flipped]`` pairs (session-stashed
    as JSON-safe lists). ``BatchSimulator.save_games`` unpacks each pair and
    persists the actual simulated sides.

    SIM-09: ``arena_map_id`` (``None`` for the 3-zone fallback) is resolved
    to an ``ArenaMap`` here and forwarded so the saved rounds carry the
    same map metadata batch-side simulation ran under. A stale id (the
    map was deleted between simulation and save) is treated as ``None``.
    """
    import django.db
    from core.models import ArenaMap

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        if arena_map_id is not None:
            try:
                arena_map = ArenaMap.objects.get(id=arena_map_id)
            except ArenaMap.DoesNotExist:
                arena_map = None
        else:
            arena_map = None
        game_rounds = BatchSimulator().save_games(
            team_red, team_blue, seeds, n, arena_map=arena_map
        )
        round_ids = [gr.id for gr in game_rounds]
        with _JOBS_LOCK:
            _SAVE_JOBS[job_id] = {
                "status": "done",
                "round_ids": round_ids,
                "error": None,
            }
    except Exception as exc:
        with _JOBS_LOCK:
            _SAVE_JOBS[job_id] = {"status": "error", "round_ids": [], "error": str(exc)}
    finally:
        django.db.close_old_connections()


def _run_batch_job(
    job_id: str,
    team_red_id: int,
    team_blue_id: int,
    n: int,
    arena_map_id: int | None,
    master_seed: int | None,
) -> None:
    """Background thread: iterate ``BatchSimulator.run_incremental``, updating
    ``_BATCH_JOBS[job_id]`` with the latest snapshot after each yield. On
    success sets ``status='complete'``; on exception sets ``status='error'``
    with ``str(exc)``.

    SIM-10: mirrors ``_run_save_job`` (try / ``with _JOBS_LOCK`` writes /
    ``finally: django.db.close_old_connections()``). Resolves the team and
    map FKs once at thread start; a stale ``arena_map_id`` (deleted between
    POST and thread start) is treated as ``None`` exactly like
    ``_run_save_job``.

    The production POST passes ``master_seed=None`` — the parameter is
    plumbed through for test pinning.
    """
    import django.db
    from core.models import ArenaMap

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        if arena_map_id is not None:
            try:
                arena_map = ArenaMap.objects.get(id=arena_map_id)
            except ArenaMap.DoesNotExist:
                arena_map = None
        else:
            arena_map = None

        for snap in BatchSimulator().run_incremental(
            team_red,
            team_blue,
            n,
            arena_map=arena_map,
            master_seed=master_seed,
        ):
            with _JOBS_LOCK:
                # Initial entry was inserted under the lock in
                # ``simulate_batch`` before this thread started; no
                # ``get(default={})`` fallback is needed.
                _BATCH_JOBS[job_id].update(
                    {
                        "status": "running",
                        "completed": snap["completed"],
                        "partial": snap["aggregate"],
                    }
                )

        with _JOBS_LOCK:
            _BATCH_JOBS[job_id].update(
                {
                    "status": "complete",
                    "completed": n,
                    "error": None,
                }
            )
    except Exception as exc:
        with _JOBS_LOCK:
            _BATCH_JOBS[job_id].update({"status": "error", "error": str(exc)})
    finally:
        django.db.close_old_connections()


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

    context = {
        "team": team,
        "matches": matches,
        "detailed_rounds": detailed_rounds,
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
    """Run N in-memory simulations asynchronously (SIM-10).

    GET → render the form. POST → validate, dispatch a background batch-sim
    job, and return JSON ``{job_id, team_red_id, team_red_name,
    team_blue_id, team_blue_name, arena_map_id, n}``. The client polls
    :func:`batch_simulate_status` for progress and final aggregate.
    """
    form = BatchSimulateForm(request.POST or None)
    context = {"form": form}

    if request.method == "POST" and form.is_valid():
        team_red = form.cleaned_data["team_red"]
        team_blue = form.cleaned_data["team_blue"]
        n = int(form.cleaned_data["n"])

        if team_red == team_blue:
            messages.error(request, "A team cannot play against itself!")
            return render(request, "matches/batch_simulate.html", context)

        for team, label in [(team_red, team_red.name), (team_blue, team_blue.name)]:
            errors = team.roster_errors
            if errors:
                messages.error(
                    request, f"{label} has an invalid roster: {'; '.join(errors)}"
                )
                return render(request, "matches/batch_simulate.html", context)

        arena_map = form.cleaned_data.get("arena_map")
        arena_map_id = arena_map.id if arena_map else None
        team_red_id = team_red.id
        team_blue_id = team_blue.id

        job_id = str(uuid.uuid4())
        with _JOBS_LOCK:
            _BATCH_JOBS[job_id] = {
                "status": "running",
                "completed": 0,
                "total": n,
                "partial": None,
                "error": None,
                "team_red_id": team_red_id,
                "team_blue_id": team_blue_id,
                "arena_map_id": arena_map_id,
            }

        thread = threading.Thread(
            target=_run_batch_job,
            args=(job_id, team_red_id, team_blue_id, n, arena_map_id, None),
            daemon=True,
        )
        thread.start()

        return JsonResponse(
            {
                "job_id": job_id,
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
    """SIM-10: return JSON status of a batch-simulate job.

    On the FIRST poll observing ``status == "complete"`` (guarded by a
    ``job_id`` marker inside ``request.session["batch_seeds"]``) also copies
    avg/outlier seeds into ``request.session`` so the existing
    :func:`save_batch_games` flow keeps working unchanged.

    Mirrors :func:`save_batch_status` — GET-by-convention (no method guard),
    returns ``JsonResponse({"status": "not_found"}, status=404)`` if the job
    is unknown.
    """
    with _JOBS_LOCK:
        if job_id not in _BATCH_JOBS:
            return JsonResponse({"status": "not_found"}, status=404)
        # Copy under the lock so the caller can serialise without races.
        job = dict(_BATCH_JOBS[job_id])

    if job.get("status") == "complete":
        existing = request.session.get("batch_seeds") or {}
        if existing.get("job_id") != job_id:
            agg = job.get("partial") or {}
            request.session["batch_seeds"] = {
                "job_id": job_id,
                "team_red_id": job.get("team_red_id"),
                "team_blue_id": job.get("team_blue_id"),
                "arena_map_id": job.get("arena_map_id"),
                "avg_seeds": agg.get("avg_seeds", []),
                "outlier_seeds": agg.get("outlier_seeds", []),
            }
            request.session.modified = True

    return JsonResponse(job)


def save_batch_games(request):
    """Start an async save of selected batch games; returns JSON {job_id}."""
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

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _SAVE_JOBS[job_id] = {"status": "running", "round_ids": [], "error": None}

    thread = threading.Thread(
        target=_run_save_job,
        args=(
            job_id,
            seeds_data["team_red_id"],
            seeds_data["team_blue_id"],
            seeds,
            n,
            arena_map_id,
        ),
        daemon=True,
    )
    thread.start()
    return JsonResponse({"job_id": job_id})


def save_batch_status(request, job_id):
    """Return JSON status of a save job."""
    with _JOBS_LOCK:
        job = dict(_SAVE_JOBS.get(job_id, {}))
    if not job:
        return JsonResponse({"status": "not_found"}, status=404)
    return JsonResponse(job)


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
