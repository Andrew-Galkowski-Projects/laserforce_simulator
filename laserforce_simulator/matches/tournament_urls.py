"""LG-02a — Tournament URL patterns mounted at ``/tournaments/`` by the
project URLconf. No ``app_name`` so reverse uses the bare names.

ANG-01f-1 — the views live in the ``matches.tournament_views`` package, one
module per area.
"""

from django.urls import path

from .tournament_views import detail, draw, list_create, play, setup

urlpatterns = [
    path("", list_create.tournament_list, name="tournament_list"),
    path("create/", list_create.tournament_create, name="tournament_create"),
    path(
        "<int:tournament_id>/",
        detail.tournament_detail,
        name="tournament_detail",
    ),
    path(
        "<int:tournament_id>/reseed/",
        setup.tournament_reseed,
        name="tournament_reseed",
    ),
    path(
        "<int:tournament_id>/lock/",
        setup.tournament_lock,
        name="tournament_lock",
    ),
    path(
        "<int:tournament_id>/play-next/",
        play.tournament_play_next,
        name="tournament_play_next",
    ),
    path(
        "<int:tournament_id>/play-all/",
        play.tournament_play_all,
        name="tournament_play_all",
    ),
    path(
        "<int:tournament_id>/play-status/<str:job_id>/",
        play.tournament_play_status,
        name="tournament_play_status",
    ),
    path(
        "<int:tournament_id>/import-participants/",
        setup.tournament_import_participants,
        name="tournament_import_participants",
    ),
    # LG-02x-1 — Random-Draw player-pool intake, draw, re-roll, hand-edit.
    path(
        "<int:tournament_id>/pool/add-existing/",
        draw.tournament_pool_add_existing,
        name="tournament_pool_add_existing",
    ),
    path(
        "<int:tournament_id>/pool/generate/",
        draw.tournament_pool_generate,
        name="tournament_pool_generate",
    ),
    path(
        "<int:tournament_id>/pool/import/",
        draw.tournament_pool_import,
        name="tournament_pool_import",
    ),
    path(
        "<int:tournament_id>/pool/remove/",
        draw.tournament_pool_remove,
        name="tournament_pool_remove",
    ),
    path(
        "<int:tournament_id>/draw/",
        draw.tournament_draw,
        name="tournament_draw",
    ),
    path(
        "<int:tournament_id>/draw/edit/",
        draw.tournament_draw_edit,
        name="tournament_draw_edit",
    ),
]
