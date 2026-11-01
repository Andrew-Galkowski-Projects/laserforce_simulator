"""ANG-10 (ADR-0049): Django serves the built SPA in every deploy.

WhiteNoise serves the bundle's files from ``SPA_ROOT``; these two views cover
the paths that are not files. Both are open to anonymous visitors: the SPA does
its own sign-in.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import (
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
)


@login_not_required
def spa_index(request: HttpRequest) -> HttpResponse:
    """``SPA_ROOT/index.html`` for ``/`` and every deep link; 404 without a bundle."""
    if settings.SPA_ROOT is None:
        raise Http404("No SPA bundle (SPA_ROOT is unset).")
    index = settings.SPA_ROOT / "index.html"
    if not index.is_file():
        raise Http404("No index.html in SPA_ROOT.")
    response = HttpResponse(index.read_bytes(), content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    return response


@login_not_required
def app_redirect(
    request: HttpRequest, path: str | None = None
) -> HttpResponsePermanentRedirect:
    """ADR-0046 links: ``/app/<path>`` moves to ``/<path>``, query string kept.

    Leading slashes and backslashes are dropped, so ``/app//evil.com`` stays on
    this site instead of becoming the protocol-relative ``//evil.com``.
    """
    local_path = (path or "").lstrip("/\\")
    query = request.META.get("QUERY_STRING", "")
    return HttpResponsePermanentRedirect(
        f"/{local_path}" + (f"?{query}" if query else "")
    )
