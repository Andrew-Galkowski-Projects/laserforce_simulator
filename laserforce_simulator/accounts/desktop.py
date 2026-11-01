"""ANG-09 spike: Desktop App sign-in (spike/ang-09 only, never merged).

The launcher calls ``mint_launch_code`` in the same process and opens the
browser at ``/api/accounts/desktop-login/?code=...``. The view spends the code,
sets the ordinary ANG-03 refresh cookie and redirects to ``/``, where the SPA's
``restoreSession`` signs in through the normal JWT path.
"""

import secrets
import threading

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.decorators import method_decorator
from rest_framework import permissions, views
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.api_views import MeAPIView
from accounts.models import User
from accounts.serializers import MeSerializer
from accounts.token_api_views import REFRESH_COOKIE_NAME, REFRESH_COOKIE_PATH

_codes: dict[str, int] = {}
_lock = threading.Lock()


def mint_launch_code(user: User) -> str:
    """A single-use code that signs ``user`` in once."""
    code = secrets.token_urlsafe(32)
    with _lock:
        _codes[code] = user.pk
    return code


def _spend(code: str) -> int | None:
    with _lock:
        return _codes.pop(code, None)


@method_decorator(login_not_required, name="dispatch")
class DesktopLoginView(views.APIView):
    authentication_classes: list = []
    permission_classes = [permissions.AllowAny]

    def get(self, request: Request) -> HttpResponse:
        user_id = _spend(request.query_params.get("code", ""))
        if user_id is None:
            return HttpResponse(
                "This launch link has been used. Restart the app.", status=403
            )
        refresh = RefreshToken.for_user(User.objects.get(pk=user_id))
        response = HttpResponseRedirect("/")
        response.set_cookie(
            REFRESH_COOKIE_NAME,
            str(refresh),
            max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
            path=REFRESH_COOKIE_PATH,
            httponly=True,
            samesite="Lax",
        )
        return response


@method_decorator(login_not_required, name="dispatch")
class DesktopMeAPIView(MeAPIView):
    """``accounts/me/`` plus ``desktop: true``, which hides Sign out in the SPA."""

    def get(self, request: Request) -> Response:
        return Response({**MeSerializer(request.user).data, "desktop": True})


@login_not_required
def spa_index(request: HttpRequest) -> FileResponse:
    return FileResponse(open(settings.LF_DESKTOP_SPA / "index.html", "rb"))
