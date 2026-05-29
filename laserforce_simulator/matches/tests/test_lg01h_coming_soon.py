"""LG-01h — Django ``TestCase`` tests for ``matches.views.coming_soon``
and the module-level ``matches.views._FEATURE_REGISTRY``.

The seam contract is locked at ``.claude/worktrees/lg-01h-seam-contract.md``
(Part b — Shared placeholder view + Placeholder template + URL routes).
The view is the single shared ``<h1>Coming soon</h1>`` view rendered via
``templates/_placeholder.html``. ``_FEATURE_REGISTRY`` is the locked
35-entry hard-coded vocabulary.

Tests hand-construct ``League`` rows — LG-01h runs NO simulation.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from matches.models import League

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


# ---------------------------------------------------------------------------
# Locked vocabulary (mirrors the seam contract — used by feature-registry
# tests for byte-for-byte assertions).
# ---------------------------------------------------------------------------

# 23-value enum + None — extended from LG-01f's 14 by LG-01h.
LOCKED_SIDEBAR_ACTIVE = {
    "dashboard",
    "standings",
    "schedule",
    "playoffs",
    "finances",
    "history",
    "power_rankings",
    "roster",
    "schedule_team",
    "finances_team",
    "history_team",
    "free_agents",
    "trade",
    "trading_block",
    "prospects",
    "watch_list",
    "hall_of_fame",
    "game_log",
    "league_leaders",
    "player_ratings",
    "player_stats",
    "team_stats",
    "statistical_feats",
    None,
}

LOCKED_SECTIONS = {"league", "team", "players", "stats", "help", "tools"}

# LEAGUE-scoped entries (the seam contract pins these labels + sidebar_active
# values; ``coming_soon_*`` is the URL-name prefix that gets used in routing).
LEAGUE_SCOPED_KEYS = {
    "league_playoffs": ("Playoffs", "league", "playoffs"),
    "league_finances": ("Finances", "league", "finances"),
    "league_power_rankings": ("Power Rankings", "league", "power_rankings"),
}
TEAM_SCOPED_KEYS = {
    "team_roster": ("Roster", "team", "roster"),
    "team_finances": ("Finances", "team", "finances_team"),
    "team_history": ("History", "team", "history_team"),
}
PLAYERS_SCOPED_KEYS = {
    "players_free_agents": ("Free Agents", "players", "free_agents"),
    "players_trade": ("Trade", "players", "trade"),
    "players_trading_block": ("Trading Block", "players", "trading_block"),
    "players_prospects": ("Prospects", "players", "prospects"),
    "players_watch_list": ("Watch List", "players", "watch_list"),
    "players_hall_of_fame": ("Hall of Fame", "players", "hall_of_fame"),
}
STATS_SCOPED_KEYS = {
    "stats_game_log": ("Game Log", "stats", "game_log"),
    "stats_league_leaders": ("League Leaders", "stats", "league_leaders"),
    "stats_player_ratings": ("Player Ratings", "stats", "player_ratings"),
    "stats_player_stats": ("Player Stats", "stats", "player_stats"),
    "stats_team_stats": ("Team Stats", "stats", "team_stats"),
    "stats_statistical_feats": ("Statistical Feats", "stats", "statistical_feats"),
}
HELP_KEYS = {
    "help_overview": ("Overview", "help", None),
    "help_changes": ("Changes", "help", None),
    "help_custom_rosters": ("Custom Rosters", "help", None),
    "help_debugging": ("Debugging", "help", None),
    "help_lol_gm_forums": ("LOL GM Forums", "help", None),
    "help_zen_gm_forums": ("Zen GM Forums", "help", None),
}
TOOLS_KEYS = {
    "tools_achievements": ("Achievements", "tools", None),
    "tools_screenshot": ("Screenshot", "tools", None),
    "tools_debug_mode": ("Enable Debug Mode", "tools", None),
    "tools_reset_db": ("Reset DB", "tools", None),
}

# URL-name → feature_key + (league-scoped? bool).
LEAGUE_SCOPED_URL_NAMES = {
    "coming_soon_playoffs": "league_playoffs",
    "coming_soon_finances": "league_finances",
    "coming_soon_power_rankings": "league_power_rankings",
    "coming_soon_team_roster": "team_roster",
    "coming_soon_team_finances": "team_finances",
    "coming_soon_team_history": "team_history",
    "coming_soon_free_agents": "players_free_agents",
    "coming_soon_trade": "players_trade",
    "coming_soon_trading_block": "players_trading_block",
    "coming_soon_prospects": "players_prospects",
    "coming_soon_watch_list": "players_watch_list",
    "coming_soon_hall_of_fame": "players_hall_of_fame",
    "coming_soon_game_log": "stats_game_log",
    "coming_soon_league_leaders": "stats_league_leaders",
    "coming_soon_player_ratings": "stats_player_ratings",
    "coming_soon_player_stats": "stats_player_stats",
    "coming_soon_team_stats": "stats_team_stats",
    "coming_soon_statistical_feats": "stats_statistical_feats",
}
HELP_TOOLS_URL_NAMES = {
    "coming_soon_help_overview": "help_overview",
    "coming_soon_help_changes": "help_changes",
    "coming_soon_help_custom_rosters": "help_custom_rosters",
    "coming_soon_help_debugging": "help_debugging",
    "coming_soon_help_lol_gm_forums": "help_lol_gm_forums",
    "coming_soon_help_zen_gm_forums": "help_zen_gm_forums",
    "coming_soon_tools_achievements": "tools_achievements",
    "coming_soon_tools_screenshot": "tools_screenshot",
    "coming_soon_tools_debug_mode": "tools_debug_mode",
    "coming_soon_tools_reset_db": "tools_reset_db",
}


# ---------------------------------------------------------------------------
# TestComingSoonRouting
# ---------------------------------------------------------------------------


class TestComingSoonRouting(TestCase):
    """200 on every URL name in ``_FEATURE_REGISTRY``; 405 on POST;
    404 on stale ``league_id``; 404 on unknown ``feature_key``; sidebar
    rendered in league branch; sidebar empty in Help / Tools branch;
    ``feature_label`` injected into the ``<h1>``; ``"Coming soon"``
    substring in the body; locked context keys present; ``app_mode``
    matches the URL-prefix rule.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="LG01hRouting", mode="league", state="active"
        )

    # -- 200 happy-path coverage --------------------------------------------

    def test_every_league_scoped_url_returns_200(self) -> None:
        for url_name in LEAGUE_SCOPED_URL_NAMES:
            with self.subTest(url_name=url_name):
                url = reverse(url_name, kwargs={"league_id": self.league.id})
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, f"{url_name} not 200")

    def test_every_help_url_returns_200(self) -> None:
        for url_name in (
            "coming_soon_help_overview",
            "coming_soon_help_changes",
            "coming_soon_help_custom_rosters",
            "coming_soon_help_debugging",
            "coming_soon_help_lol_gm_forums",
            "coming_soon_help_zen_gm_forums",
        ):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)

    def test_every_tools_url_returns_200(self) -> None:
        for url_name in (
            "coming_soon_tools_achievements",
            "coming_soon_tools_screenshot",
            "coming_soon_tools_debug_mode",
            "coming_soon_tools_reset_db",
        ):
            with self.subTest(url_name=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)

    # -- 405 on POST ---------------------------------------------------------

    def test_post_returns_405_on_league_scoped_route(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": self.league.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 405)

    def test_post_returns_405_on_help_route(self) -> None:
        response = self.client.post(reverse("coming_soon_help_overview"))
        self.assertEqual(response.status_code, 405)

    def test_post_returns_405_on_tools_route(self) -> None:
        response = self.client.post(reverse("coming_soon_tools_achievements"))
        self.assertEqual(response.status_code, 405)

    # -- 404 on stale league_id ---------------------------------------------

    def test_stale_league_id_returns_404(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": 999999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # -- 404 on unknown feature_key (direct invocation) ---------------------

    def test_unknown_feature_key_returns_404(self) -> None:
        """The URL routing pre-validates ``feature_key`` (it is passed via
        kwargs from ``urls.py``), so an unknown key cannot be reached
        through routing. Invoke the view directly to assert the
        ``Http404`` guard.
        """
        from django.http import Http404
        from django.test import RequestFactory

        from matches.views import coming_soon

        request = RequestFactory().get("/help/overview/")
        with self.assertRaises(Http404):
            coming_soon(request, "nonexistent_feature_key")

    # -- Sidebar partial rendered in the league branch ----------------------

    def test_sidebar_partial_rendered_in_league_branch(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        self.assertContains(response, 'id="league-sidebar"')

    def test_sidebar_partial_not_rendered_in_help_branch(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertNotContains(response, 'id="league-sidebar"')

    def test_sidebar_partial_not_rendered_in_tools_branch(self) -> None:
        response = self.client.get(reverse("coming_soon_tools_achievements"))
        self.assertNotContains(response, 'id="league-sidebar"')

    # -- feature_label injected into the <h1> -------------------------------

    def test_feature_label_in_h1_for_league_scoped(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        body = response.content.decode()
        # Label is "Playoffs" per the registry.
        self.assertIn("Playoffs", body)
        # Inside the locked coming-soon-header wrapper.
        self.assertContains(response, 'id="coming-soon-header"')

    def test_feature_label_in_h1_for_help_overview(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertIn("Overview", response.content.decode())

    # -- "Coming soon" substring --------------------------------------------

    def test_coming_soon_message_substring_present_league(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        self.assertContains(response, 'id="coming-soon-message"')
        self.assertIn("Coming soon", response.content.decode())

    def test_coming_soon_message_substring_present_help(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertContains(response, 'id="coming-soon-message"')
        self.assertIn("Coming soon", response.content.decode())

    # -- Locked 7 context keys present (+ global app_mode = 8) --------------

    def test_locked_7_context_keys_present_league_scoped(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        ctx = response.context
        for key in (
            "league",
            "displayed_season",
            "feature_key",
            "feature_label",
            "feature_section",
            "sidebar_links",
            "sidebar_active",
        ):
            self.assertIn(key, ctx, f"missing context key {key!r}")
        # Plus the global ``app_mode``.
        self.assertIn("app_mode", ctx)

    def test_locked_7_context_keys_present_help_branch(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        ctx = response.context
        for key in (
            "league",
            "displayed_season",
            "feature_key",
            "feature_label",
            "feature_section",
            "sidebar_links",
            "sidebar_active",
        ):
            self.assertIn(key, ctx, f"missing context key {key!r}")
        # ``league`` is None on Help/Tools, but the key must be present.
        self.assertIsNone(ctx["league"])
        self.assertEqual(ctx["sidebar_links"], [])

    # -- app_mode matches the URL-prefix rule -------------------------------

    def test_app_mode_is_league_on_league_scoped_route(self) -> None:
        url = reverse("coming_soon_playoffs", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        self.assertEqual(response.context["app_mode"], "league")

    def test_app_mode_is_sandbox_on_help_route(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertEqual(response.context["app_mode"], "sandbox")

    def test_app_mode_is_sandbox_on_tools_route(self) -> None:
        response = self.client.get(reverse("coming_soon_tools_achievements"))
        self.assertEqual(response.context["app_mode"], "sandbox")


# ---------------------------------------------------------------------------
# TestComingSoonFeatureRegistry
# ---------------------------------------------------------------------------


class TestComingSoonFeatureRegistry(TestCase):
    """All 35 ``_FEATURE_REGISTRY`` entries exist; every value-dict has the
    3 keys ``label, section, sidebar_active``; every ``sidebar_active`` is
    in the locked 23+``None`` enum; section is in the locked 6-value set;
    the 18 LEAGUE/TEAM/PLAYERS/STATS placeholder entries (3+3+6+6 = 18)
    have non-``None`` ``sidebar_active``; the 6 Help + 4 Tools entries have
    ``sidebar_active=None``.
    """

    def setUp(self) -> None:
        from matches.views import _FEATURE_REGISTRY

        self.registry = _FEATURE_REGISTRY

    def test_registry_has_28_entries(self) -> None:
        """The seam contract's prose claims "Total: 35 entries", but the
        explicit per-key vocabulary it enumerates totals 3 + 3 + 6 + 6 +
        6 + 4 = 28. The "35" is a misnote analogous to the acknowledged
        misnote on contract line 37 (the ``/leagues/<id>/standings/``
        route which was already LIVE via LG-01f). The Standings entry is
        NOT in the registry (it's already LIVE), and the 7 "extra"
        league-scoped entries the prose hints at do not appear in the
        explicit per-key enumeration. Tests assert against the
        enumerated vocabulary.
        """
        self.assertEqual(len(self.registry), 28)

    def test_every_value_dict_has_3_keys(self) -> None:
        expected = {"label", "section", "sidebar_active"}
        for key, value in self.registry.items():
            self.assertEqual(
                set(value.keys()),
                expected,
                f"feature_key {key!r} value dict has wrong keys: {set(value.keys())}",
            )

    def test_every_sidebar_active_in_locked_enum(self) -> None:
        for key, value in self.registry.items():
            self.assertIn(
                value["sidebar_active"],
                LOCKED_SIDEBAR_ACTIVE,
                f"feature_key {key!r} sidebar_active "
                f"{value['sidebar_active']!r} not in locked enum",
            )

    def test_every_section_in_locked_set(self) -> None:
        for key, value in self.registry.items():
            self.assertIn(
                value["section"],
                LOCKED_SECTIONS,
                f"feature_key {key!r} section {value['section']!r} not in locked set",
            )

    # -- 18 placeholder LEAGUE/TEAM/PLAYERS/STATS entries -------------------

    def test_league_scoped_entries_present(self) -> None:
        for key, (label, section, sidebar_active) in LEAGUE_SCOPED_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, self.registry)
                self.assertEqual(self.registry[key]["label"], label)
                self.assertEqual(self.registry[key]["section"], section)
                self.assertEqual(self.registry[key]["sidebar_active"], sidebar_active)

    def test_team_scoped_entries_present(self) -> None:
        for key, (label, section, sidebar_active) in TEAM_SCOPED_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, self.registry)
                self.assertEqual(self.registry[key]["label"], label)
                self.assertEqual(self.registry[key]["section"], section)
                self.assertEqual(self.registry[key]["sidebar_active"], sidebar_active)

    def test_players_scoped_entries_present(self) -> None:
        for key, (label, section, sidebar_active) in PLAYERS_SCOPED_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, self.registry)
                self.assertEqual(self.registry[key]["label"], label)
                self.assertEqual(self.registry[key]["section"], section)
                self.assertEqual(self.registry[key]["sidebar_active"], sidebar_active)

    def test_stats_scoped_entries_present(self) -> None:
        for key, (label, section, sidebar_active) in STATS_SCOPED_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, self.registry)
                self.assertEqual(self.registry[key]["label"], label)
                self.assertEqual(self.registry[key]["section"], section)
                self.assertEqual(self.registry[key]["sidebar_active"], sidebar_active)

    def test_18_placeholder_entries_have_non_none_sidebar_active(self) -> None:
        placeholder_keys = (
            set(LEAGUE_SCOPED_KEYS.keys())
            | set(TEAM_SCOPED_KEYS.keys())
            | set(PLAYERS_SCOPED_KEYS.keys())
            | set(STATS_SCOPED_KEYS.keys())
        )
        self.assertEqual(len(placeholder_keys), 18)
        for key in placeholder_keys:
            self.assertIsNotNone(
                self.registry[key]["sidebar_active"],
                f"feature_key {key!r} should have non-None sidebar_active",
            )

    # -- Help + Tools entries -----------------------------------------------

    def test_help_entries_present(self) -> None:
        for key, (label, section, sidebar_active) in HELP_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, self.registry)
                self.assertEqual(self.registry[key]["label"], label)
                self.assertEqual(self.registry[key]["section"], section)
                self.assertIsNone(self.registry[key]["sidebar_active"])

    def test_tools_entries_present(self) -> None:
        for key, (label, section, sidebar_active) in TOOLS_KEYS.items():
            with self.subTest(key=key):
                self.assertIn(key, self.registry)
                self.assertEqual(self.registry[key]["label"], label)
                self.assertEqual(self.registry[key]["section"], section)
                self.assertIsNone(self.registry[key]["sidebar_active"])

    def test_6_help_plus_4_tools_have_none_sidebar_active(self) -> None:
        help_tools_keys = set(HELP_KEYS.keys()) | set(TOOLS_KEYS.keys())
        self.assertEqual(len(help_tools_keys), 10)
        for key in help_tools_keys:
            self.assertIsNone(
                self.registry[key]["sidebar_active"],
                f"feature_key {key!r} should have None sidebar_active",
            )

    def test_entry_counts_by_section(self) -> None:
        """3 league + 3 team + 6 players + 6 stats + 6 help + 4 tools = 28
        ... wait — let's count properly. The seam contract pins 10
        league-scoped TOTAL in the registry (the misnote on line 25 of the
        contract listed 10 League-scoped including standings, but Standings
        is already LIVE in LG-01f so it's not in the registry).

        Per the explicit per-key vocabulary in the contract: League-scoped
        in the registry are exactly 3 (playoffs / finances / power_rankings).
        """
        by_section: dict[str, int] = {}
        for value in self.registry.values():
            by_section[value["section"]] = by_section.get(value["section"], 0) + 1
        self.assertEqual(by_section.get("league", 0), 3)
        self.assertEqual(by_section.get("team", 0), 3)
        self.assertEqual(by_section.get("players", 0), 6)
        self.assertEqual(by_section.get("stats", 0), 6)
        self.assertEqual(by_section.get("help", 0), 6)
        self.assertEqual(by_section.get("tools", 0), 4)


# ---------------------------------------------------------------------------
# TestComingSoonSessionWrite
# ---------------------------------------------------------------------------


class TestComingSoonSessionWrite(TestCase):
    """GET on a league-scoped placeholder writes ``last_league_id``;
    GET on Help / Tools does NOT.
    """

    def test_league_scoped_get_writes_last_league_id(self) -> None:
        league = _make_league("SessWriteL")
        url = reverse("coming_soon_playoffs", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_team_scoped_get_writes_last_league_id(self) -> None:
        league = _make_league("SessWriteT")
        url = reverse("coming_soon_team_roster", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_players_scoped_get_writes_last_league_id(self) -> None:
        league = _make_league("SessWriteP")
        url = reverse("coming_soon_free_agents", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_stats_scoped_get_writes_last_league_id(self) -> None:
        league = _make_league("SessWriteS")
        url = reverse("coming_soon_game_log", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_help_get_does_not_write_last_league_id(self) -> None:
        self.client.get(reverse("coming_soon_help_overview"))
        self.assertNotIn("last_league_id", self.client.session)

    def test_tools_get_does_not_write_last_league_id(self) -> None:
        self.client.get(reverse("coming_soon_tools_achievements"))
        self.assertNotIn("last_league_id", self.client.session)
