from rest_framework.routers import DefaultRouter

from matches.api_views import GameRoundViewSet, MatchViewSet
from teams.api_views import PlayerViewSet, TeamViewSet

router = DefaultRouter()
router.register("teams", TeamViewSet, basename="team")
router.register("players", PlayerViewSet, basename="player")
router.register("matches", MatchViewSet, basename="match")
router.register("rounds", GameRoundViewSet, basename="gameround")

urlpatterns = router.urls
