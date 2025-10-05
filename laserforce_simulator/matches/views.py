from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Q
from teams.models import Team
from .models import Match, SingleRound, GameRound, PlayerRoundState
from .simulation import ResourceBasedSimulator, SimpleMatchSimulator
from .forms import MatchSetupForm, SingleRoundSetupForm


def match_list(request):
    """Display all matches and single rounds"""
    matches = (
        Match.objects.all()
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )
    single_rounds = (
        SingleRound.objects.all()
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
            "single_rounds": single_rounds,
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
        .order_by("-points_scored", "player__role", "player__name")
    )

    blue_performances = (
        game_round.player_states.filter(player__team=game_round.team_blue)
        .select_related("player")
        .order_by("-points_scored", "player__role", "player__name")
    )

    context = {
        "round": game_round,
        "red_performances": red_performances,
        "blue_performances": blue_performances,
    }

    return render(request, "matches/game_round_detail.html", context)


def single_round_detail(request, round_id):
    """Display single round results (legacy)"""
    single_round = get_object_or_404(SingleRound, id=round_id)
    return render(request, "matches/single_round_detail.html", {"round": single_round})


def create_match(request):
    """Set up and simulate a new match with detailed tracking"""
    if request.method == "POST":
        form = MatchSetupForm(request.POST)
        if form.is_valid():
            team_red = form.cleaned_data["team_red"]
            team_blue = form.cleaned_data["team_blue"]
            match_type = form.cleaned_data["match_type"]
            use_detailed = form.cleaned_data.get("use_detailed_simulation", True)

            # Validate teams are different
            if team_red == team_blue:
                messages.error(request, "A team cannot play against itself!")
                return render(request, "matches/match_setup.html", {"form": form})

            # Check if both teams have valid rosters
            if not team_red.is_valid_roster:
                messages.error(
                    request, f"{team_red.name} does not have a valid roster!"
                )
                return render(request, "matches/match_setup.html", {"form": form})

            if not team_blue.is_valid_roster:
                messages.error(
                    request, f"{team_blue.name} does not have a valid roster!"
                )
                return render(request, "matches/match_setup.html", {"form": form})

            # Choose simulator
            if use_detailed:
                simulator = ResourceBasedSimulator()
            else:
                simulator = SimpleMatchSimulator()

            match = simulator.simulate_match(team_red, team_blue, match_type)

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
    """Set up and simulate a detailed single round"""
    if request.method == "POST":
        form = SingleRoundSetupForm(request.POST)
        if form.is_valid():
            team_red = form.cleaned_data["team_red"]
            team_blue = form.cleaned_data["team_blue"]
            use_detailed = form.cleaned_data.get("use_detailed_simulation", True)

            # Validate teams are different
            if team_red == team_blue:
                messages.error(request, "A team cannot play against itself!")
                return render(
                    request, "matches/single_round_setup.html", {"form": form}
                )

            # Check if both teams have valid rosters
            if not team_red.is_valid_roster:
                messages.error(
                    request, f"{team_red.name} does not have a valid roster!"
                )
                return render(
                    request, "matches/single_round_setup.html", {"form": form}
                )

            if not team_blue.is_valid_roster:
                messages.error(
                    request, f"{team_blue.name} does not have a valid roster!"
                )
                return render(
                    request, "matches/single_round_setup.html", {"form": form}
                )

            if use_detailed:
                # Use new detailed simulation
                simulator = ResourceBasedSimulator()
                game_round = simulator.simulate_single_round_detailed(
                    team_red, team_blue
                )
                messages.success(
                    request,
                    f"Round complete! {game_round.winner.name if game_round.winner else 'Tie'} won!",
                )
                return redirect("game_round_detail", round_id=game_round.id)
            else:
                # Use legacy simple simulation
                simulator = SimpleMatchSimulator()
                single_round = simulator.simulate_single_round(team_red, team_blue)
                messages.success(
                    request,
                    f"Round complete! {single_round.winner.name if single_round.winner else 'Tie'} won!",
                )
                return redirect("single_round_detail", round_id=single_round.id)
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

    single_rounds = (
        SingleRound.objects.filter(Q(team_red=team) | Q(team_blue=team))
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )

    detailed_rounds = (
        GameRound.objects.filter(
            Q(team_red=team) | Q(team_blue=team),
            match__isnull=True,  # Only standalone rounds
        )
        .select_related("team_red", "team_blue", "winner")
        .order_by("-date_played")
    )

    # Calculate stats
    total_matches = matches.count()
    wins = matches.filter(winner=team).count()
    losses = matches.exclude(winner=team).exclude(winner=None).count()
    ties = matches.filter(winner=None).count()

    total_rounds = single_rounds.count() + detailed_rounds.count()
    round_wins = (
        single_rounds.filter(winner=team).count()
        + detailed_rounds.filter(winner=team).count()
    )
    round_losses = (
        single_rounds.exclude(winner=team).exclude(winner=None).count()
        + detailed_rounds.exclude(winner=team).exclude(winner=None).count()
    )
    round_ties = (
        single_rounds.filter(winner=None).count()
        + detailed_rounds.filter(winner=None).count()
    )

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
        "single_rounds": single_rounds,
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
