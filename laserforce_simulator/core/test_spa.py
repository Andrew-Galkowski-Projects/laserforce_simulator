"""ANG-10 (ADR-0049): Django serves the built SPA from ``SPA_ROOT``.

WhiteNoise serves the bundle's files; ``core.spa.spa_index`` answers ``/`` and
every deep link with ``index.html``; ``core.spa.app_redirect`` moves ADR-0046
``/app/<path>`` links. Each test writes a tiny bundle to a temp dir and builds a
fresh ``Client``: WhiteNoise reads its settings when the middleware is built,
on the client's first request.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings

INDEX_HTML = '<!doctype html><html><head><base href="/"></head><body><app-root></app-root></body></html>'


def _write_bundle(root: Path) -> None:
    (root / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (root / "main-ABCD1234.js").write_text("console.log(1);", encoding="utf-8")
    (root / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    (root / "fonts").mkdir()
    (root / "fonts" / "mono-400-normal-C5JECUCT.woff2").write_bytes(b"wOF2")


class SpaBundleMixin:
    """A temp bundle wired in as ``SPA_ROOT`` / ``WHITENOISE_ROOT``."""

    def setUp(self) -> None:
        super().setUp()
        self.spa_root = Path(tempfile.mkdtemp(prefix="lf-spa-"))
        self.addCleanup(shutil.rmtree, self.spa_root, ignore_errors=True)
        _write_bundle(self.spa_root)
        overrides = override_settings(
            SPA_ROOT=self.spa_root, WHITENOISE_ROOT=self.spa_root
        )
        overrides.enable()
        self.addCleanup(overrides.disable)
        self.client = Client()


class SpaIndexTests(SpaBundleMixin, TestCase):
    def test_root_serves_index_anonymously_no_cache(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<app-root>", response.content)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertEqual(response["Cache-Control"], "no-cache")

    def test_deep_link_serves_index(self) -> None:
        response = self.client.get("/leagues/3/seasons/7/standings")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<app-root>", response.content)
        self.assertEqual(response["Cache-Control"], "no-cache")

    def test_django_prefixes_never_serve_index(self) -> None:
        for path in (
            "/api/no-such-route/",
            "/static/nope.css",
            "/media/nope.png",
            "/admin/nope/",
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotIn(b"<app-root>", response.content)
                self.assertNotEqual(response.status_code, 200)

    def test_admin_without_slash_still_redirects_to_admin(self) -> None:
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/admin/")

    def test_missing_index_file_is_404(self) -> None:
        (self.spa_root / "index.html").unlink()
        self.assertEqual(self.client.get("/teams/").status_code, 404)


class SpaRootUnsetTests(TestCase):
    def test_root_and_deep_link_are_404_without_spa_root(self) -> None:
        self.assertIsNone(settings.SPA_ROOT)
        client = Client()
        self.assertEqual(client.get("/").status_code, 404)
        self.assertEqual(client.get("/teams/3/").status_code, 404)


class AppRedirectTests(SpaBundleMixin, TestCase):
    def test_app_path_redirects_with_query(self) -> None:
        response = self.client.get("/app/teams/3", {"x": "1"})
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], "/teams/3?x=1")

    def test_bare_app_redirects_to_root(self) -> None:
        for path in ("/app", "/app/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], "/")

    def test_app_redirect_never_leaves_the_site(self) -> None:
        # /app//evil.com must not become a protocol-relative //evil.com.
        for path, location in (
            ("/app//evil.com", "/evil.com"),
            ("/app//evil.com/x", "/evil.com/x"),
            ("/app/\\evil.com", "/evil.com"),
            ("/app/\\/evil.com", "/evil.com"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response["Location"], location)

    def test_app_prefix_needs_a_boundary(self) -> None:
        # /apple is an SPA path, not an /app link.
        response = self.client.get("/apple")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<app-root>", response.content)


class SpaFileTests(SpaBundleMixin, TestCase):
    def test_hashed_bundle_is_immutable(self) -> None:
        response = self.client.get("/main-ABCD1234.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("immutable", response["Cache-Control"])

    def test_unhashed_file_is_not_immutable(self) -> None:
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("immutable", response.get("Cache-Control", ""))

    def test_direct_index_html_is_no_cache(self) -> None:
        response = self.client.get("/index.html")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-cache")

    def test_font_under_fonts_is_served_immutable(self) -> None:
        response = self.client.get("/fonts/mono-400-normal-C5JECUCT.woff2")
        self.assertEqual(response.status_code, 200)
        self.assertIn("immutable", response["Cache-Control"])


class ImmutableFileTestPatternTests(SimpleTestCase):
    """``WHITENOISE_IMMUTABLE_FILE_TEST`` against names from a real build."""

    def test_matches_angular_hashed_names(self) -> None:
        pattern = re.compile(settings.WHITENOISE_IMMUTABLE_FILE_TEST)
        for url in (
            "/main-774TPCF3.js",
            "/chunk-C_1nWhzy.js",
            "/chunk-w-hfC1KY.js",
            "/styles-UOQYVNHS.css",
            "/fonts/jetbrains-mono-cyrillic-400-normal-C5JECUCT.woff2",
            "/fonts/jetbrains-mono-cyrillic-400-normal-FGCZYWMY.woff",
        ):
            with self.subTest(url=url):
                self.assertIsNotNone(pattern.search(url))

    def test_skips_unhashed_names(self) -> None:
        pattern = re.compile(settings.WHITENOISE_IMMUTABLE_FILE_TEST)
        for url in ("/favicon.ico", "/index.html", "/main.js", "/styles.css"):
            with self.subTest(url=url):
                self.assertIsNone(pattern.search(url))
