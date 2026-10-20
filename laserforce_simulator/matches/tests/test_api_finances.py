"""ANG-01e T5 -- the finances API (``/api/leagues/<id>/teams/<team_id>/finances/``
and ``/api/leagues/<id>/finances/``).

Seam 1 of the ANG-01e spec (#180), driven through DRF ``APIClient``: status
codes, the envelope, the 409 / 400 error codes and the ``Team`` columns a
PATCH leaves behind. No simulator runs; Seasons are hand-built fixtures.
"""

from __future__ import annotations

from matches.finance import MAX_LEVEL
from matches.models import League, TeamSeasonFinance
from matches.tests.league_api_fixtures import LeagueApiTestCase, ranked_career_league

TEAM_FINANCE_KEYS = {
    "finance_enabled",
    "team",
    "editable",
    "budgets",
    "budget_costs",
    "ticket_price",
    "cash",
    "injury_policy",
    "live_payroll",
    "live_luxury_tax",
    "live_min_payroll_penalty",
    "record",
    "current_hype",
    "roster_salaries",
    "availability",
    "history",
}
BUDGET_KEYS = {"scouting", "coaching", "facilities", "health"}


def _team_url(league_id: int, team_id: int) -> str:
    return f"/api/leagues/{league_id}/teams/{team_id}/finances/"


def _finance_league(prefix: str, manager, *, enabled: bool = True):
    league, season, teams = ranked_career_league(prefix, 2, manager=manager)
    league.finance_enabled = enabled
    league.save(update_fields=["finance_enabled"])
    return league, season, teams


class TeamFinancesGetTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = _finance_league("TFGet", self.user_a)

    def test_get_current_team_returns_full_payload(self) -> None:
        current = self.teams[0]
        data = self.ok(self.api.get(_team_url(self.league.id, current.id)))
        self.assertEqual(set(data), TEAM_FINANCE_KEYS)
        self.assertTrue(data["finance_enabled"])
        self.assertTrue(data["editable"])
        self.assertEqual(data["team"], {"id": current.id, "name": current.name})
        self.assertEqual(set(data["budgets"]), BUDGET_KEYS)
        self.assertEqual(set(data["budget_costs"]), BUDGET_KEYS)
        self.assertEqual(data["budgets"]["scouting"], current.budget_scouting)
        self.assertEqual(set(data["record"]), {"wins", "losses", "games"})
        # teams[0] beat teams[1] in the hand-built ranked Season.
        self.assertEqual(data["record"], {"wins": 1, "losses": 0, "games": 1})
        self.assertEqual(len(data["roster_salaries"]), 6)
        self.assertEqual(
            set(data["roster_salaries"][0]["player"]), {"id", "name", "team"}
        )
        self.assertEqual(data["availability"], [])
        self.assertEqual(data["history"], [])
        self.assert_session_untouched()


