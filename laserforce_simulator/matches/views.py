from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.db.models import Q
from teams.models import Team
from .models import Match, SingleRound
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

    return render(
        request,
        "matches/match_list.html",
        {"matches": matches, "single_rounds": single_rounds},
    )


def match_detail(request, match_id):
    """Display detailed match results"""
    match = get_object_or_404(Match, id=match_id)

    context = {
        "match": match,
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

    return render(request, "matches/match_detail.html", context)


def single_round_detail(request, round_id):
    """Display single round results"""
    single_round = get_object_or_404(SingleRound, id=round_id)
    return render(request, "matches/single_round_detail.html", {"round": single_round})


def create_match(request):
    """Set up and simulate a new match"""
    if request.method == "POST":
        form = MatchSetupForm(request.POST)
        if form.is_valid():
            team_red = form.cleaned_data["team_red"]
            team_blue = form.cleaned_data["team_blue"]
            match_type = form.cleaned_data["match_type"]

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

            # Simulate the match
            from .simulation import SimpleMatchSimulator

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
        "matches/match_setup.html",
        {"form": form, "title": "Create Tournament Match"},
    )


def create_single_round(request):
    """Set up and simulate a single round"""
    if request.method == "POST":
        form = SingleRoundSetupForm(request.POST)
        if form.is_valid():
            team_red = form.cleaned_data["team_red"]
            team_blue = form.cleaned_data["team_blue"]

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

            # Simulate the round
            from .simulation import SimpleMatchSimulator

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
        "matches/single_round_setup.html",
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

    # Calculate stats
    total_matches = matches.count()
    wins = matches.filter(winner=team).count()
    losses = matches.exclude(winner=team).exclude(winner=None).count()
    ties = matches.filter(winner=None).count()

    total_rounds = single_rounds.count()
    round_wins = single_rounds.filter(winner=team).count()
    round_losses = single_rounds.exclude(winner=team).exclude(winner=None).count()
    round_ties = single_rounds.filter(winner=None).count()

    context = {
        "team": team,
        "matches": matches,
        "single_rounds": single_rounds,
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
