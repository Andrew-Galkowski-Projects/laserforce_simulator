"""LG-02x-1 — Random-Draw player-pool intake, draw, re-roll, hand-edit."""

import random

from django.contrib import messages
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from accounts.permissions import get_owned_or_404, manager_or_none
from teams.constants import PLAYER_NAMES
from teams.forms import RosterImportForm
from teams.models import Player, Team, get_free_agents_team
from teams.player_generator import draw_preferred_roles, draw_stats
from teams.roster_importer import RosterImportError, parse_roster_csv

from matches.draw import ROLE_SLOTS, compute_draw
from matches.models import Tournament, TournamentParticipant, TournamentPlayerEntry
from matches.tournament_services.detail import build_detail_context


def _pool_setup_guard(request: HttpRequest, tournament: Tournament):
    """Shared guard for the pool-mutating views: reject once the bracket is
    locked. Returns a redirect HttpResponse when blocked, else None.
    """
    if tournament.is_locked:
        messages.error(request, "The player pool can only be edited during setup.")
        return redirect("tournament_detail", tournament_id=tournament.id)
    return None


@transaction.atomic
def tournament_pool_add_existing(
    request: HttpRequest, tournament_id: int
) -> HttpResponse:
    """Add selected existing Players to the Random-Draw pool. POST-only,
    setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    player_ids = request.POST.getlist("players")
    existing = set(tournament.player_entries.values_list("player_id", flat=True))
    for player in Player.objects.filter(id__in=player_ids):
        if player.id in existing:
            continue
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)
        existing.add(player.id)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_pool_generate(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Generate N fresh Players (LG-00 pure generator) on the Free Agents Team
    and add them to the pool. POST-only, setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    try:
        count = int(request.POST.get("count") or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        mean_val = int(request.POST.get("mean") or 50)
    except (TypeError, ValueError):
        mean_val = 50
    try:
        std_dev = int(request.POST.get("std_dev") or 15)
    except (TypeError, ValueError):
        std_dev = 15

    if count <= 0:
        messages.error(request, "Enter how many players to generate.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    rng = random.Random()
    free_agents = get_free_agents_team()
    name_pool = list(PLAYER_NAMES)
    rng.shuffle(name_pool)

    for i in range(count):
        if name_pool:
            base_name = name_pool.pop()
        else:
            base_name = f"Player {i + 1}"
        # Dedupe within the Free Agents Team (unique_together team+name).
        name = base_name
        k = 2
        while Player.objects.filter(team=free_agents, name=name).exists():
            name = f"{base_name} #{k}"
            k += 1
        stats = draw_stats(rng, mean_val, std_dev)
        preferred_roles = draw_preferred_roles(rng)
        player = Player.objects.create(
            team=free_agents,
            name=name,
            preferred_roles=preferred_roles,
            **stats,
        )
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_pool_import(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Import pool Players from a roster CSV (LG-00b reuse). Each CSV ROW = one
    pool Player on the Free Agents Team; team-grouping is IGNORED. POST-only,
    setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(pool_import_row_errors: list) -> HttpResponse:
        # Build the read-only detail context BEFORE flagging rollback — a
        # rolled-back atomic block forbids further queries.
        ctx = build_detail_context(tournament)
        ctx["pool_import_form"] = form
        ctx["pool_import_row_errors"] = pool_import_row_errors
        transaction.set_rollback(True)
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])

    try:
        parsed = parse_roster_csv(form.cleaned_data["csv_file"])
    except RosterImportError as exc:
        return _render_error(exc.errors)

    free_agents = get_free_agents_team()
    # Each ParsedRow -> one pool Player. The CSV team / role columns are NOT
    # used (slots are assigned per-Round by the draw; the pool is the team).
    for row in parsed.rows:
        name = row.name
        k = 2
        while Player.objects.filter(team=free_agents, name=name).exists():
            name = f"{row.name} #{k}"
            k += 1
        player = Player.objects.create(
            team=free_agents,
            name=name,
            preferred_roles=row.preferred_roles,
            **row.profile,
            **row.stats,
        )
        TournamentPlayerEntry.objects.create(tournament=tournament, player=player)

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_pool_remove(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Remove a pool entry (by player_id). POST-only, setup-only."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    player_id = request.POST.get("player_id")
    if player_id:
        tournament.player_entries.filter(player_id=player_id).delete()

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_draw(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Run the tier-balanced draw: validate the pool, build drawn Teams +
    participants, fill each entry's tier + drawn_team. Re-runnable (re-roll)
    while in setup. POST-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    entries = list(tournament.player_entries.select_related("player"))
    n = len(entries)
    if n % 6 != 0 or n < 24:
        messages.error(
            request,
            "The pool must be divisible by 6 and have at least 24 players "
            f"(>= 4 teams). It currently has {n}.",
        )
        return redirect("tournament_detail", tournament_id=tournament.id)

    # Build the (player_id, overall_rating) pool list.
    pool = [(e.player_id, e.player.overall_rating) for e in entries]
    plans = compute_draw(pool)

    # Re-roll cleanup: drop any prior drawn Teams + participants for this
    # tournament and null the entries' tier / drawn_team.
    prior_team_ids = list(
        tournament.player_entries.filter(drawn_team__isnull=False)
        .values_list("drawn_team_id", flat=True)
        .distinct()
    )
    if prior_team_ids:
        tournament.participants.filter(team_id__in=prior_team_ids).delete()
        tournament.player_entries.update(tier=None, drawn_team=None)
        Team.objects.filter(id__in=prior_team_ids, is_draw_team=True).delete()

    entry_by_player = {e.player_id: e for e in entries}
    for plan in plans:
        team = Team.objects.create(
            name=f"Draw Team {plan.team_index + 1}",
            is_draw_team=True,
            manager=manager_or_none(request),
        )
        # Initial valid assignment: tier order -> ROLE_SLOTS order (any valid
        # no-duplicate assignment satisfies the relaxed roster_errors).
        for slot, player_id in zip(ROLE_SLOTS, plan.player_ids):
            setattr(team, f"slot_{slot}_id", player_id)
        team.save()
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=plan.team_index + 1
        )
        for player_id, tier in zip(plan.player_ids, plan.tiers):
            entry = entry_by_player[player_id]
            entry.tier = tier
            entry.drawn_team = team
            entry.save(update_fields=["tier", "drawn_team"])

    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_draw_edit(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Admin hand-edit of a drawn entry's tier / drawn_team (the variation
    mechanism — the draw itself is deterministic). POST-only, setup-only.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _pool_setup_guard(request, tournament)
    if blocked is not None:
        return blocked

    player_id = request.POST.get("player_id")
    entry = tournament.player_entries.filter(player_id=player_id).first()
    if entry is None:
        messages.error(request, "That player is not in the pool.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    update_fields = []
    raw_tier = request.POST.get("tier")
    if raw_tier is not None and raw_tier != "":
        try:
            entry.tier = int(raw_tier)
            update_fields.append("tier")
        except (TypeError, ValueError):
            pass
    raw_team = request.POST.get("drawn_team")
    if raw_team is not None and raw_team != "":
        team = Team.objects.filter(id=raw_team, is_draw_team=True).first()
        if team is not None:
            entry.drawn_team = team
            update_fields.append("drawn_team")
    if update_fields:
        entry.save(update_fields=update_fields)

    return redirect("tournament_detail", tournament_id=tournament.id)
