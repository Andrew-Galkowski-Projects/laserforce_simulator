"""FIN-01 — Team Finances + League Finances screen tests (seam contract §7 / §8).

Two NEW league screens, flipping the LG-01h placeholders live:

* ``matches.league_screens.team_finances.team_finances(request, league_id)`` —
  GET + a budget-edit POST branch; keyed on ``current_team`` (the LG-01g
  resolver chain, optional ``?team_id=`` override). The budget-edit POST writes
  ``Team.budget_scouting/coaching/facilities`` + ``ticket_price`` then redirects
  to the bare URL.
* ``matches.league_screens.league_finances.league_finances(request, league_id)``
  — GET-only league-wide read-only table.

Both follow the shared LG-01z contract: 405 GET-guard, ``get_object_or_404``,
``last_league_id`` session write, ``displayed_season`` resolve, sidebar links,
empty-state notice when no Season, a disabled-notice when the League's finance
toggle is OFF, and the LOCKED DOM ids.

Plus the central wiring: the sidebar Finances entries repoint live (no longer
``coming_soon_*``), and ``_FEATURE_REGISTRY`` no longer contains
``"league_finances"`` / ``"team_finances"``.

Tests call the views via ``reverse()`` + the test client (the screens ARE
URL-wired this slice). NO simulator, NO simulated point totals.

These FAIL until the Code agent lands the two screen modules + the URL wiring +
the templates + the sidebar repoint + the registry removal.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.league_views import _build_league_sidebar_links
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "FinScreenL", *, finance_enabled: bool = True) -> League:
    league = League.objects.create(
        name=name, mode="league", state="active", finance_enabled=finance_enabled
    )
    pool = Team.objects.create(name=f"{name} Free Agents")
    league.free_agent_pool = pool
    league.save(update_fields=["free_agent_pool"])
    return league


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
    # Key the team-finances screen on the first enrolled team.
    league.current_team = teams[0]
    league.save(update_fields=["current_team"])
    return season, teams


# ===========================================================================
# §7 — TestTeamFinancesRouting
# ===========================================================================


class TestTeamFinancesRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)

    def test_bad_league_id_returns_404(self) -> None:
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": 999999})
        )
        self.assertEqual(response.status_code, 404)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        self.client.get(reverse("team_finances", kwargs={"league_id": league.id}))
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_uses_team_finances_template(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        self.assertTemplateUsed(response, "leagues/team_finances.html")


# ===========================================================================
# §7 — TestTeamFinancesDomIds
# ===========================================================================


class TestTeamFinancesDomIds(TestCase):
    def test_history_and_budget_form_dom_ids_present(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        for dom_id in (
            "team-finances-table",
            "team-finances-budget-form",
            "team-finances-budget-scouting",
            "team-finances-budget-coaching",
            "team-finances-budget-facilities",
            "team-finances-ticket-price",
            "team-finances-budget-save",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")

    def test_sidebar_rendered(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        self.assertIn("league-sidebar", response.content.decode())


# ===========================================================================
# §7 — TestTeamFinancesEmptyAndDisabled
# ===========================================================================


class TestTeamFinancesEmptyAndDisabled(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("team-finances-empty-notice", response.content.decode())

    def test_finance_disabled_renders_disabled_notice(self) -> None:
        league = _make_league("TfDisabledL", finance_enabled=False)
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("team-finances-disabled-notice", response.content.decode())


# ===========================================================================
# §7 — TestTeamFinancesBudgetEditPost
# ===========================================================================


class TestTeamFinancesBudgetEditPost(TestCase):
    """The budget-edit POST writes ``Team.budget_*`` + ``ticket_price`` (levels
    clamped ``[1, MAX_LEVEL]``) then redirects to the bare URL."""

    def test_post_writes_budgets_and_ticket_price(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        url = reverse("team_finances", kwargs={"league_id": league.id})
        response = self.client.post(
            url,
            data={
                "budget_scouting": "70",
                "budget_coaching": "55",
                "budget_facilities": "90",
                "ticket_price": "25.0",
            },
        )
        self.assertEqual(response.status_code, 302)
        team.refresh_from_db()
        self.assertEqual(team.budget_scouting, 70)
        self.assertEqual(team.budget_coaching, 55)
        self.assertEqual(team.budget_facilities, 90)
        self.assertAlmostEqual(team.ticket_price, 25.0, places=4)

    def test_post_redirects_to_bare_url(self) -> None:
        league = _make_league()
        _make_active_season(league)
        url = reverse("team_finances", kwargs={"league_id": league.id})
        response = self.client.post(
            url,
            data={
                "budget_scouting": "50",
                "budget_coaching": "50",
                "budget_facilities": "50",
                "ticket_price": "10.0",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], url)

    def test_post_clamps_level_below_one(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        url = reverse("team_finances", kwargs={"league_id": league.id})
        self.client.post(
            url,
            data={
                "budget_scouting": "0",
                "budget_coaching": "50",
                "budget_facilities": "50",
                "ticket_price": "10.0",
            },
        )
        team.refresh_from_db()
        # Clamped to the [1, MAX_LEVEL] floor.
        self.assertGreaterEqual(team.budget_scouting, 1)

    def test_post_clamps_level_above_max(self) -> None:
        from matches.finance import MAX_LEVEL

        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        url = reverse("team_finances", kwargs={"league_id": league.id})
        self.client.post(
            url,
            data={
                "budget_scouting": str(MAX_LEVEL + 50),
                "budget_coaching": "50",
                "budget_facilities": "50",
                "ticket_price": "10.0",
            },
        )
        team.refresh_from_db()
        self.assertLessEqual(team.budget_scouting, MAX_LEVEL)


# ===========================================================================
# §7 — TestLeagueFinancesRouting
# ===========================================================================


class TestLeagueFinancesRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.post(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": 999999})
        )
        self.assertEqual(response.status_code, 404)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        self.client.get(reverse("league_finances", kwargs={"league_id": league.id}))
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_uses_league_finances_template(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertTemplateUsed(response, "leagues/league_finances.html")


# ===========================================================================
# §7 — TestLeagueFinancesDomIdsEmptyDisabled
# ===========================================================================


class TestLeagueFinancesDomIdsEmptyDisabled(TestCase):
    def test_table_dom_id_present(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertIn("league-finances-table", response.content.decode())

    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("league-finances-empty-notice", response.content.decode())

    def test_finance_disabled_renders_disabled_notice(self) -> None:
        league = _make_league("LfDisabledL", finance_enabled=False)
        _make_active_season(league)
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("league-finances-disabled-notice", response.content.decode())

    def test_sidebar_rendered(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("league_finances", kwargs={"league_id": league.id})
        )
        self.assertIn("league-sidebar", response.content.decode())


# ===========================================================================
# §7 — TestSidebarFinancesRepoint
# ===========================================================================


class TestSidebarFinancesRepoint(TestCase):
    """The LEAGUE > Finances and TEAM > Finances sidebar entries repoint from
    the ``coming_soon_*`` placeholders to the live ``league_finances`` /
    ``team_finances`` routes."""

    def _entry(self, links, key):
        for entry in links:
            if entry["key"] == key:
                return entry
        raise AssertionError(f"no sidebar entry with key {key!r}")

    def test_league_finances_entry_points_at_live_route(self) -> None:
        league = _make_league("SbLfL")
        links = _build_league_sidebar_links(league, None, None)
        entry = self._entry(links, "finances")
        self.assertEqual(
            entry["url"],
            reverse("league_finances", kwargs={"league_id": league.id}),
        )
        self.assertFalse(entry["disabled"])

    def test_team_finances_entry_points_at_live_route(self) -> None:
        league = _make_league("SbTfL")
        links = _build_league_sidebar_links(league, None, None)
        entry = self._entry(links, "finances_team")
        self.assertEqual(
            entry["url"],
            reverse("team_finances", kwargs={"league_id": league.id}),
        )
        self.assertFalse(entry["disabled"])


# ===========================================================================
# §7 — TestFeatureRegistryDropsFinances
# ===========================================================================


class TestFeatureRegistryDropsFinances(TestCase):
    """``_FEATURE_REGISTRY`` no longer carries ``"league_finances"`` /
    ``"team_finances"`` — both flip live, so their blocker entries are deleted."""

    def test_registry_no_longer_contains_finance_keys(self) -> None:
        from matches.views import _FEATURE_REGISTRY

        self.assertNotIn("league_finances", _FEATURE_REGISTRY)
        self.assertNotIn("team_finances", _FEATURE_REGISTRY)


# ===========================================================================
# FIN-04 — TestTeamFinancesHealthDomIds
# ===========================================================================


class TestTeamFinancesHealthDomIds(TestCase):
    """The Team Finances screen renders the three FIN-04 DOM ids:
    ``team-finances-budget-health`` (the health budget ``<input>``),
    ``team-finances-injury-policy`` (the ``<select name="injury_policy">``), and
    ``team-finances-availability`` (the availability-display container).

    Written test-first against the FIN-04 seam contract; these FAIL until the
    Code agent lands the three additions to ``team_finances`` + its template —
    the expected TDD red state.
    """

    def test_budget_health_and_injury_policy_and_availability_ids_present(
        self,
    ) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        for dom_id in (
            "team-finances-budget-health",
            "team-finances-injury-policy",
            "team-finances-availability",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")

    def test_existing_budget_form_dom_ids_preserved(self) -> None:
        # The FIN-01 budget-form ids stay (additive, not a rewrite).
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        for dom_id in (
            "team-finances-budget-form",
            "team-finances-budget-scouting",
            "team-finances-budget-coaching",
            "team-finances-budget-facilities",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")


# ===========================================================================
# FIN-04 — TestTeamFinancesHealthPost (budget_health + injury_policy writes)
# ===========================================================================


class TestTeamFinancesHealthPost(TestCase):
    """The budget-edit POST writes ``Team.budget_health`` (clamped
    ``[1, MAX_LEVEL]``) and ``Team.injury_policy`` (only ``"auto_sub"`` /
    ``"play_hurt"`` accepted, else current kept), alongside the FIN-01 budgets."""

    def _post_data(self, **overrides):
        data = {
            "budget_scouting": "50",
            "budget_coaching": "50",
            "budget_facilities": "50",
            "ticket_price": "10.0",
            "budget_health": "65",
            "injury_policy": "play_hurt",
        }
        data.update(overrides)
        return data

    def test_post_writes_budget_health_and_injury_policy(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        url = reverse("team_finances", kwargs={"league_id": league.id})
        response = self.client.post(url, data=self._post_data())
        self.assertEqual(response.status_code, 302)
        team.refresh_from_db()
        self.assertEqual(team.budget_health, 65)
        self.assertEqual(team.injury_policy, "play_hurt")

    def test_post_clamps_budget_health_below_one(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        url = reverse("team_finances", kwargs={"league_id": league.id})
        self.client.post(url, data=self._post_data(budget_health="0"))
        team.refresh_from_db()
        self.assertGreaterEqual(team.budget_health, 1)

    def test_post_clamps_budget_health_above_max(self) -> None:
        from matches.finance import MAX_LEVEL

        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        url = reverse("team_finances", kwargs={"league_id": league.id})
        self.client.post(url, data=self._post_data(budget_health=str(MAX_LEVEL + 50)))
        team.refresh_from_db()
        self.assertLessEqual(team.budget_health, MAX_LEVEL)

    def test_post_rejects_unknown_injury_policy_keeps_current(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        # Start from a known policy, then POST a bogus value.
        team.injury_policy = "auto_sub"
        team.save(update_fields=["injury_policy"])
        url = reverse("team_finances", kwargs={"league_id": league.id})
        self.client.post(url, data=self._post_data(injury_policy="nonsense"))
        team.refresh_from_db()
        # Unknown value ⇒ current policy preserved (not corrupted).
        self.assertEqual(team.injury_policy, "auto_sub")

    def test_post_accepts_auto_sub_policy(self) -> None:
        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        team.injury_policy = "play_hurt"
        team.save(update_fields=["injury_policy"])
        url = reverse("team_finances", kwargs={"league_id": league.id})
        self.client.post(url, data=self._post_data(injury_policy="auto_sub"))
        team.refresh_from_db()
        self.assertEqual(team.injury_policy, "auto_sub")


# ===========================================================================
# FIN-04 — TestTeamFinancesAvailabilityDisplay (lists out players)
# ===========================================================================


class TestTeamFinancesAvailabilityDisplay(TestCase):
    """The availability display (``team-finances-availability``) lists the
    Team's players with ``games_unavailable > 0`` (and renders an empty-notice
    DOM id when none are out)."""

    def test_unavailable_player_listed(self) -> None:
        from teams.models import Player

        league = _make_league()
        _season, teams = _make_active_season(league)
        team = teams[0]
        # Mark one starter unavailable with a distinctive name.
        starter = team.active_players[0]
        starter.name = "OutForThree"
        starter.games_unavailable = 3
        starter.save(update_fields=["name", "games_unavailable"])
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        self.assertIn("team-finances-availability", body)
        self.assertIn("OutForThree", body)

    def test_available_player_not_listed_when_none_out(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = self.client.get(
            reverse("team_finances", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        # The availability container still renders; an empty-state notice marks
        # that nobody is out.
        self.assertIn("team-finances-availability", body)
        self.assertIn("team-finances-availability-empty", body)
