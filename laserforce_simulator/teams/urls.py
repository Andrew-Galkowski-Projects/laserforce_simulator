from django.urls import path
from . import views

urlpatterns = [
    path("", views.team_list, name="team_list"),
    path("create/", views.team_create, name="team_create"),
    path("generate/", views.generate_players, name="generate_players"),
    path("<int:team_id>/", views.team_detail, name="team_detail"),
    path("<int:team_id>/edit/", views.team_edit, name="team_edit"),
    path("<int:team_id>/slots/", views.team_slots_edit, name="team_slots_edit"),
    path("<int:team_id>/add-player/", views.player_add, name="player_add"),
    path(
        "<int:team_id>/player/<int:player_id>/",
        views.player_detail,
        name="player_detail",
    ),
    path(
        "<int:team_id>/player/<int:player_id>/edit/",
        views.player_edit,
        name="player_edit",
    ),
    path(
        "<int:team_id>/player/<int:player_id>/delete/",
        views.player_delete,
        name="player_delete",
    ),
]
