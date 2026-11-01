"""Root URLconf: Django owns ``admin/``, ``api/``, ``static/`` and ``media/``.

ANG-10 (ADR-0049): every other path is the Angular SPA, served by Django
itself from ``SPA_ROOT``: WhiteNoise serves the bundle's files, and
``spa_index`` answers ``/`` and every deep link with ``index.html``.
"""

from django.contrib import admin
from django.urls import path, include, re_path
from core import spa
from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("laserforce_simulator.api_urls")),
    # UXP-02: uploaded media in dev (DEBUG) and on the self-hosted stack
    # (SERVE_MEDIA); replaces the DEBUG-only static() helper. With R2,
    # MEDIA_URL points at the bucket and this route is never linked.
    re_path(r"^media/(?P<path>.*)$", core_views.serve_media, name="serve_media"),
    # ADR-0046 links: /app and /app/<path> move to / and /<path>.
    re_path(r"^app(?:/(?P<path>.*))?$", spa.app_redirect),
    # Any other path is an SPA route. Django's own prefixes (and /admin, which
    # APPEND_SLASH redirects) never fall back to index.html.
    re_path(r"^(?!(?:api|admin|static|media)/|admin$).*$", spa.spa_index),
]
