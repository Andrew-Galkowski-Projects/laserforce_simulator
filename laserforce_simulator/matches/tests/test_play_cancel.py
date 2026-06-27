"""PLAY-01 — TDD tests for live incremental stats + Stop/Cancel on async runs.

The LOCKED seam contract is ``.claude/worktrees/play-01-seam-contract.md``.
PLAY-01 layers onto the NAV-01 ``Play ▾`` topnav (ASYNC runs only —
``play_two_months`` / ``play_until_end`` / ``play_playoffs``) two things:

1. A cooperative between-fixture **cancel** flag (``Season.play_cancel_requested``)
   + a **Stop** control (``play_cancel`` view + ``#topbar-play-stop``). The task
   checks the flag at its top AND between fixtures, stops cleanly, and RETURNS
   NORMALLY (Celery SUCCESS ⇒ ``"complete"``) with a partial ``{completed,
   total}`` + ``cancelled: True``. Already-played Rounds stay committed; the
   Season stays ``active`` (resumable). NO ``AsyncResult.revoke``; NO new
   status-vocabulary string.
2. **Live incremental** standings/leaders recomputed VIEW-SIDE from committed
   rows each poll, returned in ``play_status``'s JSON as ``standings`` (HTML
   fragment) + ``leaders`` (3-key dict of HTML fragments), plus an optional
   ``cancelled`` bool.

This file asserts on the PUBLIC SURFACE (§6 test boundary):

* Item 1 — cancel halts ``play_season_task`` / ``play_playoffs_task`` under
  EAGER, NO mocks: returns normally with ``cancelled: True`` + partial
  ``completed < total``, committed rows survive, Season stays ``active``,
  ``active_play_job_id`` cleared. Plus the top-of-task pre-set-flag early return.
* Item 2 — ``play_cancel`` view: POST → 200 ``{cancelled: True, season_id}`` +
  the flag persisted; GET → 405; missing Season → 404.
* Item 3 — enqueue sets run state (``active_play_job_id`` = job id,
  ``play_cancel_requested`` cleared); the 202 ``{job_id, season_id}`` shape is
  unchanged.
* Item 3/4 — extended ``play_status`` JSON: the 5 existing keys PLUS
  ``standings`` (str), ``leaders`` (``{points, tags, ratio}`` dict),
  ``cancelled`` (bool); status vocabulary unchanged.
* Item 4 — partial stats are VIEW-SIDE from committed rows (a played team shows
  up; the fragment is non-empty once ≥1 Round is committed).
* Item 5 — the topnav renders ``#topbar-play-stop`` iff ``active_play_job_id``
  is set, absent when idle.
* Item 7 — the ``0056`` migration is 2× ``AddField`` / no ``RunPython``;
  ``makemigrations --check`` is clean.

Tests assert SCHEMA-LEVEL outcomes — return-dict keys / status codes / JSON
keys / committed-row survival / DOM ids — NEVER raw simulated point totals
(sims are non-deterministic). N=2/N=3/N=4 small seeded sims under a small
``ROUND_TICKS`` patch drive the real per-fixture commits so the cancel-halt is
exercised end to end (NO ``mock.patch`` on the task / ``simulate_scheduled_round``
/ ``play_next_bracket_round``).

PRE-CODE-LANDING NOTE: the ``Season.active_play_job_id`` /
``play_cancel_requested`` model fields + migration ``0056`` have ALREADY landed,
but the ``play_cancel`` view + URL, the ``_play_cancel_requested`` task helper +
cancel checks + ``finally`` clear, the enqueue-time run-state writes, the
extended ``play_status`` JSON, the ``active_play_job_id`` render key, and the
``#topbar-play-stop`` control have NOT — so these assertions WILL fail until the
Code agent lands them. That is the expected TDD red state, not a defect here.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Season, SeasonPhase
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _active_season(prefix: str, *, n_teams: int = 2):
    """Build an ``active`` Season with ``n_teams`` enrolled (single RR phase)."""
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(league=league, name="S1", start_date=date.today())
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _rr_tournament_season(prefix: str, *, n_teams: int = 4):
    """Active Season: ordinal-1 round_robin + ordinal-2 tournament, n teams."""
    league = League.objects.create(name=f"L{prefix}")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{prefix[:3]}{i}")
        teams.append(t)
        season.teams.add(t)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    SeasonPhase.objects.create(season=season, ordinal=2, phase_type="tournament")
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _play_rr(season, teams):
    """Play every RR fixture (auto-builds the tournament phase on completion)."""
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


def _pin_league(client, league: League) -> None:
    s = client.session
    s["last_league_id"] = league.id
    s.save()


# ===========================================================================
# Boundary item 1 — cooperative cancel halts the task (NO mocks, EAGER)
# ===========================================================================


class TestPlaySeasonTaskCancel(TestCase):
    """``play_season_task`` observes ``play_cancel_requested`` at its top AND
    between fixtures, stops cleanly, RETURNS NORMALLY with ``cancelled: True``
    + a partial ``{completed, total}``, leaves committed Rounds in place, the
    Season ``active``, and ``active_play_job_id`` cleared.
    """

    def test_flag_set_before_loop_returns_completed_zero_no_new_rows(self) -> None:
        """Top-of-task case: the flag is set BEFORE the loop ⇒ returns
        ``{"completed": 0, ...}`` with zero new rows and ``cancelled: True``.
        """
        from matches.tasks import play_season_task

        season, _teams = _active_season("CancelTop", n_teams=3)
        season.play_cancel_requested = True
        season.save(update_fields=["play_cancel_requested"])

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            async_result = play_season_task.delay(season.id, max_matchdays=None)

        self.assertEqual(async_result.state, "SUCCESS")
        payload = async_result.result
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["completed"], 0)
        self.assertTrue(payload.get("cancelled"))
        # Zero Rounds played.
        self.assertEqual(GameRound.objects.filter(match__season=season).count(), 0)
        # Season stays active (no fixtures played ⇒ no completion flip).
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_cancel_mid_loop_halts_with_partial_committed_rows_survive(
        self,
    ) -> None:
        """Set the flag from inside the per-fixture loop (after the first
        Round commits) via a real seam — a spy on
        ``simulate_scheduled_round`` that flips the DB flag AFTER its first
        call. The task observes it at the TOP of the next iteration and breaks.

        NO ``mock.patch`` REPLACING ``simulate_scheduled_round`` — the spy
        DELEGATES to the real method so the per-fixture commit actually
        happens (the cancel-halt is exercised end to end).
        """
        from matches.tasks import play_season_task

        # N=4 ⇒ 12 fixtures total — plenty of room for a partial < total.
        season, _teams = _active_season("CancelMid", n_teams=4)
        original = BatchSimulator.simulate_scheduled_round
        state = {"calls": 0}

        def _spy_then_flag(self_, season_, ta, tb, rnd, **kw):
            result = original(self_, season_, ta, tb, rnd, **kw)
            state["calls"] += 1
            if state["calls"] == 1:
                # Flip the cooperative cancel flag after the first commit.
                Season.objects.filter(id=season_.id).update(play_cancel_requested=True)
            return result

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(
                BatchSimulator, "simulate_scheduled_round", _spy_then_flag
            ):
                async_result = play_season_task.delay(season.id, max_matchdays=None)

        # Returned NORMALLY (Celery SUCCESS), no exception.
        self.assertEqual(async_result.state, "SUCCESS")
        payload = async_result.result
        self.assertTrue(payload.get("cancelled"))
        # Partial: at least the first fixture committed, but NOT all of them.
        committed = GameRound.objects.filter(match__season=season).count()
        self.assertGreaterEqual(committed, 1)
        self.assertLess(payload["completed"], payload["total"])
        # The already-played Round(s) survive (committed, not rolled back).
        self.assertGreaterEqual(committed, 1)
        # Season stays active and is resumable.
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_finally_clears_active_play_job_id(self) -> None:
        """A cancel-return still runs the ``finally`` clear of
        ``active_play_job_id``.
        """
        from matches.tasks import play_season_task

        season, _teams = _active_season("CancelClear", n_teams=3)
        # Pretend an enqueue had recorded a job id.
        season.active_play_job_id = "stale-job-id"
        season.play_cancel_requested = True
        season.save(update_fields=["active_play_job_id", "play_cancel_requested"])

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.delay(season.id, max_matchdays=None)

        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)


class TestPlayPlayoffsTaskCancel(TestCase):
    """``play_playoffs_task`` mirrors the cancel contract — flag set before the
    stage-drain loop ⇒ early ``cancelled: True`` return, ``active_play_job_id``
    cleared, the bracket stays partially undrained.
    """

    def test_flag_set_before_drain_returns_cancelled(self) -> None:
        from matches.tasks import play_playoffs_task

        season, teams = _rr_tournament_season("PlayoffCancel", n_teams=4)
        _play_rr(season, teams)
        season.refresh_from_db()
        # The tournament phase is now built + active (current_phase).
        season.play_cancel_requested = True
        season.active_play_job_id = "pf-job"
        season.save(update_fields=["play_cancel_requested", "active_play_job_id"])

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            async_result = play_playoffs_task.delay(season.id)

        self.assertEqual(async_result.state, "SUCCESS")
        payload = async_result.result
        self.assertIsInstance(payload, dict)
        self.assertTrue(payload.get("cancelled"))
        # Season is NOT completed (the bracket was not drained to a champion).
        season.refresh_from_db()
        self.assertNotEqual(season.state, "completed")
        # active_play_job_id cleared in the finally.
        self.assertIsNone(season.active_play_job_id)


# ===========================================================================
# Boundary item 2 — the play_cancel view
# ===========================================================================


class TestPlayCancelView(TestCase):
    """``play_cancel`` — POST → 200 ``{cancelled: True, season_id}`` AND the
    flag persisted; GET → 405; missing Season → 404.
    """

    def test_post_returns_200_cancelled_true_and_persists_flag(self) -> None:
        season, _teams = _active_season("PCView", n_teams=2)
        response = self.client.post(reverse("play_cancel", args=[season.id]))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        self.assertTrue(payload["cancelled"])
        self.assertEqual(payload["season_id"], season.id)
        season.refresh_from_db()
        self.assertTrue(season.play_cancel_requested)

    def test_get_returns_405(self) -> None:
        season, _teams = _active_season("PCGet", n_teams=2)
        response = self.client.get(reverse("play_cancel", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_post_on_missing_season_id_returns_404(self) -> None:
        response = self.client.post(reverse("play_cancel", args=[9_999_999]))
        self.assertEqual(response.status_code, 404)

    def test_post_on_idle_season_is_harmless(self) -> None:
        """Setting the flag on an idle Season is harmless (no active-run
        guard) — still 200 + the flag set."""
        season, _teams = _active_season("PCIdle", n_teams=2)
        response = self.client.post(reverse("play_cancel", args=[season.id]))
        self.assertEqual(response.status_code, 200)
        season.refresh_from_db()
        self.assertTrue(season.play_cancel_requested)


# ===========================================================================
# Boundary item 3 — enqueue sets run state (active_play_job_id + clear cancel)
# ===========================================================================


class TestEnqueueSetsRunState(TestCase):
    """A ``play_two_months`` / ``play_until_end`` / ``play_playoffs`` POST sets
    ``Season.active_play_job_id`` to the returned ``job_id`` and clears
    ``play_cancel_requested``; the 202 ``{job_id, season_id}`` shape is
    unchanged.
    """

    def test_play_two_months_records_job_id_and_clears_cancel(self) -> None:
        season, _teams = _active_season("EnqP2M", n_teams=2)
        # Pre-set the cancel flag so we can prove the enqueue clears it.
        season.play_cancel_requested = True
        season.save(update_fields=["play_cancel_requested"])

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            response = self.client.post(reverse("play_two_months", args=[season.id]))

        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.content.decode())
        self.assertIn("job_id", payload)
        self.assertEqual(payload["season_id"], season.id)
        season.refresh_from_db()
        # Cancel flag cleared at enqueue.
        self.assertFalse(season.play_cancel_requested)
        # active_play_job_id recorded as the returned job id. (Under EAGER the
        # task's finally clears it again, so assert at minimum it is NOT the
        # pre-enqueue None-with-stale-flag state by checking the cleared flag;
        # the job-id record is pinned in the dedicated test below with a
        # patched .delay to avoid the EAGER finally clearing it.)

    def test_play_two_months_active_play_job_id_set_at_enqueue(self) -> None:
        """Pin ``active_play_job_id == job_id`` at enqueue time by patching
        ``play_season_task.delay`` to return a fake result WITHOUT running the
        task body (so the ``finally`` clear never fires)."""
        season, _teams = _active_season("EnqJobId", n_teams=2)

        class _FakeResult:
            id = "fake-job-id-123"

        with patch(
            "matches.league_views.play_season_task.delay", return_value=_FakeResult()
        ):
            response = self.client.post(reverse("play_two_months", args=[season.id]))

        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["job_id"], "fake-job-id-123")
        season.refresh_from_db()
        self.assertEqual(season.active_play_job_id, "fake-job-id-123")
        self.assertFalse(season.play_cancel_requested)

    def test_play_until_end_records_job_id(self) -> None:
        season, _teams = _active_season("EnqPUE", n_teams=2)

        class _FakeResult:
            id = "pue-job"

        with patch(
            "matches.league_views.play_season_task.delay", return_value=_FakeResult()
        ):
            response = self.client.post(reverse("play_until_end", args=[season.id]))

        self.assertEqual(response.status_code, 202)
        season.refresh_from_db()
        self.assertEqual(season.active_play_job_id, "pue-job")

    def test_play_playoffs_records_job_id(self) -> None:
        season, teams = _rr_tournament_season("EnqPF", n_teams=4)
        _play_rr(season, teams)
        season.refresh_from_db()

        class _FakeResult:
            id = "pf-enqueue-job"

        with patch(
            "matches.league_views.play_playoffs_task.delay", return_value=_FakeResult()
        ):
            response = self.client.post(reverse("play_playoffs", args=[season.id]))

        self.assertEqual(response.status_code, 202)
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["job_id"], "pf-enqueue-job")
        self.assertEqual(payload["season_id"], season.id)
        season.refresh_from_db()
        self.assertEqual(season.active_play_job_id, "pf-enqueue-job")


# ===========================================================================
# Boundary item 3/4 — extended play_status JSON
# ===========================================================================


class TestPlayStatusExtendedJson(TestCase):
    """``play_status`` JSON carries the existing 5 keys PLUS ``standings``
    (HTML str), ``leaders`` (``{points, tags, ratio}`` dict), ``cancelled``
    (bool). Status vocabulary unchanged (``running``/``complete``/``error``).
    """

    def _make_async_result(self, state: str, info=None, result_payload=None):
        class _Fake:
            def __init__(self):
                self.state = state
                self.info = info
                self.result = result_payload

        return _Fake()

    def test_all_existing_five_keys_still_present(self) -> None:
        season, _teams = _active_season("PSExist", n_teams=2)
        fake = self._make_async_result("PROGRESS", info={"completed": 1, "total": 2})
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content.decode())
        for key in ("status", "completed", "total", "error", "season_id"):
            self.assertIn(key, payload)
        # Vocabulary unchanged.
        self.assertEqual(payload["status"], "running")

    def test_new_keys_standings_leaders_cancelled_present(self) -> None:
        season, _teams = _active_season("PSNew", n_teams=2)
        fake = self._make_async_result("PROGRESS", info={"completed": 1, "total": 2})
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        self.assertIn("standings", payload)
        self.assertIn("leaders", payload)
        self.assertIn("cancelled", payload)

    def test_standings_is_string_and_leaders_is_three_key_dict(self) -> None:
        season, _teams = _active_season("PSTypes", n_teams=2)
        fake = self._make_async_result("PROGRESS", info={"completed": 1, "total": 2})
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        self.assertIsInstance(payload["standings"], str)
        self.assertIsInstance(payload["leaders"], dict)
        self.assertEqual(set(payload["leaders"].keys()), {"points", "tags", "ratio"})
        for fragment in payload["leaders"].values():
            self.assertIsInstance(fragment, str)

    def test_cancelled_true_only_when_task_returned_cancelled(self) -> None:
        season, _teams = _active_season("PSCancelTrue", n_teams=2)
        fake = self._make_async_result(
            "SUCCESS",
            result_payload={"completed": 1, "total": 2, "cancelled": True},
        )
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        # NO new status string — still "complete".
        self.assertEqual(payload["status"], "complete")
        self.assertTrue(payload["cancelled"])

    def test_cancelled_false_on_a_normal_success(self) -> None:
        season, _teams = _active_season("PSCancelFalse", n_teams=2)
        fake = self._make_async_result(
            "SUCCESS",
            result_payload={"completed": 2, "total": 2},
        )
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        self.assertEqual(payload["status"], "complete")
        self.assertFalse(payload["cancelled"])

    def test_standings_leaders_present_on_complete_poll_too(self) -> None:
        """The fragments are computed on EVERY poll (running OR complete) so
        the final poll patches the finished tables too."""
        season, _teams = _active_season("PSCompleteFrag", n_teams=2)
        fake = self._make_async_result(
            "SUCCESS", result_payload={"completed": 2, "total": 2}
        )
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        self.assertIn("standings", payload)
        self.assertIsInstance(payload["leaders"], dict)


# ===========================================================================
# Boundary item 4 — partial stats are VIEW-SIDE from committed rows
# ===========================================================================


class TestPlayStatusPartialStatsViewSide(TestCase):
    """With M of N fixtures committed, ``play_status``'s ``standings`` /
    ``leaders`` reflect the committed rows — derived view-side via
    ``compute_standings`` / ``compute_leaders``, NOT read from Celery task
    meta.
    """

    def _make_async_result(self, state: str, info=None, result_payload=None):
        class _Fake:
            def __init__(self):
                self.state = state
                self.info = info
                self.result = result_payload

        return _Fake()

    def test_committed_team_appears_in_standings_fragment(self) -> None:
        # N=4: play exactly the first matchday so SOME (not all) Matches are
        # committed, then poll a PROGRESS state and assert a played team's name
        # surfaces in the server-rendered standings fragment.
        season, teams = _active_season("PartialStand", n_teams=4)
        by_id = {t.id: t for t in teams}
        sim = BatchSimulator()
        played_team_names = set()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for phase, fixtures in season.scheduled_fixtures_by_phase():
                # Play just the first matchday's fixtures.
                first_md = min(f.matchday for f in fixtures)
                for fixture in fixtures:
                    if fixture.matchday != first_md:
                        continue
                    ta = by_id[fixture.team_a_id]
                    tb = by_id[fixture.team_b_id]
                    sim.simulate_scheduled_round(
                        season,
                        ta,
                        tb,
                        fixture.round_number,
                        season_phase=phase if phase.pk is not None else None,
                    )
                    played_team_names.add(ta.name)
                    played_team_names.add(tb.name)
                break

        # Some Matches committed but NOT the full schedule.
        self.assertGreaterEqual(
            GameRound.objects.filter(match__season=season).count(), 1
        )

        fake = self._make_async_result("PROGRESS", info={"completed": 2, "total": 12})
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        standings_html = payload["standings"]
        # At least one played team's name surfaces in the live fragment
        # (recomputed view-side from the committed Matches).
        self.assertTrue(
            any(name in standings_html for name in played_team_names),
            "no played team name in the live standings fragment — partial "
            "stats are not being recomputed view-side from committed rows",
        )

    def test_standings_fragment_non_empty_once_a_round_committed(self) -> None:
        season, teams = _active_season("PartialNonEmpty", n_teams=2)
        by_id = {t.id: t for t in teams}
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for phase, fixtures in season.scheduled_fixtures_by_phase():
                fixture = fixtures[0]
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )
                break

        fake = self._make_async_result("PROGRESS", info={"completed": 1, "total": 2})
        with patch("matches.league_views.AsyncResult", return_value=fake):
            response = self.client.get(
                reverse(
                    "play_status",
                    kwargs={"season_id": season.id, "job_id": "abc"},
                )
            )
        payload = json.loads(response.content.decode())
        self.assertTrue(payload["standings"].strip())


# ===========================================================================
# Boundary item 5 — the topnav renders #topbar-play-stop iff active run
# ===========================================================================


class TestTopnavPlayStopControl(TestCase):
    """The league-branch ``Play ▾`` topnav renders ``#topbar-play-stop`` (a
    POST form to ``play_cancel``) when ``active_play_job_id`` is set, and NOT
    when the Season is idle.
    """

    def test_stop_control_rendered_when_active_play_job_id_set(self) -> None:
        season, _teams = _active_season("StopOn", n_teams=2)
        season.active_play_job_id = "running-job"
        season.save(update_fields=["active_play_job_id"])
        _pin_league(self.client, season.league)
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertIn('id="topbar-play-stop"', body)
        self.assertIn(reverse("play_cancel", args=[season.id]), body)

    def test_stop_control_absent_when_idle(self) -> None:
        season, _teams = _active_season("StopOff", n_teams=2)
        # active_play_job_id stays None (idle).
        _pin_league(self.client, season.league)
        body = self.client.get(
            reverse("league_dashboard", args=[season.league_id])
        ).content.decode()
        self.assertNotIn('id="topbar-play-stop"', body)


# ===========================================================================
# Boundary item 7 — migration shape
# ===========================================================================


class TestPlay01MigrationShape(TestCase):
    """The PLAY-01 ``0056`` migration adds exactly the two Season fields with
    no ``RunPython`` and ``makemigrations --check`` is clean (no model drift).
    """

    def test_makemigrations_check_is_clean(self) -> None:
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        try:
            call_command(
                "makemigrations",
                "--check",
                "--dry-run",
                stdout=out,
                stderr=out,
            )
        except SystemExit as exc:  # --check exits non-zero on pending changes
            self.fail(
                "makemigrations --check reported pending model changes:\n"
                + out.getvalue()
                + f"\n(SystemExit code {exc.code})"
            )

    def test_play01_migration_is_two_addfields_no_runpython(self) -> None:
        # The contract pins 2x AddField (active_play_job_id +
        # play_cancel_requested) with NO RunPython, dep on
        # 0055_gameround_fidelity_roster_snapshot. The landed filename is
        # digit-leading (0056_season_active_play_job_id_and_more), so locate
        # the migration via the MigrationLoader graph keyed on its operations,
        # NOT by import (a digit-leading module name is not importable).
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.operations import (
            AddField,
            RunPython,
            RunSQL,
        )

        loader = MigrationLoader(connection=None, ignore_no_migrations=True)
        matches_migrations = {
            name: migration
            for (app_label, name), migration in loader.disk_migrations.items()
            if app_label == "matches"
        }
        # The PLAY-01 migration is whichever matches migration adds the two
        # new Season fields.
        candidates = []
        for name, migration in matches_migrations.items():
            add_fields = [
                op
                for op in migration.operations
                if isinstance(op, AddField) and op.model_name.lower() == "season"
            ]
            names = {op.name for op in add_fields}
            if names == {"active_play_job_id", "play_cancel_requested"}:
                candidates.append((name, migration))

        self.assertEqual(
            len(candidates),
            1,
            "expected exactly one matches migration adding both "
            "active_play_job_id + play_cancel_requested to Season; "
            f"found {[n for n, _ in candidates]}",
        )
        _name, migration = candidates[0]
        # Exactly the two AddFields, nothing else.
        self.assertEqual(len(migration.operations), 2)
        self.assertTrue(all(isinstance(op, AddField) for op in migration.operations))
        self.assertFalse(
            any(isinstance(op, (RunPython, RunSQL)) for op in migration.operations),
            "PLAY-01 migration must not carry RunPython / RunSQL (no backfill)",
        )
        self.assertIn(
            ("matches", "0055_gameround_fidelity_roster_snapshot"),
            list(migration.dependencies),
        )
