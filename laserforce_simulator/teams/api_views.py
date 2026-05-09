from rest_framework.viewsets import ReadOnlyModelViewSet

from teams.models import Player, Team
from teams.serializers import PlayerSerializer, TeamListSerializer, TeamSerializer


class TeamViewSet(ReadOnlyModelViewSet):
    queryset = Team.objects.prefetch_related("players").order_by("name")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return TeamSerializer
        return TeamListSerializer


class PlayerViewSet(ReadOnlyModelViewSet):
    queryset = Player.objects.select_related("team").order_by("team__name", "name")
    serializer_class = PlayerSerializer
