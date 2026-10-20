"""ANG-01g — the Accounts API (ADR-0039).

Plain ``APIView`` classes, each ``login_not_required`` on ``dispatch`` so an
anonymous request gets DRF's enveloped 403 JSON instead of the middleware's
HTML 302. The settings-level ``IsAuthenticated`` gates every view except
``RegisterAPIView`` (``AllowAny``).
"""

from __future__ import annotations

from collections.abc import Mapping

from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.utils.decorators import method_decorator
from rest_framework import status, views
from rest_framework.exceptions import ParseError
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from laserforce_simulator.api.exceptions import (
    EnvelopeValidationError,
    validate_form_or_raise,
)
from laserforce_simulator.api.forms import plain_form_data
from teams.models import Player

from .forms import RegisterForm, StyledPasswordChangeForm
from .permissions import get_owned_or_404
from .self_insert import (
    clear_self_insert,
    is_self_insert_eligible,
    set_self_insert,
)
from .serializers import MeSerializer


def _me(user: object) -> dict:
    return MeSerializer(user).data


@method_decorator(login_not_required, name="dispatch")
class MeAPIView(views.APIView):
    """GET /api/accounts/me/ -> the logged-in Account in the ``me`` shape."""

    def get(self, request: Request) -> Response:
        return Response(_me(request.user))


@method_decorator(login_not_required, name="dispatch")
class RegisterAPIView(views.APIView):
    """POST /api/accounts/register/ -> 201 ``me`` of the new Account.

    Binds ``RegisterForm``. Deliberately does **not** log the new Account in
    (login belongs to ANG-03); an authenticated caller keeps its own session.
    """

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        form = RegisterForm(data=plain_form_data(RegisterForm, request.data))
        validate_form_or_raise(form)
        user = form.save()
        return Response(_me(user), status=status.HTTP_201_CREATED)


@method_decorator(login_not_required, name="dispatch")
class PasswordChangeAPIView(views.APIView):
    """POST /api/accounts/me/password/ -> 204; the session survives."""

    def post(self, request: Request) -> Response:
        form = StyledPasswordChangeForm(
            request.user,
            data=plain_form_data(StyledPasswordChangeForm, request.data),
        )
        validate_form_or_raise(form)
        form.save()
        update_session_auth_hash(request, form.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


# Byte-identical to the HTML ``self_insert_link`` error flash (accounts/views.py).
NOT_ELIGIBLE_MESSAGE = "Only a Player on a Team you manage can be your Self-insert."


def _player_id(data: object) -> int:
    """The ``player`` key of a JSON object body, as a strict integer.

    A non-object body is a 400 ``parse_error``; a missing key is a 400
    ``required`` and a non-integer value (bools included) a 400 ``invalid``,
    both on ``field: "player"``.
    """
    if not isinstance(data, Mapping):
        raise ParseError("Expected a JSON object.")
    if "player" not in data:
        raise EnvelopeValidationError(
            [
                {
                    "code": "required",
                    "message": "This field is required.",
                    "field": "player",
                }
            ]
        )
    value = data["player"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvelopeValidationError(
            [
                {
                    "code": "invalid",
                    "message": "A valid integer is required.",
                    "field": "player",
                }
            ]
        )
    return value


@method_decorator(login_not_required, name="dispatch")
class SelfInsertAPIView(views.APIView):
    """PUT / DELETE /api/accounts/me/self-insert/ -- link / unlink the Self-insert."""

    def put(self, request: Request) -> Response:
        """``{"player": id}`` -> 200 ``me``.

        Missing or another Account's Player -> 404; a Player on an Unmanaged
        Team -> 400 ``not_eligible``, nothing written.
        """
        player = get_owned_or_404(
            Player.objects.select_related("team"),
            request,
            pk=_player_id(request.data),
        )
        if not is_self_insert_eligible(player, request.user):
            raise EnvelopeValidationError(
                [
                    {
                        "code": "not_eligible",
                        "message": NOT_ELIGIBLE_MESSAGE,
                        "field": None,
                    }
                ]
            )
        set_self_insert(request.user, player)  # type: ignore[arg-type]
        return Response(_me(request.user))

    def delete(self, request: Request) -> Response:
        """-> 204, idempotent; clears the stored column even when it drifted."""
        if request.user.self_insert_id is not None:  # type: ignore[union-attr]
            clear_self_insert(request.user)  # type: ignore[arg-type]
        return Response(status=status.HTTP_204_NO_CONTENT)
