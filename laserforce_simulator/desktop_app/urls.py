"""DESK-01: the Desktop App's URLconf, the Hosted one plus the Launch code route."""

from django.urls import path

from laserforce_simulator.urls import urlpatterns as hosted_urlpatterns

from .launch_code import DesktopLoginView

urlpatterns = [
    path(
        "api/accounts/desktop-login/",
        DesktopLoginView.as_view(),
        name="api_desktop_login",
    ),
    *hosted_urlpatterns,
]
