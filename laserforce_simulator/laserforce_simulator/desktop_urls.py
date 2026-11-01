"""ANG-09 spike: the Desktop App's URLconf (spike/ang-09 only, never merged).

Adds the desktop sign-in, the ``desktop`` flag on ``accounts/me/`` and the SPA
fallback in front of the Hosted URLconf.
"""

from django.urls import path, re_path

from accounts.desktop import DesktopLoginView, DesktopMeAPIView, spa_index

from .urls import urlpatterns as hosted_urlpatterns

urlpatterns = [
    path("api/accounts/desktop-login/", DesktopLoginView.as_view()),
    path("api/accounts/me/", DesktopMeAPIView.as_view()),
    *hosted_urlpatterns,
    # Any other path is an SPA route (ADR-0047's nginx fallback).
    re_path(r"^(?!api/|admin/|media/|static/).*$", spa_index),
]
