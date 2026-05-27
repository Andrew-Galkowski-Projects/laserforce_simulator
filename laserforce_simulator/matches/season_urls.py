"""LG-01 — Season URL patterns mounted at ``/seasons/`` by the project
URLconf. No ``app_name`` so reverse uses the bare names
``season_standings`` and ``season_schedule``.
"""

from django.urls import path

from . import views

urlpatterns = [
    path(
        "<int:season_id>/",
        views.season_dashboard,
        name="season_dashboard",
    ),
    path(
        "<int:season_id>/start-season/",
        views.start_season,
        name="start_season",
    ),
    path(
        "<int:season_id>/play-week/",
        views.play_week,
        name="play_week",
    ),
    path(
        "<int:season_id>/play-two-months/",
        views.play_two_months,
        name="play_two_months",
    ),
    path(
        "<int:season_id>/play-until-end/",
        views.play_until_end,
        name="play_until_end",
    ),
    path(
        "<int:season_id>/play-status/<str:job_id>/",
        views.play_status,
        name="play_status",
    ),
    path(
        "<int:season_id>/standings/",
        views.season_standings,
        name="season_standings",
    ),
    path(
        "<int:season_id>/schedule/",
        views.season_schedule,
        name="season_schedule",
    ),
]
