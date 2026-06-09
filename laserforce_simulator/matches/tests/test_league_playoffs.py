"""LG-02 — Django ``TestCase`` tests for the League Playoffs screen.

``GET /leagues/<int:league_id>/playoffs/`` (URL name ``league_playoffs``)
renders the viewed Season's ``tournament`` SeasonPhase bracket(s) inside the
league shell, replacing the LG-01h ``coming_soon`` placeholder. Read-only,
GET-only; follows the LG-01z shared-view contract.

The fixtures mirror the LG-02-Part2c-1 dashboard-test pattern: compose an
active Season with an ordinal-1 round_robin + ordinal-2 tournament phase, play
the RR to trigger the auto-build, then optionally drain the bracket. Round
ticks are patched small for speed; assertions are schema-level (DOM ids,
context keys) — never raw simulated point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 30


def _rr_tournament_season(name: str = "Pl"):
    """An active Season: ordinal-1 round_robin + ordinal-2 tournament, 4 teams."""
    league = League.objects.create(name=name)
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(4):
        t, _ = make_team_with_slots(f"{name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="tournament")
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _play_rr(season, teams):
    by_id = {t.id: t for t in teams}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


def _drain_tournament(tournament):
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


class TestLeaguePlayoffsRouting(TestCase):
    def test_get_returns_200_and_uses_template(self) -> None:
        league, _season, _teams = _rr_tournament_season("Route")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "leagues/playoffs.html")

    def test_post_returns_405(self) -> None:
        league, _season, _teams = _rr_tournament_season("Post")
        response = self.client.post(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_stale_league_id_returns_404(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": 999999})
        )
        self.assertEqual(response.status_code, 404)

    def test_get_writes_last_league_id(self) -> None:
        league, _season, _teams = _rr_tournament_season("Sess")
        self.client.get(reverse("league_playoffs", kwargs={"league_id": league.id}))
        self.assertEqual(self.client.session.get("last_league_id"), league.id)

    def test_sidebar_rendered_with_playoffs_active(self) -> None:
        league, _season, _teams = _rr_tournament_season("Side")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-sidebar"')
        self.assertEqual(response.context["sidebar_active"], "playoffs")


class TestLeaguePlayoffsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = League.objects.create(name="NoSeason")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-empty-notice"')

    def test_single_rr_season_renders_empty_notice(self) -> None:
        league = League.objects.create(name="SingleRR")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 6, 1)
        )
        for i in range(4):
            t, _ = make_team_with_slots(f"SRT{i}")
            season.teams.add(t)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.start_season()
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-empty-notice"')
        self.assertEqual(response.context["brackets"], [])


class TestLeaguePlayoffsBracket(TestCase):
    def test_pending_phase_renders_section_without_grid(self) -> None:
        # Tournament phase exists but RR not yet played -> tournament not built.
        league, _season, _teams = _rr_tournament_season("Pend")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-phase-2"')
        self.assertNotContains(response, 'id="league-playoffs-bracket-2"')
        self.assertTrue(response.context["brackets"][0]["pending"])

    def test_built_bracket_renders_nodes(self) -> None:
        league, season, teams = _rr_tournament_season("Built")
        _play_rr(season, teams)
        season.refresh_from_db()
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-phase-2"')
        self.assertContains(response, 'id="league-playoffs-bracket-2"')
        self.assertFalse(response.context["brackets"][0]["pending"])

    def test_champion_banner_after_drain(self) -> None:
        league, season, teams = _rr_tournament_season("Champ")
        _play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        _drain_tournament(tournament_phase.tournament)
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-playoffs-champion-2"')
        self.assertIsNotNone(response.context["brackets"][0]["champion"])


class TestLeaguePlayoffsSeasonSelector(TestCase):
    def test_explicit_season_param_selected(self) -> None:
        league, season, _teams = _rr_tournament_season("Sel")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id}),
            {"season": season.id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_season_id"], season.id)

    def test_invalid_season_param_falls_back_to_displayed(self) -> None:
        league, season, _teams = _rr_tournament_season("Fall")
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": league.id}),
            {"season": 999999},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_season_id"], season.id)
