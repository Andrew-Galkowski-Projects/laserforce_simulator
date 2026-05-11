import json
import threading
import uuid

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from teams.models import Team
from .models import Match, GameRound, PlayerRoundState
from .simulation import ResourceBasedSimulator, BatchSimulator
from .forms import MatchSetupForm, SingleRoundSetupForm, BatchSimulateForm

# In-process job store for async save operations
_SAVE_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()


def _serialize_seeds(seeds):
    """Convert random state tuples to JSON-serialisable lists."""
    result = []
    for state in seeds:
        v, internal, gauss = state
        result.append([v, list(internal), gauss])
    return result


def _deserialize_seeds(data):
    """Restore random state tuples from serialised lists."""
    result = []
    for item in data:
        v, internal, gauss = item
        result.append((v, tuple(internal), gauss))
    return result


def _run_save_job(job_id, team_red_id, team_blue_id, seeds, n):
    """Background thread: replay and persist n games, then update job status."""
    import django.db

    try:
        team_red = Team.objects.get(id=team_red_id)
        team_blue = Team.objects.get(id=team_blue_id)
        game_rounds = BatchSimulator().save_games(team_red, team_blue, seeds, n)
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
            simulator = ResourceBasedSimulator()
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
            simulator = ResourceBasedSimulator()
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
    """Run N in-memory simulations and display aggregate statistics."""
    import time

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

        t0 = time.time()
        results = BatchSimulator().run(team_red, team_blue, n)
        elapsed = time.time() - t0

        # Stash seeds in session for the save-games views (strip from template context)
        avg_seeds = results.pop("avg_seeds", [])
        outlier_seeds = results.pop("outlier_seeds", [])
        request.session["batch_seeds"] = {
            "team_red_id": team_red.id,
            "team_blue_id": team_blue.id,
            "avg_seeds": _serialize_seeds(avg_seeds),
            "outlier_seeds": _serialize_seeds(outlier_seeds),
        }

        # Build histogram bins (5 000-point buckets)
        all_scores = results["red_scores"] + results["blue_scores"]
        if all_scores:
            max_score = max(all_scores)
            bin_size = 5000
            num_bins = max(1, (max_score // bin_size) + 1)
            bins = [i * bin_size for i in range(num_bins + 1)]
            red_hist = [0] * num_bins
            blue_hist = [0] * num_bins
            for s in results["red_scores"]:
                idx = min(int(s // bin_size), num_bins - 1)
                red_hist[idx] += 1
            for s in results["blue_scores"]:
                idx = min(int(s // bin_size), num_bins - 1)
                blue_hist[idx] += 1
            bin_labels = [f"{b:,}–{b+bin_size:,}" for b in bins[:-1]]
        else:
            bin_labels = red_hist = blue_hist = []

        context.update(
            {
                "results": results,
                "team_red": team_red,
                "team_blue": team_blue,
                "elapsed": round(elapsed, 2),
                "bin_labels_json": json.dumps(bin_labels),
                "red_hist_json": json.dumps(red_hist),
                "blue_hist_json": json.dumps(blue_hist),
                "has_seeds": bool(avg_seeds or outlier_seeds),
            }
        )

    return render(request, "matches/batch_simulate.html", context)


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
    seeds = _deserialize_seeds(raw[:n])
    if not seeds:
        return JsonResponse({"error": "No saved seeds for this category."}, status=400)

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _SAVE_JOBS[job_id] = {"status": "running", "round_ids": [], "error": None}

    thread = threading.Thread(
        target=_run_save_job,
        args=(job_id, seeds_data["team_red_id"], seeds_data["team_blue_id"], seeds, n),
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
    """Display detailed event log for a game round"""
    game_round = get_object_or_404(GameRound, id=round_id)

    # Get all events
    events = game_round.events.all().select_related("actor", "target")

    # Event summary
    event_summary = game_round.get_event_summary()

    # Get kill feed (tags and eliminations only)
    kill_feed = game_round.get_kill_feed()

    context = {
        "round": game_round,
        "events": events,
        "event_summary": event_summary,
        "kill_feed": kill_feed,
    }

    return render(request, "matches/game_round_events.html", context)
