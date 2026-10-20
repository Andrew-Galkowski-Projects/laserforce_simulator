"""LG-02a / LG-02a-2 / ANG-01f-4 — setup writes: reseed, lock and participant
CSV import.

Each view delegates to ``matches.tournament_services.setup`` (shared with the
API). The embedded / not-setup guards and every validation failure flash and
redirect back to the detail page with nothing written; a bad import CSV
re-renders the detail page with ``import_row_errors``.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from accounts.permissions import get_owned_or_404, manager_or_none
from teams.forms import RosterImportForm
from teams.roster_importer import RosterImportError

from matches.models import Tournament
from matches.tournament_services.detail import build_detail_context
from matches.tournament_services.errors import (
    TournamentEmbeddedError,
    TournamentNotSetupError,
    require_setup,
)
from matches.tournament_services.setup import (
    SeedsForm,
    apply_seeds,
    import_participants,
    lock_tournament,
)

# The guard flashes kept from LG-02a (the embedded guard uses its own text).
_RESEED_LOCKED = "Seeding cannot be edited once the bracket is locked."
_IMPORT_LOCKED = "Participants can only be imported during setup."


def _guard_error(
    request: HttpRequest, tournament: Tournament, *, locked_message: str | None = None
) -> HttpResponse | None:
    """Run ``require_setup``; on failure flash and return the detail redirect."""
    try:
        require_setup(tournament)
    except TournamentEmbeddedError as exc:
        messages.error(request, str(exc))
    except TournamentNotSetupError as exc:
        messages.error(request, locked_message or str(exc))
    else:
        return None
    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_reseed(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Persist a manually reordered Seeding (``seed_<team_id>`` POST fields).

    The fields are bound to ``SeedsForm``: anything but exactly 1..N over the
    current participants flashes the error and writes nothing.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _guard_error(request, tournament, locked_message=_RESEED_LOCKED)
    if blocked is not None:
        return blocked

    # Rebuild the API's {team_id: seed} map from the seed_<team_id> fields.
    seeds = {
        key.removeprefix("seed_"): value
        for key, value in request.POST.items()
        if key.startswith("seed_")
    }
    form = SeedsForm({"seeds": seeds}, tournament=tournament)
    if not form.is_valid():
        for error in form.errors.get("seeds", []):
            messages.error(request, error)
    else:
        apply_seeds(tournament, form)
    return redirect("tournament_detail", tournament_id=tournament.id)


def tournament_lock(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """``lock_tournament`` (setup->active, builds + persists nodes). A guard or
    format-rule failure redirects back with a flash.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _guard_error(request, tournament)
    if blocked is not None:
        return blocked
    try:
        lock_tournament(tournament)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect("tournament_detail", tournament_id=tournament.id)


@transaction.atomic
def tournament_import_participants(
    request: HttpRequest, tournament_id: int
) -> HttpResponse:
    """Import participants from a roster CSV (LG-00b reuse). Setup-only.

    Only brand-new Teams become participants (owned by the requester); the
    whole field is then re-seeded by talent (``import_participants``).
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    blocked = _guard_error(request, tournament, locked_message=_IMPORT_LOCKED)
    if blocked is not None:
        return blocked

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(import_row_errors: list) -> HttpResponse:
        # Build the read-only detail context BEFORE flagging rollback — a
        # rolled-back atomic block forbids further queries, so set_rollback
        # must be the last DB-touching action before the render.
        ctx = build_detail_context(tournament, user=request.user)
        ctx["import_form"] = form
        ctx["import_row_errors"] = import_row_errors
        transaction.set_rollback(True)
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])
    try:
        import_participants(tournament, form, user=manager_or_none(request))
    except RosterImportError as exc:
        return _render_error(exc.errors)
    return redirect("tournament_detail", tournament_id=tournament.id)
