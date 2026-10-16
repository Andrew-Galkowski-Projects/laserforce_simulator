"""L-5 -- Cancel releases a stale Active play run (ADR-0040 Consequences).

A Season whose ``active_play_job_id`` names a job Celery is not running
(PENDING = unknown to the backend, or SUCCESS / FAILURE / REVOKED) used to
stay locked forever: Cancel only set the flag. Cancel now also clears the
column (filtered on that exact id) when the backend says the job is not
running. Covered at both seams: the HTML ``play_cancel`` view and the API
``play-cancel/`` action.

Only the Celery result backend is stubbed (the external boundary);
assertions are on real DB state.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Callable, Optional
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Season
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.league_api_fixtures import (
    LeagueApiTestCase,
    make_league,
    make_teams,
)

_FAST_TICKS = 20


class _FakeAsyncResult:
    """Stub for the Celery result backend; ``on_read`` runs when ``state``
    is read (simulates a concurrent write between read and release)."""

    def __init__(self, state: str, on_read: Optional[Callable[[], None]]) -> None:
        self._state = state
        self._on_read = on_read

    @property
    def state(self) -> str:
        if self._on_read is not None:
            self._on_read()
        return self._state


def _stub_backend(state: str, on_read: Optional[Callable[[], None]] = None):
    return patch(
        "matches.league_play_services.AsyncResult",
        return_value=_FakeAsyncResult(state, on_read),
    )


def _active_season(prefix: str) -> Season:
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    for i in range(2):
        team, _ = make_team_with_slots(f"{prefix}{i}")
        season.teams.add(team)
    season.start_season()
    season.refresh_from_db()
    return season


def _set_run(season: Season, job_id: str) -> None:
    Season.objects.filter(pk=season.pk).update(
        active_play_job_id=job_id, play_cancel_requested=False
    )


class TestHtmlCancelReleasesStaleRun(TestCase):
    def _cancel(self, season: Season) -> bool:
        response = self.client.post(reverse("play_cancel", args=[season.id]))
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content.decode())["cancelled"]

    def test_pending_job_is_released_and_play_week_then_succeeds(self) -> None:
        season = _active_season("PCStale")
        _set_run(season, "dead-job")
        with _stub_backend("PENDING"):
            self.assertTrue(self._cancel(season))
        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.client.post(reverse("play_week", args=[season.id]))
        self.assertTrue(GameRound.objects.filter(match__season=season).exists())

    def test_terminal_states_release_the_run(self) -> None:
        season = _active_season("PCTerm")
        for state in ("SUCCESS", "FAILURE", "REVOKED"):
            _set_run(season, "old-job")
            with _stub_backend(state):
                self.assertTrue(self._cancel(season))
            season.refresh_from_db()
            self.assertIsNone(season.active_play_job_id, state)

    def test_running_job_keeps_the_column_and_sets_the_flag(self) -> None:
        season = _active_season("PCRun")
        for state in ("STARTED", "PROGRESS", "RETRY"):
            _set_run(season, "live-job")
            with _stub_backend(state):
                self.assertTrue(self._cancel(season))
            season.refresh_from_db()
            self.assertEqual(season.active_play_job_id, "live-job", state)
            self.assertTrue(season.play_cancel_requested, state)

    def test_release_never_clears_a_different_runs_id(self) -> None:
        season = _active_season("PCRace")
        _set_run(season, "dead-job")

        def _new_run_claims() -> None:
            Season.objects.filter(pk=season.pk).update(active_play_job_id="new-job")

        with _stub_backend("PENDING", on_read=_new_run_claims):
            self._cancel(season)
        season.refresh_from_db()
        self.assertEqual(season.active_play_job_id, "new-job")

    def test_idle_season_does_not_consult_the_backend(self) -> None:
        season = _active_season("PCIdle")
        with patch("matches.league_play_services.AsyncResult") as backend:
            self.assertFalse(self._cancel(season))
        backend.assert_not_called()


def _action(pk: int, path: str) -> str:
    return f"/api/seasons/{pk}/{path}/"


class TestApiCancelReleasesStaleRun(LeagueApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        league = make_league("PcsL", manager=self.user_a)
        self.season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 6, 1)
        )
        self.season.teams.add(*make_teams("Pcs", 2))
        self.season.start_season()
        self.season.refresh_from_db()

    def _cancel(self, state: str) -> dict:
        with _stub_backend(state):
            return self.ok(self.api.post(_action(self.season.id, "play-cancel")))

    def test_pending_job_is_released_and_play_week_then_succeeds(self) -> None:
        _set_run(self.season, "dead")
        self.assertEqual(self._cancel("PENDING"), {"cancel_requested": True})
        self.season.refresh_from_db()
        self.assertIsNone(self.season.active_play_job_id)
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            self.ok(self.api.post(_action(self.season.id, "play-week")))
        self.assertTrue(GameRound.objects.filter(match__season=self.season).exists())

    def test_running_job_keeps_the_column_and_sets_the_flag(self) -> None:
        _set_run(self.season, "live")
        self.assertEqual(self._cancel("PROGRESS"), {"cancel_requested": True})
        self.season.refresh_from_db()
        self.assertEqual(self.season.active_play_job_id, "live")
        self.assertTrue(self.season.play_cancel_requested)
