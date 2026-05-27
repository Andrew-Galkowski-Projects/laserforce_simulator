"""LG-01 — Season URL patterns mounted at ``/seasons/`` by the project
URLconf. No ``app_name`` so reverse uses the bare names
``season_standings`` and ``season_schedule``.
"""

from django.urls import path

from . import views

urlpatterns = [
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
