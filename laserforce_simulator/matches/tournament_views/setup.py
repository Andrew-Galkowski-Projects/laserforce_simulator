"""LG-02a / LG-02a-2 — setup writes: reseed, lock and participant CSV import."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from accounts.permissions import get_owned_or_404, manager_or_none
from teams.forms import RosterImportForm
from teams.roster_importer import RosterImportError, parse_roster_csv
from teams.views import _apply_roster, _check_db_slot_collisions

from matches.bracket import default_seed_order
from matches.models import Tournament, TournamentParticipant
from matches.tournament_services.detail import build_detail_context
from matches.tournament_services.seeding import team_mean_rating


@transaction.atomic
def tournament_reseed(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Persist a manually reordered Seeding (new seed ints from POST)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
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

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    try:
        tournament.lock_and_build()
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
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

    tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    if tournament.is_locked:
        messages.error(request, "Participants can only be imported during setup.")
        return redirect("tournament_detail", tournament_id=tournament.id)

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(import_row_errors: list) -> HttpResponse:
        # Build the read-only detail context BEFORE flagging rollback — a
        # rolled-back atomic block forbids further queries, so set_rollback
        # must be the last DB-touching action before the render.
        ctx = build_detail_context(tournament)
        ctx["import_form"] = form
        ctx["import_row_errors"] = import_row_errors
        transaction.set_rollback(True)
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])

    try:
        parsed = parse_roster_csv(form.cleaned_data["csv_file"])
        # ANG-01a — ownership-scoped: a name matching only another Account's
        # Team creates a new Team (stamped) rather than appending to theirs.
        _check_db_slot_collisions(parsed, manager=manager_or_none(request))
        created_teams, _appended_teams, _player_count = _apply_roster(
            parsed, manager=manager_or_none(request)
        )
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
    team_ratings = [(p.team_id, team_mean_rating(p.team)) for p in participants]
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
