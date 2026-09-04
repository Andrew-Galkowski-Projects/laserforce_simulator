"""UX-01 — the one hand-written auth view (ADR-0038).

Login, logout and password change are Django's own CBVs, wired in
``accounts/urls.py``; only self-registration needs a body of its own.
"""

from django.contrib.auth import login
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from .forms import RegisterForm


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
