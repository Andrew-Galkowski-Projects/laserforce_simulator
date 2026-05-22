from django.urls import path
from . import views

urlpatterns = [
    path("", views.match_list, name="match_list"),
    path("create/", views.create_match, name="create_match"),
    path("single-round/create/", views.create_single_round, name="create_single_round"),
    path("<int:match_id>/", views.match_detail, name="match_detail"),
    path(
        "game-round/<int:round_id>/", views.game_round_detail, name="game_round_detail"
    ),
    path(
        "team/<int:team_id>/history/",
        views.team_match_history,
        name="team_match_history",
    ),
    path(
        "game-round/<int:round_id>/events/",
        views.game_round_events,
        name="game_round_events",
    ),
    path(
        "game-round/<int:round_id>/missile-log/",
        views.missile_log,
        name="missile_log",
    ),
    path(
        "game-round/<int:round_id>/heatmap/",
        views.movement_heatmap,
        name="movement_heatmap",
    ),
    path(
        "game-round/<int:round_id>/export/",
        views.export_round_report,
        name="export_round_report",
    ),
    path("compare/", views.compare_rounds, name="compare_rounds"),
    path("simulate-batch/", views.simulate_batch, name="simulate_batch"),
    path(
        "simulate-batch/status/<str:job_id>/",
        views.batch_simulate_status,
        name="batch_simulate_status",
    ),
    path("save-batch-games/", views.save_batch_games, name="save_batch_games"),
    path(
        "save-batch-status/<str:job_id>/",
        views.save_batch_status,
        name="save_batch_status",
    ),
]
