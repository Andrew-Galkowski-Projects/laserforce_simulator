"""Root URLconf: Django serves only ``admin/``, ``api/`` and ``media/``.

ANG-08 (ADR-0047): the Angular SPA, served by nginx at ``/``, owns every
other path.
"""

from django.contrib import admin
from django.urls import path, include, re_path
from core import views as core_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("laserforce_simulator.api_urls")),
    # UXP-02: uploaded media in dev (DEBUG) and on the self-hosted stack
    # (SERVE_MEDIA); replaces the DEBUG-only static() helper. With R2,
    # MEDIA_URL points at the bucket and this route is never linked.
    re_path(r"^media/(?P<path>.*)$", core_views.serve_media, name="serve_media"),
]
