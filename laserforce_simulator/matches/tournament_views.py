"""LG-02a — sandbox single-elimination Tournament views.

GET-driven list / create / detail surfaces plus three POST write endpoints
(reseed / lock / play-next). The bracket STRUCTURE lives in the pure
``matches.bracket`` module; these views are the ORM side of that seam.
"""

import random
from statistics import mean

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render

from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.models import Team
from teams.views import _generate_teams

from .bracket import (
    advance_winner,
    break_tie,
    default_seed_order,
)
from .models import BracketNode, Tournament, TournamentParticipant, _node_to_dict
from .simulation.entrypoints import BatchSimulator


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

    tournament = Tournament.objects.create(name=name, state="setup")

    # Default Seeding via mean active-player overall_rating.
    team_ratings = [(t.id, _team_mean_rating(t)) for t in teams]
    seed_order = default_seed_order(team_ratings)
    team_by_id = {t.id: t for t in teams}
    for idx, team_id in enumerate(seed_order, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team_by_id[team_id], seed=idx
        )

    return redirect("tournament_detail", tournament_id=tournament.id)


def _build_rounds(tournament: Tournament) -> list[dict]:
    """Group nodes into a list of {bracket_round, nodes} for the tree render."""
    nodes = list(
        tournament.nodes.select_related("team_a", "team_b", "match", "winner").order_by(
            "bracket_round", "position"
        )
    )
    by_round: dict[int, list] = {}
    for node in nodes:
        view_dict = {
            "bracket_round": node.bracket_round,
            "position": node.position,
            "team_a": node.team_a,
            "team_b": node.team_b,
            "seed_a": node.seed_a,
            "seed_b": node.seed_b,
            "is_bye": node.is_bye,
            "match": node.match,
            "winner": node.winner,
        }
        by_round.setdefault(node.bracket_round, []).append(view_dict)
    return [{"bracket_round": r, "nodes": by_round[r]} for r in sorted(by_round)]


def tournament_detail(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Render the bracket tree + (in setup) the Seeding-edit form + play
    controls.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    participants = list(tournament.participants.select_related("team").order_by("seed"))
    rounds = _build_rounds(tournament)
    next_node = tournament.find_next_playable_node()
    context = {
        "tournament": tournament,
        "participants": participants,
        "rounds": rounds,
        "next_node": next_node,
        "is_locked": tournament.is_locked,
        "can_play": tournament.state == "active" and next_node is not None,
    }
    return render(request, "matches/tournament_detail.html", context)


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


@transaction.atomic
def tournament_play_next(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Find next playable node, sim ONE Match, resolve winner (incl. tie-break),
    Advance, stamp champion if final.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_object_or_404(Tournament, pk=tournament_id)
    if tournament.state != "active":
        messages.error(request, "The tournament is not active.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    node = tournament.find_next_playable_node()
    if node is None:
        messages.error(request, "No playable match is ready.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    # 1. Simulate one Match (team_a plays red, team_b plays blue).
    match = BatchSimulator().simulate_match(
        node.team_a, node.team_b, match_type="tournament"
    )
    node.match = match

    # 2-4. Resolve winner (with tie-break on a true tie).
    winner_team = match.winner
    if winner_team is None:
        best_a = max(match.red_round1_points, match.red_round2_points)
        best_b = max(match.blue_round1_points, match.blue_round2_points)
        winning_seed = break_tie(node.seed_a, best_a, node.seed_b, best_b)
        if winning_seed == node.seed_a:
            winner_team = node.team_a
            winner_seed = node.seed_a
        else:
            winner_team = node.team_b
            winner_seed = node.seed_b
    else:
        if winner_team.id == node.team_a_id:
            winner_seed = node.seed_a
        else:
            winner_seed = node.seed_b

    # 5. Set winner, save node.
    node.winner = winner_team
    node.save(update_fields=["match", "winner"])

    # Compute + apply parent mutations.
    flat = [_node_to_dict(n) for n in tournament.nodes.select_related("advances_to")]
    mutations = advance_winner(
        flat, (node.bracket_round, node.position), winner_team.id, winner_seed
    )
    for mut in mutations:
        parent = tournament.nodes.get(
            bracket_round=mut["bracket_round"], position=mut["position"]
        )
        if mut["slot"] == "a":
            parent.team_a = winner_team
            parent.seed_a = mut["seed"]
        else:
            parent.team_b = winner_team
            parent.seed_b = mut["seed"]
        parent.save(update_fields=["team_a", "team_b", "seed_a", "seed_b"])

    # 6. Final node -> stamp champion + complete.
    if node.advances_to_id is None:
        tournament.champion = winner_team
        tournament.state = "completed"
        tournament.save(update_fields=["champion", "state"])

    return redirect("tournament_detail", tournament_id=tournament.id)
