"""LG-02a — Tournament URL patterns mounted at ``/tournaments/`` by the
project URLconf. No ``app_name`` so reverse uses the bare names.
"""

from django.urls import path

from . import tournament_views as views

urlpatterns = [
    path("", views.tournament_list, name="tournament_list"),
    path("create/", views.tournament_create, name="tournament_create"),
    path(
        "<int:tournament_id>/",
        views.tournament_detail,
        name="tournament_detail",
    ),
    path(
        "<int:tournament_id>/reseed/",
        views.tournament_reseed,
        name="tournament_reseed",
    ),
    path(
        "<int:tournament_id>/lock/",
        views.tournament_lock,
        name="tournament_lock",
    ),
    path(
        "<int:tournament_id>/play-next/",
        views.tournament_play_next,
        name="tournament_play_next",
    ),
    path(
        "<int:tournament_id>/play-all/",
        views.tournament_play_all,
        name="tournament_play_all",
    ),
    path(
        "<int:tournament_id>/play-status/<str:job_id>/",
        views.tournament_play_status,
        name="tournament_play_status",
    ),
    path(
        "<int:tournament_id>/import-participants/",
        views.tournament_import_participants,
        name="tournament_import_participants",
    ),
    # LG-02x-1 — Random-Draw player-pool intake, draw, re-roll, hand-edit.
    path(
        "<int:tournament_id>/pool/add-existing/",
        views.tournament_pool_add_existing,
        name="tournament_pool_add_existing",
    ),
    path(
        "<int:tournament_id>/pool/generate/",
        views.tournament_pool_generate,
        name="tournament_pool_generate",
    ),
    path(
        "<int:tournament_id>/pool/import/",
        views.tournament_pool_import,
        name="tournament_pool_import",
    ),
    path(
        "<int:tournament_id>/pool/remove/",
        views.tournament_pool_remove,
        name="tournament_pool_remove",
    ),
    path(
        "<int:tournament_id>/draw/",
        views.tournament_draw,
        name="tournament_draw",
    ),
    path(
        "<int:tournament_id>/draw/edit/",
        views.tournament_draw_edit,
        name="tournament_draw_edit",
    ),
]
