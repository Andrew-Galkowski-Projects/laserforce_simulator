"""LG-01z-n — Django ``TestCase`` tests for the Player Ratings league screen.

The view ``matches.league_screens.player_ratings.player_ratings(request,
league_id)`` is read-only / GET-only. It is NOT yet URL-wired (the
orchestrator wires the ``stats_player_ratings`` route centrally), so these
tests call the view directly via ``RequestFactory`` with a real session
attached.

Fixtures are hand-constructed League / Season / Team / Player rows —
LG-01z runs NO simulation, so the simulator is never entered.

Scope under test: players whose Team is enrolled in the displayed Season
appear; players on unenrolled teams (and the Free Agents pool) do NOT.
Columns are the 19 rating attributes + ``overall_rating`` (NOT performance
stats).
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.player_ratings import player_ratings
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team, get_free_agents_team


def _attach_session(request):
    """Run SessionMiddleware so the view's session write succeeds."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/stats/player-ratings/"
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
    return _attach_session(request)


def _make_league(name: str = "PRLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


# ---------------------------------------------------------------------------
# Routing / method / 404 / session
# ---------------------------------------------------------------------------


class TestPlayerRatingsRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = player_ratings(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/player-ratings/")
        )
        response = player_ratings(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            player_ratings(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        player_ratings(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ---------------------------------------------------------------------------
# Empty state — no Season
# ---------------------------------------------------------------------------


class TestPlayerRatingsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = player_ratings(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("player-ratings-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = player_ratings(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ---------------------------------------------------------------------------
# Scope — enrolled-team players appear, others do not
# ---------------------------------------------------------------------------


class TestPlayerRatingsScope(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.enrolled_a, self.enrolled_b = self.teams

    def test_enrolled_team_player_appears(self) -> None:
        enrolled_player = self.enrolled_a.active_players[0]
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn(enrolled_player.name, content)
        # LG-06h: player-name link repointed to league_player_detail.
        self.assertIn(
            f"/leagues/{self.league.id}/players/{enrolled_player.id}/", content
        )

    def test_unenrolled_team_player_excluded(self) -> None:
        outsider = Team.objects.create(name="Outsiders")
        op = Player.objects.create(team=outsider, name="Outsider Ace")
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertNotIn("Outsider Ace", content)
        # LG-06h: the excluded player has no in-League player-page link.
        self.assertNotIn(f"/leagues/{self.league.id}/players/{op.id}/", content)

    def test_free_agent_pool_player_excluded(self) -> None:
        pool = get_free_agents_team()
        fa = Player.objects.create(team=pool, name="Pool Drifter")
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertNotIn("Pool Drifter", content)
        # LG-06h: the excluded player has no in-League player-page link.
        self.assertNotIn(f"/leagues/{self.league.id}/players/{fa.id}/", content)


# ---------------------------------------------------------------------------
# Body — DOM ids, sidebar_active, rating columns, career links
# ---------------------------------------------------------------------------


class TestPlayerRatingsBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.player = self.teams[0].active_players[0]

    def test_table_dom_id_present(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn("player-ratings-table", response.content.decode())

    def test_sortable_header_dom_ids_present(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("player-ratings-th-name", content)
        self.assertIn("player-ratings-th-team", content)
        self.assertIn("player-ratings-th-overall_rating", content)
        # A representative rating attribute column is present.
        self.assertIn("player-ratings-th-accuracy", content)

    def test_sidebar_active_is_player_ratings(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-stats-player_ratings", response.content.decode())

    def test_player_links_to_in_league_player_page(self) -> None:
        # LG-06h: player-name link repointed to league_player_detail.
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn(
            f"/leagues/{self.league.id}/players/{self.player.id}/",
            response.content.decode(),
        )

    def test_bio_and_proxy_columns_present(self) -> None:
        # Fixed bio columns + the deferred MMR / Rank / Potential proxies.
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for col in (
            "age",
            "home_site",
            "height",
            "games",
            "started",
            "mmr",
            "rank",
            "potential",
        ):
            self.assertIn(f"player-ratings-th-{col}", content)
        self.assertIn("Potential", content)

    def test_team_cell_links_to_roster(self) -> None:
        # The Team value links to the league Team Roster page for that team.
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        team = self.player.team
        self.assertIn(
            f"/leagues/{self.league.id}/team/roster/?team_id={team.id}", content
        )


# ---------------------------------------------------------------------------
# Sorting — LG-00c forgiving fallback + ORM / Python-sentinel branches
# ---------------------------------------------------------------------------


class TestPlayerRatingsSorting(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        # Three players on an enrolled team with distinct names + accuracy.
        team = self.teams[0]
        self.zed = Player.objects.create(team=team, name="Zed", accuracy=10)
        self.amy = Player.objects.create(team=team, name="Amy", accuracy=90)
        self.bob = Player.objects.create(team=team, name="Bob", accuracy=50)

    def test_default_sort_returns_200(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertEqual(response.status_code, 200)

    def test_sort_by_name_asc(self) -> None:
        # per_page=100 keeps every row on one page so all three named
        # players render regardless of how many auto-slot players exist.
        response = player_ratings(
            _get(self.league.id, query="sort=name&dir=asc&per_page=100"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertLess(content.index("Amy"), content.index("Bob"))
        self.assertLess(content.index("Bob"), content.index("Zed"))

    def test_sort_by_accuracy_desc(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=accuracy&dir=desc&per_page=100"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertLess(content.index("Amy"), content.index("Bob"))
        self.assertLess(content.index("Bob"), content.index("Zed"))

    def test_sort_by_overall_rating_desc_uses_annotation(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=overall_rating&dir=desc"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)

    def test_sort_by_preferred_roles_python_branch(self) -> None:
        self.amy.preferred_roles = ["scout"]
        self.amy.save(update_fields=["preferred_roles"])
        response = player_ratings(
            _get(self.league.id, query="sort=preferred_roles&dir=asc"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_sort_falls_back_to_default(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=BOGUS&dir=SIDEWAYS"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPlayerRatingsPagination(TestCase):
    def test_per_page_paginates_and_carries_querystring(self) -> None:
        league = _make_league()
        season, teams = _make_active_season(league, n_teams=2)
        team = teams[0]
        for i in range(15):
            Player.objects.create(team=team, name=f"Extra{i:02d}")
        response = player_ratings(
            _get(league.id, query="per_page=10&page=2"), league.id
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("player-ratings-pagination", content)
        # Page-2 navigation links must carry the coerced per_page value.
        self.assertIn("per_page=10", content)


# ---------------------------------------------------------------------------
# LG-06a — page-size <select> selector
# ---------------------------------------------------------------------------


class TestPlayerRatingsPerPageSelector(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        # An enrolled-team player so the body (+ per-page form) renders.
        Player.objects.create(team=self.teams[0], name="Rate Selector")

    def test_per_page_select_dom_id_present(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn("player-ratings-per-page-select", response.content.decode())

    def test_selected_option_reflects_requested_per_page(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="per_page=25"), self.league.id
        )
        self.assertIn('value="25" selected', response.content.decode())

    def test_per_page_form_carries_hidden_sort_and_dir(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=name&dir=asc&per_page=25"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertIn('name="sort"', content)
        self.assertIn('name="dir"', content)


# ---------------------------------------------------------------------------
# LG-06b — `_coerce_team_id` pure unit
# ---------------------------------------------------------------------------


class TestCoerceTeamId(TestCase):
    """Pure-unit tests for ``matches.league_views._coerce_team_id``."""

    def setUp(self) -> None:
        from matches.league_views import _coerce_team_id

        self._coerce = _coerce_team_id
        self.enrolled = {10, 20, 30}

    def test_parses_and_enrolled_returns_int(self) -> None:
        self.assertEqual(self._coerce("20", self.enrolled), 20)

    def test_none_returns_none(self) -> None:
        self.assertIsNone(self._coerce(None, self.enrolled))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(self._coerce("", self.enrolled))

    def test_malformed_returns_none(self) -> None:
        self.assertIsNone(self._coerce("abc", self.enrolled))

    def test_negative_non_enrolled_returns_none(self) -> None:
        self.assertIsNone(self._coerce("-1", self.enrolled))

    def test_parseable_but_not_enrolled_returns_none(self) -> None:
        self.assertIsNone(self._coerce("999", self.enrolled))

    def test_empty_enrolled_set_returns_none(self) -> None:
        self.assertIsNone(self._coerce("20", set()))


# ---------------------------------------------------------------------------
# LG-06b — team filter
# ---------------------------------------------------------------------------


class TestPlayerRatingsTeamFilter(TestCase):
    """LG-06b team filter. Uses the Django test ``Client`` against the
    wired ``stats_player_ratings`` URL so ``response.context`` is
    populated for the context-key assertions."""

    URL_NAME = "stats_player_ratings"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        # Sort teams by name so we know which is "first" deterministically.
        self.teams.sort(key=lambda t: t.name)
        self.team_a, self.team_b = self.teams
        # One distinctly-named player on each enrolled team.
        self.player_a = Player.objects.create(team=self.team_a, name="Alpha Filtered")
        self.player_b = Player.objects.create(team=self.team_b, name="Bravo Filtered")

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_enrolled_teams_context_ordered_by_name(self) -> None:
        response = self._get()
        enrolled = response.context["enrolled_teams"]
        names = [t.name for t in enrolled]
        self.assertEqual(names, sorted(names))
        self.assertEqual(
            {t.id for t in enrolled},
            {self.team_a.id, self.team_b.id},
        )

    def test_selected_team_id_none_when_no_param(self) -> None:
        response = self._get()
        self.assertIsNone(response.context["selected_team_id"])

    def test_selected_team_id_set_for_enrolled(self) -> None:
        response = self._get(query=f"team_id={self.team_a.id}")
        self.assertEqual(response.context["selected_team_id"], self.team_a.id)

    def test_selected_team_id_none_for_malformed(self) -> None:
        response = self._get(query="team_id=abc")
        self.assertIsNone(response.context["selected_team_id"])

    def test_selected_team_id_none_for_non_enrolled(self) -> None:
        outsider = Team.objects.create(name="Filter Outsiders")
        response = self._get(query=f"team_id={outsider.id}")
        self.assertIsNone(response.context["selected_team_id"])

    def test_filter_restricts_to_team_and_excludes_other(self) -> None:
        response = self._get(query=f"team_id={self.team_a.id}&per_page=100")
        content = response.content.decode()
        self.assertIn("Alpha Filtered", content)
        # The other team's player must NOT appear.
        self.assertNotIn("Bravo Filtered", content)

    def test_absent_param_shows_all_teams(self) -> None:
        response = self._get(query="per_page=100")
        content = response.content.decode()
        self.assertIn("Alpha Filtered", content)
        self.assertIn("Bravo Filtered", content)

    def test_malformed_param_falls_back_to_all(self) -> None:
        response = self._get(query="team_id=abc&per_page=100")
        content = response.content.decode()
        self.assertIn("Alpha Filtered", content)
        self.assertIn("Bravo Filtered", content)

    def test_non_enrolled_param_falls_back_to_all(self) -> None:
        outsider = Team.objects.create(name="Filter Outsiders")
        response = self._get(query=f"team_id={outsider.id}&per_page=100")
        content = response.content.decode()
        self.assertIn("Alpha Filtered", content)
        self.assertIn("Bravo Filtered", content)

    def test_filter_form_and_select_dom_ids_present(self) -> None:
        content = self._get().content.decode()
        self.assertIn("player-ratings-team-filter-form", content)
        self.assertIn("player-ratings-team-filter-select", content)

    def test_default_all_teams_option_present(self) -> None:
        content = self._get().content.decode()
        # The default option carries value="" and the "All Teams" label.
        # It may additionally carry ``selected`` when no team is picked,
        # so match the value attribute and label independently rather than
        # pinning the exact tag.
        self.assertIn('value=""', content)
        self.assertIn("All Teams", content)

    def test_selected_option_matches_selected_team_id(self) -> None:
        content = self._get(query=f"team_id={self.team_a.id}").content.decode()
        # The enrolled team's option is marked selected.
        self.assertIn(f'value="{self.team_a.id}" selected', content)

    def test_team_id_in_querystring_without_page(self) -> None:
        response = self._get(query=f"team_id={self.team_a.id}&per_page=25")
        qs = response.context["querystring_without_page"]
        self.assertIn(f"team_id={self.team_a.id}", qs)

    def test_team_id_in_querystring_without_sort_dir_page(self) -> None:
        response = self._get(query=f"team_id={self.team_a.id}&per_page=25")
        qs = response.context["querystring_without_sort_dir_page"]
        self.assertIn(f"team_id={self.team_a.id}", qs)

    def test_hidden_team_id_input_in_per_page_form(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&per_page=25"
        ).content.decode()
        self.assertIn('name="team_id"', content)

    def test_picker_form_omits_page(self) -> None:
        # The team-picker <form> must NOT carry a hidden ``page`` input
        # (selecting a team resets to page 1). We assert the picker form
        # block does not contain a ``name="page"`` input.
        content = self._get(
            query=f"team_id={self.team_a.id}&page=3&per_page=10"
        ).content.decode()
        form_start = content.index("player-ratings-team-filter-form")
        # Find the closing </form> after the picker form opens.
        form_end = content.index("</form>", form_start)
        picker_block = content[form_start:form_end]
        self.assertNotIn('name="page"', picker_block)


# ---------------------------------------------------------------------------
# LG-05 — Potential is SORTABLE on Player Ratings (nulls-last both directions)
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-05-player-potential-seam-contract.md``
# §5 / §6: ``?sort=potential&dir=desc|asc`` orders rows by ``Player.potential``
# with NULLS LAST in BOTH directions; the ``player-ratings-th-potential`` header
# renders as a sortable ``<a ... sort=potential ...>`` link (NOT the fixed
# ``<td>-</td>`` placeholder it was before LG-05); a player's potential value
# renders in its row.
#
# Uses the Django test ``Client`` against the wired ``stats_player_ratings`` URL
# (like ``TestPlayerRatingsTeamFilter``). Players' ``Player.potential`` is set
# EXPLICITLY in the fixture (the screen fixtures bypass ``league_create``, so
# the field defaults to ``None`` unless set). Appended as NEW classes; no
# existing class is modified. These WILL fail until the Code agent lands
# ``Player.potential`` + the ``"potential"`` sort key + the sortable header — the
# TDD red state.


class TestPlayerRatingsPotentialSortable(TestCase):
    """LG-05 — Potential is a sortable column (nulls-last both directions)."""

    URL_NAME = "stats_player_ratings"

    def setUp(self) -> None:
        self.league = _make_league("PotSortL")
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        team = self.teams[0]
        # Three enrolled-team players with distinct potentials + one with None.
        self.high = Player.objects.create(team=team, name="PotHigh", potential=90.0)
        self.mid = Player.objects.create(team=team, name="PotMid", potential=50.0)
        self.low = Player.objects.create(team=team, name="PotLow", potential=10.0)
        self.nul = Player.objects.create(team=team, name="PotNull", potential=None)

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_sortable_header_renders_as_link(self) -> None:
        content = self._get().content.decode()
        # The header is a sortable <a> carrying sort=potential — NOT a fixed
        # placeholder <th id="player-ratings-th-potential">-</th>.
        self.assertIn("player-ratings-th-potential", content)
        idx = content.index('id="player-ratings-th-potential"')
        # Scope to the header element and assert it wraps an <a sort=potential>.
        window = content[idx : idx + 400]
        self.assertIn("<a", window)
        self.assertIn("sort=potential", window)

    def test_potential_value_renders_in_row(self) -> None:
        content = self._get(query="per_page=100").content.decode()
        # floatformat:1 renders 90.0 → "90.0".
        self.assertIn("90.0", content)

    def test_sort_desc_orders_high_to_low_nulls_last(self) -> None:
        content = self._get(
            query="sort=potential&dir=desc&per_page=100"
        ).content.decode()
        # High > Mid > Low, and the None-potential player sorts LAST.
        self.assertLess(content.index("PotHigh"), content.index("PotMid"))
        self.assertLess(content.index("PotMid"), content.index("PotLow"))
        self.assertLess(content.index("PotLow"), content.index("PotNull"))

    def test_sort_asc_orders_low_to_high_nulls_still_last(self) -> None:
        content = self._get(
            query="sort=potential&dir=asc&per_page=100"
        ).content.decode()
        # Low < Mid < High ascending; the None-potential player STILL sorts last.
        self.assertLess(content.index("PotLow"), content.index("PotMid"))
        self.assertLess(content.index("PotMid"), content.index("PotHigh"))
        self.assertLess(content.index("PotHigh"), content.index("PotNull"))

    def test_sort_potential_returns_200(self) -> None:
        self.assertEqual(self._get(query="sort=potential&dir=desc").status_code, 200)
