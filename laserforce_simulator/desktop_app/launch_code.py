"""DESK-01 (ADR-0048): the Desktop App's Launch code sign-in.

The launcher mints one code per launch, in this process, and opens the browser
at ``/api/accounts/desktop-login/?code=...``. The view spends the code, sets
the ordinary ANG-03 refresh cookie and redirects to ``/``, where the SPA's
``restoreSession`` signs in through the normal JWT path.
"""

from __future__ import annotations

import secrets
import threading

from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.utils.decorators import method_decorator
from rest_framework import permissions, views
from rest_framework.request import Request
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User
from accounts.token_api_views import set_refresh_cookie

SPENT_MESSAGE = "This launch link has been used. Restart the app."
LOOPBACK_ADDRESSES = frozenset({"127.0.0.1", "::1"})

_codes: dict[str, int] = {}
_lock = threading.Lock()


def mint_launch_code(user: User) -> str:
    """A single-use code that signs ``user`` in once."""
    code = secrets.token_urlsafe(32)
    with _lock:
        _codes[code] = user.pk
    return code


def spend_launch_code(code: str) -> int | None:
    """The user pk for ``code``, removing it; ``None`` if unknown or spent."""
    with _lock:
        return _codes.pop(code, None)


@method_decorator(login_not_required, name="dispatch")
class DesktopLoginView(views.APIView):
    """GET ``?code=`` -> 302 to ``/`` with the refresh cookie, else a plain 403.

    Only this machine may spend a code, and the address is checked before the
    spend, so a remote request never burns one. The redirect is always ``/``.
    """

    authentication_classes: list[type] = []
    permission_classes = [permissions.AllowAny]

    def get(self, request: Request) -> HttpResponseBase:
        if request.META.get("REMOTE_ADDR") not in LOOPBACK_ADDRESSES:
            return _refused()
        user_id = spend_launch_code(request.query_params.get("code", ""))
        if user_id is None:
            return _refused()
        user = User.objects.get(pk=user_id)
        response = HttpResponseRedirect("/")
        set_refresh_cookie(response, str(RefreshToken.for_user(user)))
        return response


def _refused() -> HttpResponse:
    return HttpResponse(
        SPENT_MESSAGE, status=403, content_type="text/plain; charset=utf-8"
    )
