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

import pytest
from django.conf import settings
from django.test import Client
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.settings import api_settings

# ANG-08 D1: the Admin login is the only Django login page left (ADR-0047).
HTML_EXEMPT_VIEWS = {
    "django.contrib.admin.sites.login",
}

ANONYMOUS_API_VIEWS = {
    "accounts.api_views.RegisterAPIView",
    # ANG-03 (ADR-0041) - JWT login / refresh / logout must work while anonymous.
    "accounts.token_api_views.TokenObtainAPIView",
    "accounts.token_api_views.TokenRefreshAPIView",
    "accounts.token_api_views.TokenLogoutAPIView",
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


def _routes(patterns: list[Any], prefix: str = "") -> Iterator[str]:
    for pattern in patterns:
        route = prefix + str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            yield from _routes(pattern.url_patterns, route)
        elif isinstance(pattern, URLPattern):
            yield route


def test_django_serves_only_admin_api_and_media() -> None:
    # ANG-08 (ADR-0047): the SPA owns every other path.
    allowed = ("admin/", "api/", "^media/")
    stray = [
        r for r in _routes(get_resolver().url_patterns) if not r.startswith(allowed)
    ]
    assert stray == []


def test_the_admin_login_is_the_only_html_exemption() -> None:
    assert HTML_EXEMPT_VIEWS == {"django.contrib.admin.sites.login"}


@pytest.mark.django_db
def test_admin_login_renders_its_form_for_an_anonymous_visitor(client: Client) -> None:
    # Admin needs Django's own three context processors (ANG-08).
    response = client.get(reverse("admin:login"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="login-form"' in body
    assert 'name="username"' in body
    assert 'name="password"' in body


def test_no_project_template_remains() -> None:
    templates_dir = settings.BASE_DIR / "templates"
    assert list(templates_dir.rglob("*.html")) == []


def test_no_custom_context_processor_is_registered() -> None:
    processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    custom = [p for p in processors if p.startswith(("core.", "accounts."))]
    assert custom == []


def test_anonymous_api_views_are_explicitly_allow_any() -> None:
    by_name = {
        _dotted(callback.cls): callback  # type: ignore[attr-defined]
        for callback in _callbacks()
        if hasattr(callback, "cls")
    }
    for name in ANONYMOUS_API_VIEWS:
        assert _api_permissions(by_name[name]) == [AllowAny]
