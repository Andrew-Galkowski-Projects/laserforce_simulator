"""LG-01h — Django ``TestCase`` tests for ``matches.views.coming_soon``
and the module-level ``matches.views._FEATURE_REGISTRY``.

The view is the single shared ``<h1>Coming soon</h1>`` view rendered via
``templates/_placeholder.html``.

**Updated by LG-01z** (sidebar placeholder backlog): 11 of the original
18 LEAGUE/TEAM/PLAYERS/STATS placeholders became real read-only screens
(Power Rankings, Team Roster, Team History, Free Agents, Watch List, and
all 6 STATS screens). Their ``coming_soon_*`` routes + ``_FEATURE_REGISTRY``
entries were removed and their sidebar entries repointed to the live URLs.
The 7 still-blocked LEAGUE/TEAM/PLAYERS placeholders (Playoffs, League
Finances, Team Finances, Trade, Trading Block, Prospects, Hall of Fame)
keep routing through ``coming_soon`` and now carry a ``blocker`` note
rendered on the explainer page. Registry total: 7 + 6 Help + 4 Tools = 17.

Tests hand-construct ``League`` rows — no simulation.
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
# Locked vocabulary (post-LG-01z).
# ---------------------------------------------------------------------------

# The sidebar_active enum is unchanged by LG-01z (the live screens still
# pass their key as sidebar_active to _build_league_sidebar_links).
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

# Still-blocked placeholder entries (LG-01z flipped most live; FIN-01 flipped
# the LEAGUE / TEAM Finances screens live, leaving no blocked LEAGUE- or
# TEAM-section placeholder keys).
LEAGUE_SCOPED_KEYS: dict[str, tuple[str, str, str]] = {}
TEAM_SCOPED_KEYS: dict[str, tuple[str, str, str]] = {}
PLAYERS_SCOPED_KEYS = {
    "players_trade": ("Trade", "players", "trade"),
    "players_trading_block": ("Trading Block", "players", "trading_block"),
    "players_prospects": ("Prospects", "players", "prospects"),
    "players_hall_of_fame": ("Hall of Fame", "players", "hall_of_fame"),
}
# All STATS placeholders went live in LG-01z.
STATS_SCOPED_KEYS: dict[str, tuple[str, str, str]] = {}
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

# The league-scoped placeholder URL names still routed through coming_soon
# (FIN-01 flipped Finances / Team Finances live, removing their coming_soon_*).
LEAGUE_SCOPED_URL_NAMES = {
    "coming_soon_trade": "players_trade",
    "coming_soon_trading_block": "players_trading_block",
    "coming_soon_prospects": "players_prospects",
    "coming_soon_hall_of_fame": "players_hall_of_fame",
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
    """200 on every still-placeholder URL name; 405 on POST; 404 on stale
    ``league_id``; 404 on unknown ``feature_key``; sidebar rendered in
    league branch; sidebar empty in Help / Tools branch; ``feature_label``
    in the ``<h1>``; ``"Coming soon"`` substring; locked context keys;
    ``app_mode`` matches the URL-prefix rule.
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
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
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
        url = reverse("coming_soon_trade", kwargs={"league_id": 999999})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    # -- 404 on unknown feature_key (direct invocation) ---------------------

    def test_unknown_feature_key_returns_404(self) -> None:
        from django.http import Http404
        from django.test import RequestFactory

        from matches.views import coming_soon

        request = RequestFactory().get("/help/overview/")
        with self.assertRaises(Http404):
            coming_soon(request, "nonexistent_feature_key")

    # -- Sidebar partial rendered in the league branch ----------------------

    def test_sidebar_partial_rendered_in_league_branch(self) -> None:
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
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
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        self.assertIn("Trade", response.content.decode())
        self.assertContains(response, 'id="coming-soon-header"')

    def test_feature_label_in_h1_for_help_overview(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertIn("Overview", response.content.decode())

    # -- "Coming soon" substring --------------------------------------------

    def test_coming_soon_message_substring_present_league(self) -> None:
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        self.assertContains(response, 'id="coming-soon-message"')
        self.assertIn("Coming soon", response.content.decode())

    def test_coming_soon_message_substring_present_help(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertContains(response, 'id="coming-soon-message"')
        self.assertIn("Coming soon", response.content.decode())

    # -- LG-01z: blocked placeholders render a blocker note -----------------

    def test_blocker_note_rendered_for_blocked_league_screen(self) -> None:
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        self.assertContains(response, 'id="coming-soon-blocker"')
        self.assertIn("Blocked:", response.content.decode())

    def test_no_blocker_note_for_help_screen(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        self.assertNotContains(response, 'id="coming-soon-blocker"')

    # -- Locked 7 context keys present (+ global app_mode) ------------------

    def test_locked_context_keys_present_league_scoped(self) -> None:
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
        response = self.client.get(url)
        ctx = response.context
        for key in (
            "league",
            "displayed_season",
            "feature_key",
            "feature_label",
            "feature_section",
            "blocker",
            "sidebar_links",
            "sidebar_active",
        ):
            self.assertIn(key, ctx, f"missing context key {key!r}")
        self.assertIn("app_mode", ctx)

    def test_locked_context_keys_present_help_branch(self) -> None:
        response = self.client.get(reverse("coming_soon_help_overview"))
        ctx = response.context
        for key in (
            "league",
            "displayed_season",
            "feature_key",
            "feature_label",
            "feature_section",
            "blocker",
            "sidebar_links",
            "sidebar_active",
        ):
            self.assertIn(key, ctx, f"missing context key {key!r}")
        self.assertIsNone(ctx["league"])
        self.assertEqual(ctx["sidebar_links"], [])

    # -- app_mode matches the URL-prefix rule -------------------------------

    def test_app_mode_is_league_on_league_scoped_route(self) -> None:
        url = reverse("coming_soon_trade", kwargs={"league_id": self.league.id})
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
    """Post-FIN-01 (Finances + Team Finances flipped live) the registry holds
    14 entries (4 still-blocked PLAYERS placeholders + 6 Help + 4 Tools). The
    4 blocked entries carry a ``blocker`` note; Help/Tools do not.
    """

    def setUp(self) -> None:
        from matches.views import _FEATURE_REGISTRY

        self.registry = _FEATURE_REGISTRY

    def test_registry_has_14_entries(self) -> None:
        self.assertEqual(len(self.registry), 14)

    def test_base_keys_present_in_every_value_dict(self) -> None:
        base = {"label", "section", "sidebar_active"}
        for key, value in self.registry.items():
            self.assertTrue(
                base.issubset(set(value.keys())),
                f"feature_key {key!r} missing base keys: {set(value.keys())}",
            )

    def test_blocked_placeholders_carry_a_blocker_note(self) -> None:
        blocked_keys = (
            set(LEAGUE_SCOPED_KEYS) | set(TEAM_SCOPED_KEYS) | set(PLAYERS_SCOPED_KEYS)
        )
        self.assertEqual(len(blocked_keys), 4)
        for key in blocked_keys:
            self.assertIn("blocker", self.registry[key], f"{key!r} missing blocker")
            self.assertIsInstance(self.registry[key]["blocker"], str)
            self.assertTrue(self.registry[key]["blocker"])

    def test_help_tools_have_no_blocker(self) -> None:
        for key in set(HELP_KEYS) | set(TOOLS_KEYS):
            self.assertNotIn("blocker", self.registry[key])

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

    def test_no_live_lg01z_keys_remain_in_registry(self) -> None:
        for gone in (
            "league_power_rankings",
            "team_roster",
            "team_history",
            "players_free_agents",
            "players_watch_list",
            "stats_game_log",
            "stats_league_leaders",
            "stats_player_ratings",
            "stats_player_stats",
            "stats_team_stats",
            "stats_statistical_feats",
        ):
            self.assertNotIn(
                gone, self.registry, f"{gone!r} should be live, not placeholder"
            )

    def test_placeholder_entries_have_non_none_sidebar_active(self) -> None:
        placeholder_keys = (
            set(LEAGUE_SCOPED_KEYS)
            | set(TEAM_SCOPED_KEYS)
            | set(PLAYERS_SCOPED_KEYS)
            | set(STATS_SCOPED_KEYS)
        )
        self.assertEqual(len(placeholder_keys), 4)
        for key in placeholder_keys:
            self.assertIsNotNone(
                self.registry[key]["sidebar_active"],
                f"feature_key {key!r} should have non-None sidebar_active",
            )

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
        help_tools_keys = set(HELP_KEYS) | set(TOOLS_KEYS)
        self.assertEqual(len(help_tools_keys), 10)
        for key in help_tools_keys:
            self.assertIsNone(self.registry[key]["sidebar_active"])

    def test_entry_counts_by_section(self) -> None:
        by_section: dict[str, int] = {}
        for value in self.registry.values():
            by_section[value["section"]] = by_section.get(value["section"], 0) + 1
        self.assertEqual(by_section.get("league", 0), 0)
        self.assertEqual(by_section.get("team", 0), 0)
        self.assertEqual(by_section.get("players", 0), 4)
        self.assertEqual(by_section.get("stats", 0), 0)
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
        url = reverse("coming_soon_trading_block", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_team_scoped_get_writes_last_league_id(self) -> None:
        league = _make_league("SessWriteT")
        url = reverse("coming_soon_prospects", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_players_scoped_get_writes_last_league_id(self) -> None:
        league = _make_league("SessWriteP")
        url = reverse("coming_soon_trade", kwargs={"league_id": league.id})
        self.client.get(url)
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_help_get_does_not_write_last_league_id(self) -> None:
        self.client.get(reverse("coming_soon_help_overview"))
        self.assertNotIn("last_league_id", self.client.session)

    def test_tools_get_does_not_write_last_league_id(self) -> None:
        self.client.get(reverse("coming_soon_tools_achievements"))
        self.assertNotIn("last_league_id", self.client.session)
