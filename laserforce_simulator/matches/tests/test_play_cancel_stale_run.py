"""L-5 -- Cancel releases a stale Active play run (ADR-0040 Consequences).

A Season whose ``active_play_job_id`` names a job Celery is not running
(PENDING = unknown to the backend, or SUCCESS / FAILURE / REVOKED) used to
stay locked forever: Cancel only set the flag. Cancel now also clears the
column (filtered on that exact id) when the backend says the job is not
running. Covered at the API ``play-cancel/`` action.

Only the Celery result backend is stubbed (the external boundary);
assertions are on real DB state.
"""

from __future__ import annotations

from datetime import date
from typing import Callable, Optional
from unittest.mock import patch

from matches.models import GameRound, Season
from matches.simulation import BatchSimulator
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


def _set_run(season: Season, job_id: str) -> None:
    Season.objects.filter(pk=season.pk).update(
        active_play_job_id=job_id, play_cancel_requested=False
    )


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

    def test_terminal_states_release_the_run(self) -> None:
        for state in ("SUCCESS", "FAILURE", "REVOKED"):
            _set_run(self.season, "old")
            self.assertEqual(self._cancel(state), {"cancel_requested": True})
            self.season.refresh_from_db()
            self.assertIsNone(self.season.active_play_job_id, state)

    def test_started_and_retry_keep_the_column_and_set_the_flag(self) -> None:
        for state in ("STARTED", "RETRY"):
            _set_run(self.season, "live")
            self.assertEqual(self._cancel(state), {"cancel_requested": True})
            self.season.refresh_from_db()
            self.assertEqual(self.season.active_play_job_id, "live", state)
            self.assertTrue(self.season.play_cancel_requested, state)

    def test_release_never_clears_a_different_runs_id(self) -> None:
        _set_run(self.season, "dead")

        def _new_run_claims() -> None:
            Season.objects.filter(pk=self.season.pk).update(active_play_job_id="new")

        with _stub_backend("PENDING", on_read=_new_run_claims):
            self.ok(self.api.post(_action(self.season.id, "play-cancel")))
        self.season.refresh_from_db()
        self.assertEqual(self.season.active_play_job_id, "new")

    def test_idle_season_does_not_consult_the_backend(self) -> None:
        with patch("matches.league_play_services.AsyncResult") as backend:
            data = self.ok(self.api.post(_action(self.season.id, "play-cancel")))
        self.assertEqual(data, {"cancel_requested": False})
        backend.assert_not_called()
