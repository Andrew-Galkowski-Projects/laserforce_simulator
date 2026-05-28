"""LG-01a — League URL patterns mounted at ``/leagues/`` by the project
URLconf. No ``app_name`` so reverse uses the bare name ``league_list``.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("create/", views.league_create, name="league_create"),
    path("<int:league_id>/", views.league_dashboard, name="league_dashboard"),
    path("<int:league_id>/next-season/", views.next_season, name="next_season"),
    path("<int:league_id>/history/", views.league_history, name="league_history"),
    path("", views.league_list, name="league_list"),
]
