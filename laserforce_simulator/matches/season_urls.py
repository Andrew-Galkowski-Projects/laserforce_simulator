"""LG-01 — Season URL patterns mounted at ``/seasons/`` by the project
URLconf. No ``app_name`` so reverse uses the bare names
``season_standings`` and ``season_schedule``.
"""

from django.urls import path

from . import league_views

urlpatterns = [
    path(
        "<int:season_id>/",
        league_views.season_dashboard,
        name="season_dashboard",
    ),
    path(
        "<int:season_id>/start-season/",
        league_views.start_season,
        name="start_season",
    ),
    path(
        "<int:season_id>/play-week/",
        league_views.play_week,
        name="play_week",
    ),
    path(
        "<int:season_id>/play-two-months/",
        league_views.play_two_months,
        name="play_two_months",
    ),
    path(
        "<int:season_id>/play-until-end/",
        league_views.play_until_end,
        name="play_until_end",
    ),
    path(
        "<int:season_id>/play-single-round/",
        league_views.play_single_round,
        name="play_single_round",
    ),
    path(
        "<int:season_id>/play-playoffs/",
        league_views.play_playoffs,
        name="play_playoffs",
    ),
    path(
        "<int:season_id>/play-status/<str:job_id>/",
        league_views.play_status,
        name="play_status",
    ),
    path(
        "<int:season_id>/awards/",
        league_views.season_awards,
        name="season_awards",
    ),
    path(
        "<int:season_id>/standings/",
        league_views.season_standings,
        name="season_standings",
    ),
    path(
        "<int:season_id>/schedule/",
        league_views.season_schedule,
        name="season_schedule",
    ),
]
