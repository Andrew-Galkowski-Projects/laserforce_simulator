"""PLAY-01 -- the cooperative cancel on async play runs.

``Season.play_cancel_requested`` is a between-fixture **cancel** flag. The
task checks it at its top AND between fixtures, stops cleanly, and RETURNS
NORMALLY (Celery SUCCESS => ``"complete"``) with a partial ``{completed,
total}`` + ``cancelled: True``. Already-played Rounds stay committed; the
Season stays ``active`` (resumable). NO ``AsyncResult.revoke``.

This file asserts:

* cancel halts ``play_season_task`` / ``play_playoffs_task`` under EAGER, NO
  mocks: returns normally with ``cancelled: True`` + partial ``completed <
  total``, committed rows survive, Season stays ``active``,
  ``active_play_job_id`` cleared. Plus the top-of-task pre-set-flag early
  return.
* the ``0056`` migration is 2x ``AddField`` / no ``RunPython``;
  ``makemigrations --check`` is clean.

ANG-08: the HTML ``play_cancel`` / ``play_status`` views and the topnav Stop
control went; the API cancel, enqueue run state and ``cancelled`` poll are
pinned in ``test_api_play_runs.py``.

Tests assert SCHEMA-LEVEL outcomes, NEVER raw simulated point totals. Small
sims under a small ``ROUND_TICKS`` patch drive the real per-fixture commits so
the cancel-halt is exercised end to end.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.test import TestCase

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


# ===========================================================================
# Cooperative cancel halts the task (NO mocks, EAGER)
# ===========================================================================


class TestPlaySeasonTaskCancel(TestCase):
    """``play_season_task`` observes ``play_cancel_requested`` at its top AND
    between fixtures, stops cleanly, RETURNS NORMALLY with ``cancelled: True``
    + a partial ``{completed, total}``, leaves committed Rounds in place, the
    Season ``active``, and ``active_play_job_id`` cleared.
    """

    def test_flag_set_before_loop_returns_completed_zero_no_new_rows(self) -> None:
        """Top-of-task case: the flag is set BEFORE the loop => returns
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
        # Season stays active (no fixtures played => no completion flip).
        season.refresh_from_db()
        self.assertEqual(season.state, "active")

    def test_cancel_mid_loop_halts_with_partial_committed_rows_survive(
        self,
    ) -> None:
        """Set the flag from inside the per-fixture loop (after the first
        Round commits) via a real seam -- a spy on
        ``simulate_scheduled_round`` that flips the DB flag AFTER its first
        call. The task observes it at the TOP of the next iteration and breaks.

        NO ``mock.patch`` REPLACING ``simulate_scheduled_round`` -- the spy
        DELEGATES to the real method so the per-fixture commit actually
        happens (the cancel-halt is exercised end to end).
        """
        from matches.tasks import play_season_task

        # N=4 => 12 fixtures total -- plenty of room for a partial < total.
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

        # ANG-01d-2 (ADR-0040): the finally clears only the task's OWN job id,
        # so run the task under the id the enqueue recorded.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_season_task.apply_async(
                kwargs={"season_id": season.id, "max_matchdays": None},
                task_id="stale-job-id",
            )

        season.refresh_from_db()
        self.assertIsNone(season.active_play_job_id)


class TestPlayPlayoffsTaskCancel(TestCase):
    """``play_playoffs_task`` mirrors the cancel contract -- flag set before the
    stage-drain loop => early ``cancelled: True`` return, ``active_play_job_id``
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

        # ANG-01d-2 (ADR-0040): run under the recorded id -- the finally clears
        # only the task's OWN job id.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            async_result = play_playoffs_task.apply_async(
                kwargs={"season_id": season.id}, task_id="pf-job"
            )

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
# Migration shape
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
