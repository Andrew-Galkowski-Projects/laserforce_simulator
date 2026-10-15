"""UX-01 — the one hand-written auth view (ADR-0038), plus the UX-02
Self-insert link / unlink views.

Login, logout and password change are Django's own CBVs, wired in
``accounts/urls.py``; only self-registration needs a body of its own.
"""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from teams.models import Player

from .forms import RegisterForm
from .permissions import get_owned_or_404
from .self_insert import clear_self_insert, is_self_insert_eligible, set_self_insert


@login_not_required
def register(request: HttpRequest) -> HttpResponse:
    """UX-01 — open self-registration. On success, log the new Account in and
    redirect to ``LOGIN_REDIRECT_URL``.
    """
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("landing")
    else:
        form = RegisterForm()
    return render(request, "accounts/register.html", {"form": form})


def _safe_next(request: HttpRequest, fallback: str) -> str:
    """UX-02 — ``POST["next"]`` when it is a safe same-host URL, else ``fallback``."""
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return fallback


@require_POST
def self_insert_link(request: HttpRequest, player_id: int) -> HttpResponse:
    """UX-02 — link ``player_id`` as the requesting Account's Self-insert.

    Another Account's Player (or a nonexistent one) ⇒ 404. A Player on an
    Unmanaged Team ⇒ error flash, no write. Otherwise link (replacing any
    previous Self-insert) and redirect back.
    """
    player = get_owned_or_404(
        Player.objects.select_related("team"), request, pk=player_id
    )
    fallback = reverse("player_detail", args=[player.team_id, player.id])
    if not is_self_insert_eligible(player, request.user):
        messages.error(
            request, "Only a Player on a Team you manage can be your Self-insert."
        )
        return redirect(_safe_next(request, fallback))
    set_self_insert(request.user, player)  # type: ignore[arg-type]
    messages.success(request, f'"{player.name}" is now your Self-insert.')
    return redirect(_safe_next(request, fallback))


@require_POST
def self_insert_unlink(request: HttpRequest) -> HttpResponse:
    """UX-02 — clear the requesting Account's Self-insert (if any) and redirect back."""
    if request.user.self_insert_id is None:  # type: ignore[union-attr]
        messages.info(request, "You have no Self-insert to unlink.")
    else:
        clear_self_insert(request.user)  # type: ignore[arg-type]
        messages.success(request, "Self-insert unlinked.")
    return redirect(_safe_next(request, reverse("landing")))
