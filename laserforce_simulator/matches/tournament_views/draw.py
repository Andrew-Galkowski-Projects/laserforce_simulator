"""LG-02x-1 / ANG-01f-5 — Player-pool draw intake, draw, re-roll, hand-edit.

Each view delegates to ``matches.tournament_services.draw`` and binds the same
strict Forms as the API. Guard failures (embedded, not in setup, not a
Player-pool draw), Form errors and Draw-rule errors are flashed and redirect
to the detail page; a bad pool CSV re-renders the detail page with its row
errors, as before.
"""

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from accounts.permissions import get_owned_or_404, manager_or_none
from matches.models import Tournament
from matches.tournament_services.detail import build_detail_context
from matches.tournament_services.draw import (
    DrawEntryForm,
    PoolAddPlayersForm,
    PoolGenerateForm,
    add_pool_players,
    edit_draw_entry,
    generate_pool_players,
    import_pool_players,
    remove_pool_player,
    require_pool_setup,
    run_draw,
)
from matches.tournament_services.errors import (
    DrawNotReadyError,
    TournamentEmbeddedError,
    TournamentNotSetupError,
)
from teams.forms import RosterImportForm
from teams.roster_importer import RosterImportError

_NOT_IN_POOL = "That player is not in the pool."


def _detail(tournament: Tournament) -> HttpResponse:
    return redirect("tournament_detail", tournament_id=tournament.id)


def _pool_tournament(
    request: HttpRequest, tournament_id: int
) -> tuple[Tournament, HttpResponse | None]:
    """The owned Tournament (404 otherwise) and, when a pool-setup guard
    fails, a flash-and-redirect response to return instead."""
    tournament: Tournament = get_owned_or_404(Tournament, request, pk=tournament_id)
    try:
        require_pool_setup(tournament)
    except TournamentNotSetupError:
        messages.error(request, "The player pool can only be edited during setup.")
        return tournament, _detail(tournament)
    except (TournamentEmbeddedError, ValidationError) as exc:
        messages.error(request, "; ".join(getattr(exc, "messages", [str(exc)])))
        return tournament, _detail(tournament)
    return tournament, None


def _flash_form_errors(request: HttpRequest, form: forms.Form) -> None:
    """One flash per Form error, prefixed with the field label."""
    for field, errors in form.errors.items():
        label = form[field].label if field in form.fields else None
        for message in errors:
            messages.error(request, f"{label}: {message}" if label else message)


def _posted_player_id(request: HttpRequest) -> int | None:
    """The POSTed ``player_id`` as an int, or None when missing / malformed."""
    raw = request.POST.get("player_id", "")
    return int(raw) if raw.isdigit() else None


def tournament_pool_add_existing(
    request: HttpRequest, tournament_id: int
) -> HttpResponse:
    """Add selected owned Players to the pool. POST-only, setup-only."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tournament, blocked = _pool_tournament(request, tournament_id)
    if blocked is not None:
        return blocked

    form = PoolAddPlayersForm(
        {"player_ids": request.POST.getlist("players")},
        tournament=tournament,
        user=manager_or_none(request),
    )
    if form.is_valid():
        add_pool_players(tournament, form)
    else:
        _flash_form_errors(request, form)
    return _detail(tournament)


def tournament_pool_generate(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Generate N fresh Players on the Free Agents Team and pool them.
    POST-only, setup-only; a bad count / mean / spread is flashed."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tournament, blocked = _pool_tournament(request, tournament_id)
    if blocked is not None:
        return blocked

    form = PoolGenerateForm(request.POST)
    if form.is_valid():
        generate_pool_players(tournament, form)
    else:
        _flash_form_errors(request, form)
    return _detail(tournament)


def tournament_pool_import(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Import pool Players from a roster CSV (one row = one pool Player).
    POST-only, setup-only. Errors re-render the detail page."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tournament, blocked = _pool_tournament(request, tournament_id)
    if blocked is not None:
        return blocked

    form = RosterImportForm(request.POST, request.FILES)

    def _render_error(pool_import_row_errors: list) -> HttpResponse:
        ctx = build_detail_context(tournament)
        ctx["pool_import_form"] = form
        ctx["pool_import_row_errors"] = pool_import_row_errors
        return render(request, "matches/tournament_detail.html", ctx)

    if not form.is_valid():
        return _render_error([])
    try:
        import_pool_players(tournament, form, user=manager_or_none(request))
    except RosterImportError as exc:
        return _render_error(exc.errors)
    return _detail(tournament)


def tournament_pool_remove(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Remove a pool entry (by ``player_id``). POST-only, setup-only."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tournament, blocked = _pool_tournament(request, tournament_id)
    if blocked is not None:
        return blocked

    player_id = _posted_player_id(request)
    try:
        if player_id is None:
            raise Http404(_NOT_IN_POOL)
        remove_pool_player(tournament, player_id)
    except Http404:
        messages.error(request, _NOT_IN_POOL)
    return _detail(tournament)


def tournament_draw(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Run (or re-roll) the tier-balanced Draw. POST-only, setup-only; a bad
    pool size is flashed with the rule."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tournament, blocked = _pool_tournament(request, tournament_id)
    if blocked is not None:
        return blocked

    try:
        run_draw(tournament, user=manager_or_none(request))
    except DrawNotReadyError as exc:
        messages.error(request, str(exc))
    return _detail(tournament)


def tournament_draw_edit(request: HttpRequest, tournament_id: int) -> HttpResponse:
    """Hand-edit one drawn entry's tier / drawn Team. POST-only, setup-only;
    a tier outside 1..6 or another Tournament's Team is flashed."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    tournament, blocked = _pool_tournament(request, tournament_id)
    if blocked is not None:
        return blocked

    form = DrawEntryForm(request.POST, tournament=tournament)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _detail(tournament)
    player_id = _posted_player_id(request)
    try:
        if player_id is None:
            raise Http404(_NOT_IN_POOL)
        edit_draw_entry(tournament, player_id, form)
    except Http404:
        messages.error(request, _NOT_IN_POOL)
    return _detail(tournament)
