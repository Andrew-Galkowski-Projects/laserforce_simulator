"""LG-00c — Sortable Players tab tests.

Two classes:

- ``TestCoerceSortAndDir`` — pure-unit tests for the inline
  ``_coerce_sort`` / ``_coerce_dir`` helpers and their backing whitelists
  in ``teams.views``.
- ``TestPlayerListView`` — Django ``TestCase`` tests for the
  ``player_list`` view at ``/players/``: default sort, every sortable
  key, the ``preferred_roles`` Python-side branch, forgiving fallback,
  pagination, Free Agents inclusion, cell links, active-column arrow
  glyph, and the new nav link in ``base.html``.

Seam contract: ``.claude/worktrees/lg-00c-seam-contract.md`` §9.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from teams.models import Player, Team, get_free_agents_team
from teams.views import (
    _DEFAULT_PAGE_SIZE,
    _SORT_KEYS,
    _SORT_KEYS_DISPLAY,
    _VALID_DIRS,
    _VALID_PAGE_SIZES,
    _coerce_dir,
    _coerce_per_page,
    _coerce_sort,
)


def _make_player(team: Team, name: str, **stat_overrides) -> Player:
    """Create a Player on the given Team with all 19 stats defaulted to 50,
    overridable per-test via kwargs. Profile fields default to safe values.

    Use ``Offensive_synergy=<int>`` (capital-O) to override the synergy stat.
    """
    defaults = {
        "age": 25,
        "started_playing_age": 18,
        "total_games": 100,
        "home_site": "Test Arena",
        "height": "5'10\"",
        "preferred_roles": ["scout"],
        "player_awareness": 50,
        "game_awareness": 50,
        "resource_awareness": 50,
        "decision_making": 50,
        "positioning": 50,
        "stamina": 50,
        "speed": 50,
        "flexibility": 50,
        "adaptability": 50,
        "communication": 50,
        "teamwork": 50,
        "Offensive_synergy": 50,
        "defensive_synergy": 50,
        "midfield_synergy": 50,
        "resupply_synergy": 50,
        "resupply_efficiency": 50,
        "accuracy": 50,
        "survival": 50,
        "special_usage": 50,
    }
    defaults.update(stat_overrides)
    return Player.objects.create(team=team, name=name, **defaults)


# ---------------------------------------------------------------------------
# 9.1 — _coerce_sort / _coerce_dir pure-unit tests
# ---------------------------------------------------------------------------


class TestCoerceSortAndDir(TestCase):
    """Pure-unit tests for the inline ``_coerce_sort`` / ``_coerce_dir``
    helpers and their backing constants."""

    def test_coerce_sort_accepts_every_orm_key(self) -> None:
        """Every key in ``_SORT_KEYS`` is identity-returned by ``_coerce_sort``."""
        for key in _SORT_KEYS:
            self.assertEqual(_coerce_sort(key), key)

    def test_coerce_sort_accepts_preferred_roles_sentinel(self) -> None:
        """``"preferred_roles"`` is the 23rd accepted key (Python-side branch)."""
        self.assertEqual(_coerce_sort("preferred_roles"), "preferred_roles")

    def test_coerce_sort_falls_back_on_unknown_value(self) -> None:
        """Unknown strings fall back to the ``"team"`` default."""
        self.assertEqual(_coerce_sort("foo"), "team")

    def test_coerce_sort_falls_back_on_none(self) -> None:
        """``None`` falls back to the ``"team"`` default."""
        self.assertEqual(_coerce_sort(None), "team")

    def test_coerce_sort_falls_back_on_empty_string(self) -> None:
        """The empty string falls back to the ``"team"`` default."""
        self.assertEqual(_coerce_sort(""), "team")

    def test_coerce_dir_accepts_asc(self) -> None:
        """``"asc"`` is identity-returned."""
        self.assertEqual(_coerce_dir("asc"), "asc")

    def test_coerce_dir_accepts_desc(self) -> None:
        """``"desc"`` is identity-returned."""
        self.assertEqual(_coerce_dir("desc"), "desc")

    def test_coerce_dir_falls_back_on_unknown(self) -> None:
        """Unknown direction strings fall back to ``"asc"``."""
        self.assertEqual(_coerce_dir("sideways"), "asc")

    def test_coerce_dir_falls_back_on_none(self) -> None:
        """``None`` falls back to ``"asc"``."""
        self.assertEqual(_coerce_dir(None), "asc")

    def test_coerce_dir_falls_back_on_uppercase(self) -> None:
        """Case-sensitive: ``"ASC"`` is NOT accepted, falls back to default."""
        self.assertEqual(_coerce_dir("ASC"), "asc")

    def test_coerce_per_page_accepts_every_whitelisted_size(self) -> None:
        """Every value in ``_VALID_PAGE_SIZES`` round-trips through ``int()``."""
        for size in _VALID_PAGE_SIZES:
            self.assertEqual(_coerce_per_page(str(size)), size)

    def test_coerce_per_page_falls_back_on_none(self) -> None:
        """Missing ``?per_page=`` → default 10."""
        self.assertEqual(_coerce_per_page(None), _DEFAULT_PAGE_SIZE)

    def test_coerce_per_page_falls_back_on_empty_string(self) -> None:
        self.assertEqual(_coerce_per_page(""), _DEFAULT_PAGE_SIZE)

    def test_coerce_per_page_falls_back_on_non_int(self) -> None:
        self.assertEqual(_coerce_per_page("BOGUS"), _DEFAULT_PAGE_SIZE)

    def test_coerce_per_page_falls_back_on_out_of_whitelist(self) -> None:
        """Numeric but non-whitelisted values (0, negative, 37, 9999) → default."""
        for bogus in ("0", "-5", "37", "9999", "11", "49", "101"):
            self.assertEqual(
                _coerce_per_page(bogus), _DEFAULT_PAGE_SIZE, f"per_page={bogus!r}"
            )


# ---------------------------------------------------------------------------
# 9.2 — TestPlayerListView (Django TestCase)
# ---------------------------------------------------------------------------


class TestPlayerListView(TestCase):
    """View tests for ``GET /players/``."""

    def test_get_returns_200_with_default_sort(self) -> None:
        """Bare ``GET /players/`` → 200 with ``sort == "team"``, ``dir == "asc"``."""
        response = self.client.get(reverse("player_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "team")
        self.assertEqual(response.context["dir"], "asc")

    def test_default_sort_is_team_asc_with_name_secondary(self) -> None:
        """Default sort orders by ``team__name asc`` with ``name asc`` tiebreak.

        Two teams ("Alpha", "Bravo"), two players each ("Zed", "Aaron").
        Expected row order: Alpha/Aaron, Alpha/Zed, Bravo/Aaron, Bravo/Zed.
        """
        team_alpha = Team.objects.create(name="Alpha")
        team_bravo = Team.objects.create(name="Bravo")
        _make_player(team_alpha, "Zed")
        _make_player(team_alpha, "Aaron")
        _make_player(team_bravo, "Zed")
        _make_player(team_bravo, "Aaron")

        response = self.client.get(reverse("player_list"))
        self.assertEqual(response.status_code, 200)
        rows = list(response.context["page_obj"])
        self.assertEqual(
            [(p.team.name, p.name) for p in rows],
            [
                ("Alpha", "Aaron"),
                ("Alpha", "Zed"),
                ("Bravo", "Aaron"),
                ("Bravo", "Zed"),
            ],
        )

    def test_sort_by_name_asc(self) -> None:
        """``?sort=name&dir=asc`` puts the lex-lowest name first."""
        team = Team.objects.create(name="Alpha")
        _make_player(team, "Charlie")
        _make_player(team, "Alpha")
        _make_player(team, "Bravo")

        response = self.client.get(
            reverse("player_list"), {"sort": "name", "dir": "asc"}
        )
        self.assertEqual(response.status_code, 200)
        rows = list(response.context["page_obj"])
        self.assertEqual(rows[0].name, "Alpha")

    def test_sort_by_overall_rating_desc(self) -> None:
        """``?sort=overall_rating&dir=desc`` puts the highest-stat-sum player first."""
        team = Team.objects.create(name="Alpha")
        # All stats default to 50 → overall_rating == 50.0 baseline.
        _make_player(team, "MidRated")
        # Bump every stat to 80 for High; drop to 20 for Low.
        high_stats = {
            stat: 80
            for stat in (
                "player_awareness",
                "game_awareness",
                "resource_awareness",
                "decision_making",
                "positioning",
                "stamina",
                "speed",
                "flexibility",
                "adaptability",
                "communication",
                "teamwork",
                "Offensive_synergy",
                "defensive_synergy",
                "midfield_synergy",
                "resupply_synergy",
                "resupply_efficiency",
                "accuracy",
                "survival",
                "special_usage",
            )
        }
        low_stats = {key: 20 for key in high_stats}
        _make_player(team, "HighRated", **high_stats)
        _make_player(team, "LowRated", **low_stats)

        response = self.client.get(
            reverse("player_list"), {"sort": "overall_rating", "dir": "desc"}
        )
        self.assertEqual(response.status_code, 200)
        rows = list(response.context["page_obj"])
        self.assertEqual(rows[0].name, "HighRated")
        self.assertEqual(rows[-1].name, "LowRated")

    def test_sort_by_offensive_synergy_url_alias_maps_to_capital_O_field(self) -> None:
        """Lowercase URL key ``offensive_synergy`` maps to the capital-O ORM field.

        Pins the casing quirk: URL key is lowercase, ORM target is ``Offensive_synergy``.
        """
        team = Team.objects.create(name="Alpha")
        _make_player(team, "Low", Offensive_synergy=10)
        _make_player(team, "Mid", Offensive_synergy=50)
        _make_player(team, "High", Offensive_synergy=90)

        response = self.client.get(
            reverse("player_list"),
            {"sort": "offensive_synergy", "dir": "desc"},
        )
        self.assertEqual(response.status_code, 200)
        rows = list(response.context["page_obj"])
        self.assertEqual(rows[0].name, "High")
        self.assertEqual(rows[-1].name, "Low")

    def test_sort_by_preferred_roles_python_branch(self) -> None:
        """``?sort=preferred_roles&dir=asc`` runs the Python-side sort.

        Expected asc order by ``",".join(preferred_roles or [])``:
        empty list ("" sorts first) → "commander,heavy" → "scout".
        """
        team = Team.objects.create(name="Alpha")
        _make_player(team, "EmptyP", preferred_roles=[])
        _make_player(team, "ScoutP", preferred_roles=["scout"])
        _make_player(team, "CommanderHeavyP", preferred_roles=["commander", "heavy"])

        response = self.client.get(
            reverse("player_list"),
            {"sort": "preferred_roles", "dir": "asc"},
        )
        self.assertEqual(response.status_code, 200)
        rows = list(response.context["page_obj"])
        self.assertEqual(
            [p.name for p in rows],
            ["EmptyP", "CommanderHeavyP", "ScoutP"],
        )

    def test_sort_by_every_stat_key_returns_200(self) -> None:
        """Every ``(url_key, _)`` in ``_SORT_KEYS_DISPLAY`` returns 200 for both dirs."""
        team = Team.objects.create(name="Alpha")
        _make_player(team, "OnlyOne")

        for url_key, _label in _SORT_KEYS_DISPLAY:
            for direction in ("asc", "desc"):
                with self.subTest(sort=url_key, dir=direction):
                    response = self.client.get(
                        reverse("player_list"),
                        {"sort": url_key, "dir": direction},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertGreater(len(response.context["page_obj"]), 0)

    def test_invalid_sort_falls_back_to_team(self) -> None:
        """``?sort=bogus`` → context ``sort == "team"``; rows match team-asc default."""
        team_alpha = Team.objects.create(name="Alpha")
        team_bravo = Team.objects.create(name="Bravo")
        _make_player(team_alpha, "Aaron")
        _make_player(team_bravo, "Aaron")

        response = self.client.get(reverse("player_list"), {"sort": "bogus"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sort"], "team")
        rows = list(response.context["page_obj"])
        self.assertEqual(
            [(p.team.name, p.name) for p in rows],
            [("Alpha", "Aaron"), ("Bravo", "Aaron")],
        )

    def test_invalid_dir_falls_back_to_asc(self) -> None:
        """``?sort=name&dir=BOGUS`` → context ``dir == "asc"``; rows in name-asc order."""
        team = Team.objects.create(name="Alpha")
        _make_player(team, "Zed")
        _make_player(team, "Aaron")

        response = self.client.get(
            reverse("player_list"), {"sort": "name", "dir": "BOGUS"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["dir"], "asc")
        rows = list(response.context["page_obj"])
        self.assertEqual([p.name for p in rows], ["Aaron", "Zed"])

    def test_default_pagination_is_10_per_page(self) -> None:
        """11 players + default ``?per_page=`` → page 1 has 10 rows, page 2 has 1."""
        team = Team.objects.create(name="Alpha")
        for i in range(11):
            _make_player(team, f"Player{i:03d}")

        response_page_1 = self.client.get(reverse("player_list"), {"page": 1})
        self.assertEqual(response_page_1.status_code, 200)
        self.assertEqual(len(response_page_1.context["page_obj"]), 10)
        self.assertEqual(response_page_1.context["per_page"], _DEFAULT_PAGE_SIZE)
        self.assertEqual(_DEFAULT_PAGE_SIZE, 10)

        response_page_2 = self.client.get(reverse("player_list"), {"page": 2})
        self.assertEqual(response_page_2.status_code, 200)
        self.assertEqual(len(response_page_2.context["page_obj"]), 1)

    def test_per_page_25_50_100_each_render(self) -> None:
        """Every valid ``?per_page=`` value renders the expected row count."""
        team = Team.objects.create(name="Alpha")
        for i in range(120):
            _make_player(team, f"Player{i:03d}")

        for size in _VALID_PAGE_SIZES:
            response = self.client.get(reverse("player_list"), {"per_page": size})
            self.assertEqual(response.status_code, 200, f"per_page={size} failed")
            self.assertEqual(response.context["per_page"], size)
            self.assertEqual(len(response.context["page_obj"]), size)

    def test_invalid_per_page_falls_back_to_default(self) -> None:
        """Bogus / out-of-whitelist values silently coerce to the default."""
        team = Team.objects.create(name="Alpha")
        for i in range(20):
            _make_player(team, f"Player{i:03d}")

        for bogus in ("BOGUS", "0", "-5", "37", "9999", ""):
            response = self.client.get(reverse("player_list"), {"per_page": bogus})
            self.assertEqual(response.status_code, 200, f"per_page={bogus!r} failed")
            self.assertEqual(
                response.context["per_page"], _DEFAULT_PAGE_SIZE, f"per_page={bogus!r}"
            )

    def test_per_page_select_marks_active_option(self) -> None:
        """The dropdown marks exactly one ``<option>`` as selected — the active value."""
        _make_player(Team.objects.create(name="Alpha"), "Solo")

        response = self.client.get(reverse("player_list"), {"per_page": 25})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # The active option carries `selected`; every option label is present.
        self.assertIn('value="25" selected>25</option>', body)
        self.assertIn(">10</option>", body)
        self.assertIn(">50</option>", body)
        self.assertIn(">100</option>", body)
        # Exactly one `selected` in the per-page select.
        self.assertEqual(body.count('value="25" selected'), 1)
        for other in ("10", "50", "100"):
            self.assertNotIn(f'value="{other}" selected', body)
        # Locked DOM ids for the per-page surface.
        self.assertIn('id="player-list-per-page-form"', body)
        self.assertIn('id="player-list-per-page-select"', body)

    def test_pagination_carries_sort_and_dir_in_links(self) -> None:
        """The Next-page link preserves ``sort=`` + ``dir=`` + ``per_page=`` across pages."""
        team = Team.objects.create(name="Alpha")
        for i in range(51):
            _make_player(team, f"Player{i:03d}")

        response = self.client.get(
            reverse("player_list"),
            {"sort": "name", "dir": "desc", "per_page": 50, "page": 1},
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # The Next-page link's href must carry the active sort + dir + per_page.
        self.assertIn("sort=name", body)
        self.assertIn("dir=desc", body)
        self.assertIn("per_page=50", body)
        self.assertIn("page=2", body)

    def test_pagination_carries_per_page_in_links(self) -> None:
        """LG-00c+ — ``?per_page=`` survives across page navigation."""
        team = Team.objects.create(name="Alpha")
        for i in range(30):
            _make_player(team, f"Player{i:03d}")

        response = self.client.get(reverse("player_list"), {"per_page": 25, "page": 1})
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("per_page=25", body)
        # And page 1 carries 25 rows; page 2 carries the remaining 5.
        self.assertEqual(len(response.context["page_obj"]), 25)

    def test_pagination_links_drop_invalid_sort_and_dir(self) -> None:
        """LG00c-7 regression — invalid ``?sort=&dir=`` must not survive in page links.

        The view coerces invalid query params to defaults, but the pagination
        href must reflect the COERCED values, not the raw ``request.GET``, or the
        rubbish propagates across page navigation.
        """
        team = Team.objects.create(name="Alpha")
        for i in range(15):
            _make_player(team, f"Player{i:03d}")

        response = self.client.get(
            reverse("player_list"),
            {"sort": "BOGUS", "dir": "SIDEWAYS", "page": 1},
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # Pagination link must carry the coerced defaults, not the rubbish.
        self.assertNotIn("sort=BOGUS", body)
        self.assertNotIn("dir=SIDEWAYS", body)
        self.assertIn("sort=team", body)
        self.assertIn("dir=asc", body)

    def test_sort_change_resets_to_page_1(self) -> None:
        """Column-header hrefs drop ``page=`` so clicking a header resets to page 1.

        ``per_page=`` IS preserved in column-header links so the user's page-size
        choice survives a re-sort.
        """
        team = Team.objects.create(name="Alpha")
        for i in range(15):
            _make_player(team, f"Player{i:03d}")

        response = self.client.get(
            reverse("player_list"),
            {"sort": "name", "dir": "asc", "page": 2},
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        # Column-header querystring carries per_page (default 10) but NOT page.
        # Note: `per_page` ENDS with `page` so we check that `page=` does not
        # appear as a separate key — i.e. neither at the start nor after an `&`.
        qs_no_sort = response.context["querystring_without_sort_dir_page"]
        self.assertIn("per_page=10", qs_no_sort)
        self.assertFalse(
            qs_no_sort.startswith("page=") or "&page=" in qs_no_sort,
            f"Header qs unexpectedly contains a page= key: {qs_no_sort!r}",
        )

        # Belt-and-suspenders: parse each `<a` opened inside a `<th id="player-list-th-`
        # and assert none of those hrefs contain a `page=` KEY (per_page= is fine
        # — it ENDS with `page` but is a different param).
        import re

        th_anchor_re = re.compile(
            r'<th id="player-list-th-[^"]+">\s*<a\s+href="\?([^"]+)"',
            re.DOTALL,
        )
        for qs in th_anchor_re.findall(body):
            self.assertFalse(
                qs.startswith("page=") or "&page=" in qs,
                f"Header href ?{qs} contains a page= key",
            )

    def test_free_agents_players_appear_in_listing(self) -> None:
        """Players on the Free Agents Team are NOT special-cased out of the listing."""
        free_agents = get_free_agents_team()
        _make_player(free_agents, "ZorroFreeAgent")

        response = self.client.get(reverse("player_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("ZorroFreeAgent", body)

    def test_name_cell_links_to_career_stats(self) -> None:
        """The name cell wraps an anchor pointing at ``/players/<id>/stats/``."""
        team = Team.objects.create(name="Alpha")
        player = _make_player(team, "Linkable")

        response = self.client.get(reverse("player_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        expected_href = reverse("player_career_stats", args=[player.id])
        self.assertIn(f'href="{expected_href}"', body)

    def test_team_cell_links_to_team_detail(self) -> None:
        """The team cell wraps an anchor pointing at ``/teams/<id>/``."""
        team = Team.objects.create(name="LinkableTeam")
        _make_player(team, "AnyPlayer")

        response = self.client.get(reverse("player_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        expected_href = reverse("team_detail", args=[team.id])
        self.assertIn(f'href="{expected_href}"', body)

    def test_active_column_renders_arrow_glyph(self) -> None:
        """Active asc column appends ``↑`` (U+2191); active desc appends ``↓`` (U+2193)."""
        team = Team.objects.create(name="Alpha")
        _make_player(team, "Anyone")

        response_asc = self.client.get(
            reverse("player_list"), {"sort": "name", "dir": "asc"}
        )
        self.assertEqual(response_asc.status_code, 200)
        self.assertIn("Name ↑", response_asc.content.decode("utf-8"))

        response_desc = self.client.get(
            reverse("player_list"), {"sort": "name", "dir": "desc"}
        )
        self.assertEqual(response_desc.status_code, 200)
        self.assertIn("Name ↓", response_desc.content.decode("utf-8"))

    def test_nav_link_present_in_base_html(self) -> None:
        """GET ``team_list`` (a base.html-extending page) shows the new Players nav link.

        Asserts both the ``"Players"`` label and the locked
        ``id="player-list-nav-link"`` DOM id appear in the response body.
        """
        response = self.client.get(reverse("team_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Players", body)
        self.assertIn('id="player-list-nav-link"', body)
