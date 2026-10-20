"""LG-02a — the Tournament index and the create form."""

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from accounts.permissions import owned_queryset

from matches.models import Tournament
from matches.tournament_services.create import (
    TournamentCreateForm,
    create_tournament,
)
from matches.tournament_services.detail import format_label


def tournament_list(request: HttpRequest) -> HttpResponse:
    """List all Tournaments newest-first."""
    tournaments = list(
        owned_queryset(Tournament.objects.all(), request.user).order_by("-id")
    )
    # ANG-01f — the list shows the derived label ("Random Draw" for a
    # Player-pool draw played as RR->DE), not the raw format choice.
    for tournament in tournaments:
        tournament.format_label = format_label(tournament)
    return render(
        request,
        "matches/tournament_list.html",
        {"tournaments": tournaments},
    )


def _render_create(request: HttpRequest, form: TournamentCreateForm) -> HttpResponse:
    # ANG-01f — the picker offers exactly the Form's choices: the requester's
    # own and Unmanaged Teams with a full valid Roster (the leak fix).
    return render(
        request,
        "matches/tournament_create.html",
        {"form": form, "available_teams": list(form.fields["teams"].queryset)},
    )


def _flash_form_errors(request: HttpRequest, form: TournamentCreateForm) -> None:
    """Flash every form error; a field error is prefixed with its field name."""
    for field, errors in form.errors.items():
        for error in errors:
            if field in ("__all__", "name", "teams"):
                messages.error(request, error)
            else:
                messages.error(request, f"{field}: {error}")


def tournament_create(request: HttpRequest) -> HttpResponse:
    """GET -> render the create form. POST valid -> create the Tournament (and
    its participants with default Seeding), redirect to detail. POST invalid ->
    re-render (200) with the form errors flashed.
    """
    if request.method != "POST":
        return _render_create(request, TournamentCreateForm(user=request.user))

    form = TournamentCreateForm(request.POST, user=request.user)
    if not form.is_valid():
        _flash_form_errors(request, form)
        return _render_create(request, form)

    tournament = create_tournament(form, user=request.user)
    return redirect("tournament_detail", tournament_id=tournament.id)
