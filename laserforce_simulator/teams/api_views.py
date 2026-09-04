from django.contrib.auth.decorators import login_not_required
from django.utils.decorators import method_decorator
from rest_framework.viewsets import ReadOnlyModelViewSet

from accounts.permissions import owned_queryset
from teams.models import Player, Team
from teams.serializers import PlayerSerializer, TeamListSerializer, TeamSerializer


# UX-01 — exempt from `LoginRequiredMiddleware` so an anonymous request gets
# DRF's 403 JSON rather than an HTML 302 to the login page; the settings-level
# `IsAuthenticated` is what actually gates it. The class-level `queryset` stays
# for the router's basename/model introspection.
@method_decorator(login_not_required, name="dispatch")
class TeamViewSet(ReadOnlyModelViewSet):
    queryset = Team.objects.prefetch_related("players").order_by("name")

    def get_queryset(self):
        return owned_queryset(
            Team.objects.prefetch_related("players").order_by("name"), self.request.user
        )

    def get_serializer_class(self):
        if self.action == "retrieve":
            return TeamSerializer
        return TeamListSerializer


@method_decorator(login_not_required, name="dispatch")
class PlayerViewSet(ReadOnlyModelViewSet):
    queryset = Player.objects.select_related("team").order_by("team__name", "name")
    serializer_class = PlayerSerializer

    def get_queryset(self):
        return owned_queryset(
            Player.objects.select_related("team").order_by("team__name", "name"),
            self.request.user,
            path="team",
        )
