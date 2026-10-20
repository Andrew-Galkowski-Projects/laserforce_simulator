"""ANG-03 - the JWT token endpoints (spec #207, ADR-0041).

``POST /api/token/`` logs in, ``POST /api/token/refresh/`` trades the refresh
cookie for a new access token (rotating the cookie) and
``POST /api/token/logout/`` blacklists the cookie's token and deletes it.

The access token travels in the response body; the refresh token only ever in
the httpOnly ``refresh_token`` cookie scoped to ``/api/token/``. Each view is
``login_not_required``, ``AllowAny`` and has no authentication classes, so no
CSRF token or session is involved. simplejwt's serializers do the token work.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.utils.decorators import method_decorator
from rest_framework import exceptions, status, views
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings as jwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/token/"


def _set_refresh_cookie(response: Response, refresh: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh,
        max_age=int(jwt_settings.REFRESH_TOKEN_LIFETIME.total_seconds()),
        path=REFRESH_COOKIE_PATH,
        secure=not settings.DEBUG,
        httponly=True,
        samesite="Lax",
    )


def _access_response(tokens: dict[str, str]) -> Response:
    """200 ``{"access"}``; the refresh token goes only into the cookie."""
    response = Response({"access": tokens["access"]})
    _set_refresh_cookie(response, tokens["refresh"])
    return response


class _TokenAPIView(views.APIView):
    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    def get_authenticate_header(self, request: Request) -> str:
        """Keep simplejwt's 401s as 401 (DRF turns them into 403 when no
        authentication class supplies a challenge)."""
        return f'{jwt_settings.AUTH_HEADER_TYPES[0]} realm="api"'


@method_decorator(login_not_required, name="dispatch")
class TokenObtainAPIView(_TokenAPIView):
    """POST ``{"email", "password"}`` -> 200 ``{"access"}`` + refresh cookie.

    Bad credentials / inactive Account -> 401; missing fields -> 400.
    """

    def post(self, request: Request) -> Response:
        serializer = TokenObtainPairSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        return _access_response(serializer.validated_data)


@method_decorator(login_not_required, name="dispatch")
class TokenRefreshAPIView(_TokenAPIView):
    """POST (no body) -> 200 ``{"access"}`` + a rotated refresh cookie.

    Reads the refresh token only from the cookie; a missing, expired,
    malformed or blacklisted one is a 401.
    """

    def post(self, request: Request) -> Response:
        refresh = request.COOKIES.get(REFRESH_COOKIE_NAME)
        if not refresh:
            raise exceptions.NotAuthenticated("No refresh token cookie.")
        serializer = TokenRefreshSerializer(data={"refresh": refresh})
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as error:
            raise InvalidToken(error.args[0]) from error
        return _access_response(serializer.validated_data)


@method_decorator(login_not_required, name="dispatch")
class TokenLogoutAPIView(_TokenAPIView):
    """POST -> 204; blacklists a valid refresh cookie, always deletes it."""

    def post(self, request: Request) -> Response:
        refresh = request.COOKIES.get(REFRESH_COOKIE_NAME)
        if refresh:
            try:
                RefreshToken(refresh).blacklist()  # type: ignore[arg-type]
            except TokenError:
                pass  # already invalid: nothing to revoke
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, samesite="Lax"
        )
        return response
