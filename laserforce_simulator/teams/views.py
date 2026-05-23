from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.core.exceptions import ValidationError
from matches.models import PlayerRoundState
from .career_stats import points_trend, summarize, summarize_by_role
from .models import Team, Player, ROLE_CHOICES, _random_player_profile
from .forms import TeamForm, PlayerForm, TeamSlotForm


def team_list(request):
    """Display all teams with their roster status"""
    teams = Team.objects.all().prefetch_related("players")
    return render(request, "teams/team_list.html", {"teams": teams})


def team_detail(request, team_id):
    """Display team details and roster"""
    team = get_object_or_404(Team, id=team_id)
    players = team.players.all().order_by("name")

    context = {
        "team": team,
        "players": players,
        "active_roster": team.active_roster,
        "bench_players": team.bench_players,
        "is_valid": team.is_valid_roster,
        "roster_errors": team.roster_errors,
        "player_count": team.player_count,
        "role_choices": ROLE_CHOICES,
    }
    return render(request, "teams/team_detail.html", context)


def team_create(request):
    """Create a new team"""
    if request.method == "POST":
        form = TeamForm(request.POST)
        if form.is_valid():
            team = form.save()
            messages.success(request, f'Team "{team.name}" created successfully!')
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamForm()

    return render(
        request, "teams/team_form.html", {"form": form, "title": "Create New Team"}
    )


def team_edit(request, team_id):
    """Edit an existing team"""
    team = get_object_or_404(Team, id=team_id)

    if request.method == "POST":
        form = TeamForm(request.POST, instance=team)
        if form.is_valid():
            team = form.save()
            messages.success(request, f'Team "{team.name}" updated successfully!')
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamForm(instance=team)

    return render(
        request,
        "teams/team_form.html",
        {"form": form, "title": f"Edit {team.name}", "team": team},
    )


def team_slots_edit(request, team_id):
    """Assign players to role slots on a team."""
    team = get_object_or_404(Team, id=team_id)

    if request.method == "POST":
        form = TeamSlotForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, "Roster slots updated.")
            return redirect("team_detail", team_id=team.id)
    else:
        form = TeamSlotForm(instance=team)

    return render(
        request,
        "teams/team_slots_form.html",
        {"form": form, "team": team, "title": f"Edit Roster – {team.name}"},
    )


# PD-1: groups mirror the documented stat categories in teams/CLAUDE.md
# (Awareness / Decision-making / Physical / Team / Role). Every one of the
# 19 stats appears in exactly one group.
_STAT_GROUPS = [
    (
        "Awareness",
        [
            ("player_awareness", "Player Awareness"),
            ("game_awareness", "Game Awareness"),
            ("resource_awareness", "Resource Awareness"),
        ],
    ),
    (
        "Decision-making",
        [
            ("decision_making", "Decision Making"),
        ],
    ),
    (
        "Physical",
        [
            ("positioning", "Positioning"),
            ("stamina", "Stamina"),
            ("speed", "Speed"),
            ("flexibility", "Flexibility"),
            ("adaptability", "Adaptability"),
        ],
    ),
    (
        "Team",
        [
            ("communication", "Communication"),
            ("teamwork", "Teamwork"),
        ],
    ),
    (
        "Role",
        [
            ("Offensive_synergy", "Offensive Synergy"),
            ("defensive_synergy", "Defensive Synergy"),
            ("midfield_synergy", "Midfield Synergy"),
            ("resupply_synergy", "Resupply Synergy"),
            ("resupply_efficiency", "Resupply Efficiency"),
            ("accuracy", "Accuracy"),
            ("survival", "Survival"),
            ("special_usage", "Special Usage"),
        ],
    ),
]


def player_detail(request, team_id: int, player_id: int):
    team = get_object_or_404(Team, id=team_id)
    player = get_object_or_404(Player, id=player_id, team=team)
    stat_groups = [
        (group_name, [(label, getattr(player, field)) for field, label in fields])
        for group_name, fields in _STAT_GROUPS
    ]
    return render(
        request,
        "teams/player_detail.html",
        {"player": player, "team": team, "stat_groups": stat_groups},
    )


def player_add(request, team_id):
    """Add a player to a team"""
    team = get_object_or_404(Team, id=team_id)

    if request.method == "POST":
        form = PlayerForm(request.POST)
        if form.is_valid():
            player = form.save(commit=False)
            player.team = team
            try:
                player.full_clean()
                player.save()
                messages.success(
                    request, f'Player "{player.name}" added to {team.name}!'
                )
                return redirect("team_detail", team_id=team.id)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
    else:
        form = PlayerForm(initial=_random_player_profile())

    return render(
        request,
        "teams/player_form.html",
        {"form": form, "team": team, "title": f"Add Player to {team.name}"},
    )


def player_edit(request, team_id, player_id):
    """Edit a player"""
    team = get_object_or_404(Team, id=team_id)
    player = get_object_or_404(Player, id=player_id, team=team)

    if request.method == "POST":
        form = PlayerForm(request.POST, instance=player)
        if form.is_valid():
            player = form.save(commit=False)
            try:
                player.full_clean()
                player.save()
                messages.success(request, f'Player "{player.name}" updated!')
                return redirect("team_detail", team_id=team.id)
            except ValidationError as e:
                for error in e.messages:
                    messages.error(request, error)
    else:
        form = PlayerForm(instance=player)

    return render(
        request,
        "teams/player_form.html",
        {"form": form, "team": team, "player": player, "title": f"Edit {player.name}"},
    )


def player_delete(request, team_id, player_id):
    """Delete a player"""
    team = get_object_or_404(Team, id=team_id)
    player = get_object_or_404(Player, id=player_id, team=team)

    if request.method == "POST":
        player_name = player.name
        player.delete()
        messages.success(request, f'Player "{player_name}" removed from {team.name}')
        return redirect("team_detail", team_id=team.id)

    return render(
        request, "teams/player_confirm_delete.html", {"player": player, "team": team}
    )


def player_career_stats(request, player_id: int):
    """Render a player's career stats page (HX-01).

    Aggregates every `PlayerRoundState` for this player into the totals,
    per-role breakdown, and rolling-mean trend defined in
    `teams/career_stats.py` (the pure module). The view owns the
    round-dict assembly so the pure module never sees a Django object.
    """
    player = get_object_or_404(Player, pk=player_id)

    states = (
        PlayerRoundState.objects.filter(player=player)
        .select_related("game_round")
        .order_by("game_round__date_played", "game_round_id")
    )

    rounds = [
        {
            "role": s.role,
            "points_scored": s.points_scored,
            "tags_made": s.tags_made,
            "times_tagged": s.times_tagged,
            "shots_missed": s.shots_missed,
            "final_special": s.final_special,
            "specials_used": s.specials_used,
            "was_eliminated_at": s.was_eliminated_at,
            "date_played": s.game_round.date_played,
            "game_round_id": s.game_round_id,
        }
        for s in states
    ]

    career = summarize(rounds)
    per_role = summarize_by_role(rounds)
    trend = points_trend(rounds)
    total_rounds = career["games"]

    context = {
        "player": player,
        "total_rounds": total_rounds,
        "career": career,
        "per_role": per_role,
        "trend": trend,
        "has_rounds": total_rounds > 0,
    }
    return render(request, "teams/player_career_stats.html", context)
