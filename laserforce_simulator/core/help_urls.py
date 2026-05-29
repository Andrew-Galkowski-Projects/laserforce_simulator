"""LG-01h — Help dropdown placeholder URLs mounted at ``/help/``.

The six routes here are sandbox-mode (no ``league_id`` kwarg) and all
resolve to the shared :func:`matches.views.coming_soon` view via the
locked ``feature_key`` vocabulary in ``_FEATURE_REGISTRY``. No
``app_name`` — bare URL namespace (LG-01a/b/c/d/e/f/g precedent).
"""

from django.urls import path

from matches import views

urlpatterns = [
    path(
        "overview/",
        views.coming_soon,
        {"feature_key": "help_overview"},
        name="coming_soon_help_overview",
    ),
    path(
        "changes/",
        views.coming_soon,
        {"feature_key": "help_changes"},
        name="coming_soon_help_changes",
    ),
    path(
        "custom-rosters/",
        views.coming_soon,
        {"feature_key": "help_custom_rosters"},
        name="coming_soon_help_custom_rosters",
    ),
    path(
        "debugging/",
        views.coming_soon,
        {"feature_key": "help_debugging"},
        name="coming_soon_help_debugging",
    ),
    path(
        "lol-gm-forums/",
        views.coming_soon,
        {"feature_key": "help_lol_gm_forums"},
        name="coming_soon_help_lol_gm_forums",
    ),
    path(
        "zen-gm-forums/",
        views.coming_soon,
        {"feature_key": "help_zen_gm_forums"},
        name="coming_soon_help_zen_gm_forums",
    ),
]
