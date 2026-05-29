"""LG-01h — Tools dropdown placeholder URLs mounted at ``/tools/``.

The four routes here are sandbox-mode (no ``league_id`` kwarg) and all
resolve to the shared :func:`matches.views.coming_soon` view via the
locked ``feature_key`` vocabulary in ``_FEATURE_REGISTRY``. No
``app_name`` — bare URL namespace (LG-01a/b/c/d/e/f/g precedent).
"""

from django.urls import path

from matches import views

urlpatterns = [
    path(
        "achievements/",
        views.coming_soon,
        {"feature_key": "tools_achievements"},
        name="coming_soon_tools_achievements",
    ),
    path(
        "screenshot/",
        views.coming_soon,
        {"feature_key": "tools_screenshot"},
        name="coming_soon_tools_screenshot",
    ),
    path(
        "debug-mode/",
        views.coming_soon,
        {"feature_key": "tools_debug_mode"},
        name="coming_soon_tools_debug_mode",
    ),
    path(
        "reset-db/",
        views.coming_soon,
        {"feature_key": "tools_reset_db"},
        name="coming_soon_tools_reset_db",
    ),
]
