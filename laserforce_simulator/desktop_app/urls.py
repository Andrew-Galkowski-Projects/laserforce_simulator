"""DESK-01: the Desktop App's URLconf, the Hosted one plus the Launch code route.

DESK-04 adds the Update check (``api/accounts/desktop-update/``).
"""

from django.urls import path

from laserforce_simulator.urls import urlpatterns as hosted_urlpatterns

from .launch_code import DesktopLoginView
from .update_check import DesktopUpdateView

urlpatterns = [
    path(
        "api/accounts/desktop-login/",
        DesktopLoginView.as_view(),
        name="api_desktop_login",
    ),
    path(
        "api/accounts/desktop-update/",
        DesktopUpdateView.as_view(),
        name="api_desktop_update",
    ),
    *hosted_urlpatterns,
]
