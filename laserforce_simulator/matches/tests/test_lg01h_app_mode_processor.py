"""LG-01h — Django ``TestCase`` tests for
``core.context_processors.app_mode``.

The seam contract is locked at ``.claude/worktrees/lg-01h-seam-contract.md``
(Part a — Mode-detecting context processor). The processor returns the
single-key dict ``{"app_mode": "league" | "sandbox"}``. Path-prefix rule
(locked): ``request.path.startswith("/leagues/")`` or
``request.path.startswith("/seasons/")`` ⇒ ``"league"``; everything else
(including ``/``, ``/teams/``, ``/players/``, ``/matches/``, ``/maps/``,
``/help/*``, ``/tools/*``) ⇒ ``"sandbox"``.

Read via ``getattr(request, "path", "/")`` so a ``RequestFactory()``-built
request without ``.path`` doesn't crash.
"""

from __future__ import annotations

from django.test import RequestFactory, TestCase

# ---------------------------------------------------------------------------
# TestAppModeContextProcessor
# ---------------------------------------------------------------------------


class TestAppModeContextProcessor(TestCase):
    """Exercise the processor function directly via ``RequestFactory()``.

    The locked literals are ``"league"`` and ``"sandbox"``; the locked
    context key is ``"app_mode"``; the returned dict has EXACTLY that
    one key.
    """

    def setUp(self) -> None:
        self.factory = RequestFactory()
        # Late import to ensure the Code agent's new function is resolved
        # at test time; tests fail before the Code agent lands.
        from core.context_processors import app_mode

        self.app_mode = app_mode

    # -- Sandbox branch ------------------------------------------------------

    def test_root_path_is_sandbox(self) -> None:
        request = self.factory.get("/")
        result = self.app_mode(request)
        self.assertEqual(result, {"app_mode": "sandbox"})

    def test_teams_path_is_sandbox(self) -> None:
        request = self.factory.get("/teams/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_players_path_is_sandbox(self) -> None:
        request = self.factory.get("/players/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_matches_path_is_sandbox(self) -> None:
        request = self.factory.get("/matches/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_maps_path_is_sandbox(self) -> None:
        request = self.factory.get("/maps/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_help_overview_path_is_sandbox(self) -> None:
        request = self.factory.get("/help/overview/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_tools_achievements_path_is_sandbox(self) -> None:
        request = self.factory.get("/tools/achievements/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    # -- League branch -------------------------------------------------------

    def test_leagues_index_path_is_league(self) -> None:
        request = self.factory.get("/leagues/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_league_detail_path_is_league(self) -> None:
        request = self.factory.get("/leagues/1/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_league_history_path_is_league(self) -> None:
        request = self.factory.get("/leagues/1/history/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_season_detail_path_is_league(self) -> None:
        request = self.factory.get("/seasons/1/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_season_standings_path_is_league(self) -> None:
        request = self.factory.get("/seasons/1/standings/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    # -- Edge cases ----------------------------------------------------------

    def test_empty_path_is_sandbox(self) -> None:
        """An empty ``request.path`` does not start with ``/leagues/`` or
        ``/seasons/`` so it should resolve to sandbox without crashing.
        """
        request = self.factory.get("/")
        request.path = ""
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_missing_path_attribute_does_not_crash(self) -> None:
        """A request without a ``.path`` attribute must not crash — the
        processor reads via ``getattr(request, "path", "/")`` per the
        seam contract.
        """
        request = self.factory.get("/")
        # Defensively remove the path attribute. ``del`` is the explicit
        # way to recreate a no-path request that ``getattr`` must handle.
        try:
            del request.path
        except AttributeError:
            # Some Django versions store ``path`` as a property — fall
            # back to setting it via the dict directly. Either way the
            # processor must not raise AttributeError.
            request.__dict__.pop("path", None)
        result = self.app_mode(request)
        self.assertIn("app_mode", result)
        # Default ("/" via getattr) ⇒ sandbox.
        self.assertEqual(result["app_mode"], "sandbox")

    # -- Returned shape ------------------------------------------------------

    def test_returned_dict_has_exactly_one_key_app_mode(self) -> None:
        request = self.factory.get("/")
        result = self.app_mode(request)
        self.assertEqual(list(result.keys()), ["app_mode"])

    def test_returned_value_is_one_of_the_two_literals(self) -> None:
        for path in ("/", "/teams/", "/leagues/", "/seasons/1/", "/help/overview/"):
            request = self.factory.get(path)
            result = self.app_mode(request)
            self.assertIn(result["app_mode"], ("league", "sandbox"))
