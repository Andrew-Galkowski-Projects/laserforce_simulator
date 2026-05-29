"""LG-01a — League URL patterns mounted at ``/leagues/`` by the project
URLconf. No ``app_name`` so reverse uses the bare name ``league_list``.
"""

from django.urls import path

from . import league_views

urlpatterns = [
    path("create/", league_views.league_create, name="league_create"),
    path("<int:league_id>/", league_views.league_dashboard, name="league_dashboard"),
    path(
        "<int:league_id>/next-season/",
        league_views.next_season,
        name="next_season",
    ),
    path(
        "<int:league_id>/history/",
        league_views.league_history,
        name="league_history",
    ),
    path(
        "<int:league_id>/team_schedule/<int:team_id>/",
        league_views.team_schedule,
        name="team_schedule",
    ),
    path("", league_views.league_list, name="league_list"),
]
