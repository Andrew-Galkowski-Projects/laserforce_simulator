from django.urls import path
from rest_framework.routers import DefaultRouter

from matches.api_views import (
    GameRoundViewSet,
    MatchViewSet,
    SimulateBatchAPIView,
    SimulateBatchStatusAPIView,
)
from teams.api_views import PlayerViewSet, TeamViewSet

router = DefaultRouter()
router.register("teams", TeamViewSet, basename="team")
router.register("players", PlayerViewSet, basename="player")
router.register("matches", MatchViewSet, basename="match")
router.register("rounds", GameRoundViewSet, basename="gameround")

urlpatterns = router.urls + [
    path(
        "simulate-batch/",
        SimulateBatchAPIView.as_view(),
        name="api_simulate_batch",
    ),
    path(
        "simulate-batch/<str:job_id>/",
        SimulateBatchStatusAPIView.as_view(),
        name="api_simulate_batch_status",
    ),
]