class TeamFinancesPatchTests(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.league, self.season, self.teams = _finance_league("TFPatch", self.user_a)
        self.current = self.teams[0]
        self.url = _team_url(self.league.id, self.current.id)

    def test_patch_changes_only_sent_fields_and_returns_get_payload(self) -> None:
        before_coaching = self.current.budget_coaching
        data = self.ok(
            self.api.patch(
                self.url,
                {"budget_scouting": 80, "injury_policy": "play_hurt"},
                format="json",
            )
        )
        self.assertEqual(set(data), TEAM_FINANCE_KEYS)
        self.assertEqual(data["budgets"]["scouting"], 80)
        self.assertEqual(data["injury_policy"], "play_hurt")
        self.current.refresh_from_db()
        self.assertEqual(self.current.budget_scouting, 80)
        self.assertEqual(self.current.injury_policy, "play_hurt")
        self.assertEqual(self.current.budget_coaching, before_coaching)

    def test_patch_another_enrolled_team_is_409_and_changes_nothing(self) -> None:
        ai_team = self.teams[1]
        before = ai_team.budget_scouting
        self.error(
            self.api.patch(
                _team_url(self.league.id, ai_team.id),
                {"budget_scouting": 90},
                format="json",
            ),
            409,
            "team_not_editable",
        )
        ai_team.refresh_from_db()
        self.assertEqual(ai_team.budget_scouting, before)

    def test_patch_with_no_current_team_is_409(self) -> None:
        League.objects.filter(pk=self.league.pk).update(current_team=None)
        before = self.current.budget_scouting
        self.error(
            self.api.patch(self.url, {"budget_scouting": 90}, format="json"),
            409,
            "team_not_editable",
        )
        self.current.refresh_from_db()
        self.assertEqual(self.current.budget_scouting, before)

    def test_patch_with_finances_off_is_409(self) -> None:
        League.objects.filter(pk=self.league.pk).update(finance_enabled=False)
        before = self.current.budget_scouting
        self.error(
            self.api.patch(self.url, {"budget_scouting": 90}, format="json"),
            409,
            "finance_disabled",
        )
        self.current.refresh_from_db()
        self.assertEqual(self.current.budget_scouting, before)

    def test_invalid_values_are_400_with_field_errors_and_no_partial_save(
        self,
    ) -> None:
        before = (
            self.current.budget_scouting,
            self.current.ticket_price,
            self.current.injury_policy,
        )
        response = self.api.patch(
            self.url,
            {
                "budget_scouting": 70,
                "budget_coaching": MAX_LEVEL + 1,
                "budget_health": 0,
                "ticket_price": -1,
                "injury_policy": "nonsense",
            },
            format="json",
        )
        errors = self.errors(response, 400)
        fields = {e["field"] for e in errors}
        self.assertEqual(
            fields,
            {"budget_coaching", "budget_health", "ticket_price", "injury_policy"},
        )
        self.current.refresh_from_db()
        self.assertEqual(
            (
                self.current.budget_scouting,
                self.current.ticket_price,
                self.current.injury_policy,
            ),
            before,
        )

    def test_non_integer_budget_is_400(self) -> None:
        self.error(
            self.api.patch(self.url, {"budget_facilities": 2.5}, format="json"),
            400,
            "invalid",
            "budget_facilities",
        )

    def test_patch_on_another_accounts_league_is_404(self) -> None:
        foreign = self.foreign_league()
        self.errors(
            self.api.patch(
                _team_url(foreign.id, self.current.id),
                {"budget_scouting": 90},
                format="json",
            ),
            404,
        )

    def test_anonymous_patch_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        response = self.anon.patch(self.url, {"budget_scouting": 90}, format="json")
        self.assertEqual(response.status_code, 401)


class TeamFinancesGuardTests(LeagueApiTestCase):
    def test_finances_off_get_returns_header_only(self) -> None:
        league, _season, teams = _finance_league("TFOff", self.user_a, enabled=False)
        data = self.ok(self.api.get(_team_url(league.id, teams[0].id)))
        self.assertEqual(
            data,
            {
                "finance_enabled": False,
                "team": {"id": teams[0].id, "name": teams[0].name},
                "editable": False,
            },
        )

    def test_get_other_enrolled_team_is_not_editable(self) -> None:
        league, _season, teams = _finance_league("TFAi", self.user_a)
        data = self.ok(self.api.get(_team_url(league.id, teams[1].id)))
        self.assertFalse(data["editable"])
        self.assertEqual(set(data), TEAM_FINANCE_KEYS)

    def test_team_not_enrolled_is_404(self) -> None:
        league, _season, _teams = _finance_league("TFEnr", self.user_a)
        _other_league, _s, outsiders = _finance_league("TFOut", self.user_a)
        self.errors(self.api.get(_team_url(league.id, outsiders[0].id)), 404)

    def test_league_with_no_season_is_404(self) -> None:
        from matches.tests.league_api_fixtures import make_league, make_teams

        team = make_teams("TFNoS", 1)[0]
        league = make_league("TFNoS League", manager=self.user_a, current_team=team)
        self.errors(self.api.get(_team_url(league.id, team.id)), 404)

    def test_another_accounts_league_is_404(self) -> None:
        _league, _season, teams = _finance_league("TFFor", self.user_a)
        foreign = self.foreign_league()
        self.errors(self.api.get(_team_url(foreign.id, teams[0].id)), 404)

    def test_anonymous_get_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        league, _season, teams = _finance_league("TFAnon", self.user_a)
        response = self.anon.get(_team_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 401)


def _league_url(league_id: int) -> str:
    return f"/api/leagues/{league_id}/finances/"


class LeagueFinancesTests(LeagueApiTestCase):
    def test_rows_per_enrolled_team_by_name_with_snapshot_figures(self) -> None:
        league, season, teams = _finance_league("LFin", self.user_a)
        TeamSeasonFinance.objects.create(
            team=teams[1], season=season, revenue=50.0, profit=12.5, payroll=30.0
        )
        data = self.ok(self.api.get(_league_url(league.id)))
        self.assertEqual(set(data), {"finance_enabled", "rows"})
        self.assertTrue(data["finance_enabled"])
        by_name = sorted(teams, key=lambda t: t.name)
        self.assertEqual(
            [r["team"]["id"] for r in data["rows"]], [t.id for t in by_name]
        )
        for row in data["rows"]:
            self.assertEqual(set(row), {"team", "cash", "revenue", "profit", "payroll"})
        snap = next(r for r in data["rows"] if r["team"]["id"] == teams[1].id)
        self.assertEqual(
            (snap["revenue"], snap["profit"], snap["payroll"]), (50.0, 12.5, 30.0)
        )
        empty = next(r for r in data["rows"] if r["team"]["id"] == teams[0].id)
        self.assertEqual(
            (empty["revenue"], empty["profit"], empty["payroll"]), (None, None, None)
        )
        self.assertEqual(empty["cash"], teams[0].cash)
        self.assert_session_untouched()

    def test_finances_off_has_empty_rows(self) -> None:
        league, _season, _teams = _finance_league("LFOff", self.user_a, enabled=False)
        data = self.ok(self.api.get(_league_url(league.id)))
        self.assertEqual(data, {"finance_enabled": False, "rows": []})

    def test_league_with_no_season_has_empty_rows(self) -> None:
        from matches.tests.league_api_fixtures import make_league

        league = make_league("LFNoS League", manager=self.user_a)
        league.finance_enabled = True
        league.save(update_fields=["finance_enabled"])
        data = self.ok(self.api.get(_league_url(league.id)))
        self.assertEqual(data, {"finance_enabled": True, "rows": []})

    def test_another_accounts_league_is_404(self) -> None:
        self.errors(self.api.get(_league_url(self.foreign_league().id)), 404)

    def test_anonymous_is_401(self) -> None:
        # ADR-0041: an anonymous /api/ request is 401 (was 403).
        league, _season, _teams = _finance_league("LFAnon", self.user_a)
        self.assertEqual(self.anon.get(_league_url(league.id)).status_code, 401)
