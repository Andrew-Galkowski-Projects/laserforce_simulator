"""The one view ``core`` keeps (ANG-08, ADR-0047): ``serve_media``.

The map list / editor template views were deleted; the SPA drives the map
editor through ``/api/maps/`` (``core/api_views.py``).
"""

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.views.static import serve


def serve_media(request: HttpRequest, path: str) -> HttpResponse:
    """UXP-02 — serve an uploaded file from ``MEDIA_ROOT`` under ``DEBUG`` (dev,
    as ``static()`` did) or when ``SERVE_MEDIA`` is on (self-hosting without
    R2); 404 otherwise. ``serve`` rejects paths that escape the document root."""
    if not (settings.DEBUG or settings.SERVE_MEDIA):
        raise Http404()
    return serve(request, path, document_root=settings.MEDIA_ROOT)
