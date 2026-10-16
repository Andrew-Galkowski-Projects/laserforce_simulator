"""ANG-01d-2 T1 -- the Active play run guard on the HTML play views (ADR-0040).

Seam 2 (Django test client): the async ``play_two_months`` / ``play_until_end``
/ ``play_playoffs`` endpoints enqueue through ``enqueue_play_run`` -- the run
state clears when the eager Job ends, and a second start while a run is live is
a 409 JSON ``play_in_progress``. ``play_cancel`` only sets the stop flag while a
run is live. Assertions are on status codes, JSON bodies and the Season's
run-state columns -- never on simulated point totals.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 20


def _active_season(prefix: str, n_teams: int, *, phases: tuple[str, ...] = ()):
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    for i in range(n_teams):
        team, _ = make_team_with_slots(f"{prefix}{i}")
        season.teams.add(team)
    for ordinal, phase_type in enumerate(phases, start=1):
        SeasonPhase.objects.create(
            season=season, ordinal=ordinal, phase_type=phase_type
        )
    season.start_season()
    season.refresh_from_db()
    return season


def _play_round_robin(season: Season) -> None:
    teams = {t.id: t for t in season.teams.all()}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    teams[fixture.team_a_id],
                    teams[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase,
                )


class TestAsyncPlayClearsTheRun(TestCase):
    """Regression: the job id used to be written AFTER ``.delay`` -- an eager
    task cleared the column first and left a stale finished id behind."""

    def test_play_two_months_leaves_no_stale_job_id(self) -> None:
        season = _active_season("HtmA", 3)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_two_months", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["season_id"], season.id)
        self.assertTrue(GameRound.objects.filter(match__season=season).exists())
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)

    def test_play_until_end_leaves_no_stale_job_id(self) -> None:
        season = _active_season("HtmB", 3)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_until_end", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNone(season.active_play_job_id)

    def test_play_playoffs_leaves_no_stale_job_id(self) -> None:
        season = _active_season("HtmC", 4, phases=("round_robin", "tournament"))
        _play_round_robin(season)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 202)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNone(season.active_play_job_id)


class TestAsyncPlayRefusedWhileARunIsLive(TestCase):
    def _assert_play_in_progress(self, response, job_id: str) -> None:
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "play_in_progress")
        self.assertEqual(body["job_id"], job_id)
        self.assertTrue(body["error"])

    def test_play_two_months_and_until_end_are_409(self) -> None:
        season = _active_season("HtmD", 3)
        Season.objects.filter(pk=season.pk).update(active_play_job_id="live")
        for name in ("play_two_months", "play_until_end"):
            with self.subTest(name=name):
                response = self.client.post(reverse(name, args=[season.id]))
                self._assert_play_in_progress(response, "live")
        self.assertFalse(GameRound.objects.filter(match__season=season).exists())
        season.refresh_from_db()
        self.assertEqual(season.active_play_job_id, "live")

    def test_play_playoffs_is_409(self) -> None:
        season = _active_season("HtmE", 4, phases=("round_robin", "tournament"))
        _play_round_robin(season)
        Season.objects.filter(pk=season.pk).update(active_play_job_id="live-pf")
        response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self._assert_play_in_progress(response, "live-pf")

    def test_existing_error_shapes_are_unchanged(self) -> None:
        draft = Season.objects.create(
            league=League.objects.create(name="LHtmF"),
            name="S1",
            start_date=date(2026, 6, 1),
        )
        response = self.client.post(reverse("play_two_months", args=[draft.id]))
        self.assertEqual(response.status_code, 400)
        self.assertTemplateUsed(response, "seasons/dashboard.html")
        season = _active_season("HtmG", 4, phases=("round_robin", "tournament"))
        response = self.client.post(reverse("play_playoffs", args=[season.id]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(), {"error": "No active playoff bracket to play."}
        )


class TestPlayCancelOnlyWhileLive(TestCase):
    def test_cancel_with_a_live_run_sets_the_flag(self) -> None:
        season = _active_season("HtmH", 2)
        Season.objects.filter(pk=season.pk).update(active_play_job_id="run")
        response = self.client.post(reverse("play_cancel", args=[season.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cancelled": True, "season_id": season.id})
        season.refresh_from_db()
        self.assertTrue(season.play_cancel_requested)

    def test_cancel_without_a_run_leaves_no_stale_flag(self) -> None:
        season = _active_season("HtmI", 2)
        response = self.client.post(reverse("play_cancel", args=[season.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cancelled": False, "season_id": season.id})
        season.refresh_from_db()
        self.assertFalse(season.play_cancel_requested)
