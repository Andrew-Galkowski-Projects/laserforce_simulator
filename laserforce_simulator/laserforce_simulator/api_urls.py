from django.urls import path
from rest_framework.routers import DefaultRouter

from core.api_views import ArenaMapViewSet
from matches.api_views import (
    GameRoundViewSet,
    MatchViewSet,
    SaveBatchGamesAPIView,
    SaveBatchStatusAPIView,
    SimulateBatchAPIView,
    SimulateBatchStatusAPIView,
)
from matches.league_api_views import LeagueViewSet, SeasonViewSet
from matches.tournament_api_views.base import TournamentViewSet
from teams.api_views import PlayerViewSet, TeamViewSet

router = DefaultRouter()
router.register("teams", TeamViewSet, basename="team")
router.register("players", PlayerViewSet, basename="player")
router.register("matches", MatchViewSet, basename="match")
router.register("rounds", GameRoundViewSet, basename="gameround")
router.register("maps", ArenaMapViewSet, basename="arenamap")
router.register("leagues", LeagueViewSet, basename="league")
router.register("seasons", SeasonViewSet, basename="season")
router.register("tournaments", TournamentViewSet, basename="tournament")

# ANG-01b — `save/<save_job_id>/` sits before `<job_id>/save/` because
# `simulate-batch/save/save/` matches both; Celery ids are UUIDs, so a batch
# job id of literally "save" does not occur in practice.
urlpatterns = router.urls + [
    path(
        "simulate-batch/",
        SimulateBatchAPIView.as_view(),
        name="api_simulate_batch",
    ),
    path(
        "simulate-batch/save/<str:save_job_id>/",
        SaveBatchStatusAPIView.as_view(),
        name="api_save_batch_status",
    ),
    path(
        "simulate-batch/<str:job_id>/save/",
        SaveBatchGamesAPIView.as_view(),
        name="api_save_batch_games",
    ),
    path(
        "simulate-batch/<str:job_id>/",
        SimulateBatchStatusAPIView.as_view(),
        name="api_simulate_batch_status",
    ),
]
