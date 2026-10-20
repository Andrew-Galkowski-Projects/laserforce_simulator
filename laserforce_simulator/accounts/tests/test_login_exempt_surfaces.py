"""The global login gate's exemptions, checked against the live URLconf.

`LoginRequiredMiddleware` lets through any view whose callback carries
``login_required = False``. Two ways a view gets that mark:

- an explicit ``@login_not_required`` on an HTML view: every one must be on
  ``HTML_EXEMPT_VIEWS``, or an anonymous visitor could reach it;
- DRF's ``as_view()``, which marks **every** DRF view exempt by itself (DRF
  3.15+). The API is then gated only by its permission classes, so every DRF
  view must require ``IsAuthenticated`` unless it is on ``ANONYMOUS_API_VIEWS``.

Adding a surface to either allowlist is a deliberate security decision.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.settings import api_settings

HTML_EXEMPT_VIEWS = {
    "django.contrib.admin.sites.login",
    "django.contrib.auth.views.LoginView",
    "django.contrib.auth.views.LogoutView",
    "django.contrib.auth.views.PasswordChangeView",
    "django.contrib.auth.views.PasswordChangeDoneView",
    "accounts.views.register",
}

ANONYMOUS_API_VIEWS = {
    "accounts.api_views.RegisterAPIView",
}


def _patterns(patterns: list[Any]) -> Iterator[URLPattern]:
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _patterns(pattern.url_patterns)
        elif isinstance(pattern, URLPattern):
            yield pattern


def _dotted(obj: Any) -> str:
    return f"{obj.__module__}.{obj.__name__}"


def _callbacks() -> list[Callable[..., Any]]:
    return [pattern.callback for pattern in _patterns(get_resolver().url_patterns)]


def _api_permissions(callback: Callable[..., Any]) -> list[type]:
    initkwargs: dict[str, Any] = getattr(callback, "initkwargs", None) or {}
    default: list[type] = (
        getattr(
            callback.cls,  # type: ignore[attr-defined]
            "permission_classes",
            api_settings.DEFAULT_PERMISSION_CLASSES,
        )
        or []
    )
    if "permission_classes" in initkwargs:
        return list(initkwargs["permission_classes"])
    return list(default)


def test_html_login_exemptions_match_the_allowlist() -> None:
    exempt = set()
    for callback in _callbacks():
        if hasattr(callback, "cls"):
            continue  # DRF: covered by the permission test below
        if getattr(callback, "login_required", True) is False:
            owner = getattr(callback, "view_class", None) or callback
            exempt.add(_dotted(owner))
    assert exempt == HTML_EXEMPT_VIEWS


def test_every_api_view_requires_authentication_unless_allowlisted() -> None:
    anonymous = set()
    for callback in _callbacks():
        if not hasattr(callback, "cls"):
            continue
        permissions = _api_permissions(callback)
        if IsAuthenticated not in permissions:
            anonymous.add(_dotted(callback.cls))  # type: ignore[attr-defined]
    assert anonymous == ANONYMOUS_API_VIEWS


def test_anonymous_api_views_are_explicitly_allow_any() -> None:
    by_name = {
        _dotted(callback.cls): callback  # type: ignore[attr-defined]
        for callback in _callbacks()
        if hasattr(callback, "cls")
    }
    for name in ANONYMOUS_API_VIEWS:
        assert _api_permissions(by_name[name]) == [AllowAny]
